"""Rapport de qualité (clean/report.py) : calculs, rendu markdown, archivage."""

import json
from datetime import UTC, date, datetime

from gazette.clean.report import (
    GREEDY_MIN_ARTICLES,
    TOP_N,
    build_report,
    render_markdown,
    summary_line,
    write_report,
)
from gazette.clean.rules import RULE_IDS, Rejected, RulesConfig, RulesResult

DAY = date(2026, 11, 12)
AT = datetime(2026, 11, 12, 11, 30, tzinfo=UTC)


def article(source, n=0, titre=None):
    return {"id": f"{source}-{n}", "source": source, "titre": titre or f"Titre {source} {n}"}


def rejected(source, rule, n=0, titre=None):
    return Rejected(article(source, n, titre), rule, "raison de test")


def sample_result():
    kept = [article("Le Monde", n) for n in range(8)] + [article("Sud Ouest", n) for n in range(4)]
    rejects = (
        [rejected("L'Obs", "R05_trop_ancien", n) for n in range(6)]
        + [rejected("L'Obs", "R01_quiz_jeux", n + 10, "Quiz. Test") for n in range(2)]
        + [rejected("Le Monde", "R03_direct_replay", 20, "EN DIRECT. Foot")]
        + [rejected("Sud Ouest", "R05_trop_ancien", 21)]
    )
    return RulesResult(kept=kept, rejected=rejects)


def test_totals_and_rates():
    report = build_report(DAY, sample_result(), generated_at=AT)
    assert (report["entres"], report["gardes"], report["rejetes"]) == (22, 12, 10)
    assert report["taux_rejet"] == round(10 / 22, 4)
    assert report["jour"] == "2026-11-12"
    assert report["genere_le"] == "2026-11-12T11:30:00+00:00"


def test_every_rule_is_listed_even_without_rejection():
    report = build_report(DAY, sample_result(), generated_at=AT)
    assert list(report["par_regle"]) == list(RULE_IDS)
    assert report["par_regle"]["R05_trop_ancien"] == {
        "rejetes": 7,
        "part_des_entres": round(7 / 22, 4),
    }
    assert report["par_regle"]["R07_horoscope"] == {"rejetes": 0, "part_des_entres": 0.0}


def test_per_source_counts_and_sorting_by_rejection_rate():
    by_source = build_report(DAY, sample_result(), generated_at=AT)["par_source"]
    assert list(by_source) == ["L'Obs", "Sud Ouest", "Le Monde"]  # taux de rejet décroissant
    assert by_source["L'Obs"] == {
        "entres": 8,
        "gardes": 0,
        "rejetes": 8,
        "taux_rejet": 1.0,
        "par_regle": {"R01_quiz_jeux": 2, "R05_trop_ancien": 6},
    }
    assert by_source["Le Monde"]["entres"] == 9
    assert by_source["Le Monde"]["par_regle"] == {"R03_direct_replay": 1}


def test_top_rejects_are_sorted_with_an_example():
    top = build_report(DAY, sample_result(), generated_at=AT)["top_rejets"]
    assert [(t["regle"], t["source"], t["nombre"]) for t in top] == [
        ("R05_trop_ancien", "L'Obs", 6),
        ("R01_quiz_jeux", "L'Obs", 2),
        ("R03_direct_replay", "Le Monde", 1),
        ("R05_trop_ancien", "Sud Ouest", 1),
    ]
    assert top[1]["exemple"] == "Quiz. Test"


def test_top_rejects_are_capped():
    rejects = [rejected(f"Source {i}", "R01_quiz_jeux", i) for i in range(TOP_N + 4)]
    top = build_report(DAY, RulesResult(kept=[], rejected=rejects), generated_at=AT)["top_rejets"]
    assert len(top) == TOP_N


def test_greedy_rule_is_flagged_only_on_big_enough_sources():
    watch = build_report(DAY, sample_result(), generated_at=AT)["regles_a_surveiller"]
    # L'Obs : 6 articles sur 8 rejetés par R05 (75 %) ; la source a au moins 5 articles.
    assert [(w["regle"], w["source"], w["nombre"], w["sur"]) for w in watch] == [
        ("R05_trop_ancien", "L'Obs", 6, 8)
    ]
    # Sud Ouest : 1 article sur 5 : sous le seuil de part. Le Monde : 1 sur 9 : idem.


def test_a_tiny_source_is_never_flagged():
    rejects = [
        rejected("Petite source", "R01_quiz_jeux", n) for n in range(GREEDY_MIN_ARTICLES - 1)
    ]
    report = build_report(DAY, RulesResult(kept=[], rejected=rejects), generated_at=AT)
    assert report["regles_a_surveiller"] == []


def test_empty_day_has_no_division_by_zero():
    report = build_report(DAY, RulesResult(kept=[], rejected=[]), generated_at=AT)
    assert (report["entres"], report["gardes"], report["rejetes"], report["taux_rejet"]) == (
        0,
        0,
        0,
        0.0,
    )
    assert report["par_source"] == {} and report["top_rejets"] == []
    assert "Aucun rejet." in render_markdown(report)


def test_thresholds_used_are_recorded():
    report = build_report(
        DAY, sample_result(), config=RulesConfig(max_age_hours=24), generated_at=AT
    )
    assert report["seuils"]["age_max_heures"] == 24
    assert report["seuils"]["similarite_doublon"] == 0.7


def test_report_is_json_serializable():
    report = build_report(DAY, sample_result(), generated_at=AT)
    assert json.loads(json.dumps(report)) == report


def test_summary_line_is_key_value():
    report = build_report(DAY, sample_result(), generated_at=AT)
    assert (
        summary_line(report)
        == "nettoyage jour=2026-11-12 entres=22 gardes=12 rejetes=10 taux_rejet=45.5%"
    )


def test_markdown_lists_rules_sources_top_and_watchlist():
    text = render_markdown(build_report(DAY, sample_result(), generated_at=AT))
    assert text.startswith("# Rapport de qualité du 2026-11-12")
    assert "**22** articles entrés, **12** gardés, **10** rejetés (45 %)" in text
    for rule in RULE_IDS:
        assert f"| `{rule}` |" in text
    assert "| L'Obs | 8 | 0 | 8 | 100 % |" in text
    assert "| 1 | `R05_trop_ancien` | L'Obs | 6 |" in text
    assert "`R05_trop_ancien` écarte 75 % des articles de la source L'Obs (6 sur 8)" in text
    assert "similarité des doublons 0,7" in text
    assert text.endswith("\n")


def test_markdown_escapes_pipes_in_titles():
    rejects = [rejected("S", "R03_direct_replay", 1, "EN DIRECT | Suivez le grand oral")]
    text = render_markdown(
        build_report(DAY, RulesResult(kept=[], rejected=rejects), generated_at=AT)
    )
    assert "EN DIRECT \\| Suivez le grand oral" in text


def test_markdown_cuts_long_examples():
    long_title = "Un titre " + "très long " * 30
    rejects = [rejected("S", "R01_quiz_jeux", 1, long_title)]
    text = render_markdown(
        build_report(DAY, RulesResult(kept=[], rejected=rejects), generated_at=AT)
    )
    row = next(line for line in text.splitlines() if line.startswith("| 1 |"))
    example_cell = row.split("|")[5].strip()
    assert len(example_cell) <= 90 and example_cell.endswith("\u2026")


def test_write_report_archives_markdown_and_json(tmp_path):
    report = build_report(DAY, sample_result(), generated_at=AT)
    markdown, data = write_report(tmp_path / "rapports", report)
    assert markdown.name == "qualite-2026-11-12.md"
    assert data.name == "qualite-2026-11-12.json"
    assert markdown.read_text(encoding="utf-8") == render_markdown(report)
    assert json.loads(data.read_text(encoding="utf-8")) == report
    assert sorted(p.name for p in markdown.parent.iterdir()) == sorted(
        [markdown.name, data.name]
    )  # pas de fichier .tmp oublié


def test_write_report_overwrites_the_day_report(tmp_path):
    write_report(tmp_path, build_report(DAY, sample_result(), generated_at=AT))
    smaller = build_report(DAY, RulesResult(kept=[article("A")], rejected=[]), generated_at=AT)
    markdown, data = write_report(tmp_path, smaller)
    assert json.loads(data.read_text(encoding="utf-8"))["entres"] == 1
    assert "**1** articles entrés" in markdown.read_text(encoding="utf-8")
