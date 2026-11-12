"""Pipeline de nettoyage (clean/pipeline.py) : du brut sur disque aux tables et au rapport.

Les tables sont simulées en mémoire ; la version PostgreSQL est dans tests/test_db_clean.py.
"""

import json
from datetime import UTC, date, datetime

import pytest
from clean_helpers import load_sample, write_raw_day

from gazette.clean.pipeline import read_raw_items, run_cleaning
from gazette.clean.rules import RulesConfig
from gazette.cli import main
from gazette.config import ConfigError
from gazette.db.clean_repository import InMemoryCleanStore

DAY = date(2026, 11, 12)
NOW = datetime(2026, 11, 12, 13, 0, tzinfo=UTC)
NBSP = "\u00a0"


@pytest.fixture
def sample():
    return load_sample()


@pytest.fixture
def raw_day(settings, sample):
    write_raw_day(settings, sample["items"], DAY)
    return sample


def outcome(store, day=DAY):
    """Verdict de chaque article par préfixe d'id : None (propre) ou l'identifiant de la règle."""
    clean, rejects = store.load_day(day)
    return {r["id"][:8]: None for r in clean} | {r["id"][:8]: r["regle"] for r in rejects}


# -- Lecture du brut ---------------------------------------------------------------------------
def test_read_raw_items_reads_every_file_and_skips_the_manifest(settings, raw_day):
    items = read_raw_items(settings.raw_dir, DAY)
    assert len(items) == 29
    files = {i["_fichier"] for i in items}
    assert files == {
        "2026-11-12/pqn/l-obs.json",
        "2026-11-12/pqn/le-figaro.json",
        "2026-11-12/pqn/le-monde.json",
        "2026-11-12/pqn/liberation.json",
        "2026-11-12/pqr/la-depeche-du-midi.json",
        "2026-11-12/pqr/le-dauphine-libere.json",
        "2026-11-12/pqr/sud-ouest.json",
    }


def test_read_raw_items_points_to_the_file_each_article_comes_from(settings, raw_day):
    for item in read_raw_items(settings.raw_dir, DAY):
        document = json.loads((settings.raw_dir / item["_fichier"]).read_text(encoding="utf-8"))
        assert item["id"] in {i["id"] for i in document["items"]}


def test_read_raw_items_of_a_day_without_collection_is_empty(settings, raw_day):
    assert read_raw_items(settings.raw_dir, date(2026, 1, 1)) == []


# -- Nettoyage de bout en bout -----------------------------------------------------------------
def test_run_cleaning_gives_the_expected_verdicts(settings, raw_day):
    store = InMemoryCleanStore()
    report = run_cleaning(settings, DAY, store=store, now=NOW)
    assert outcome(store) == raw_day["expected"]
    assert (report["entres"], report["gardes"], report["rejetes"]) == (29, 16, 13)
    assert report["par_regle"]["R01_quiz_jeux"]["rejetes"] == 3
    assert report["par_regle"]["R06_doublon_approx"]["rejetes"] == 1


def test_stored_articles_are_normalized(settings, raw_day):
    store = InMemoryCleanStore()
    run_cleaning(settings, DAY, store=store, now=NOW)
    clean, rejects = store.load_day(DAY)
    assert clean and rejects
    for row in clean + rejects:
        assert NBSP not in row["titre"]
        assert row["recupere_le"].utcoffset().total_seconds() == 0  # UTC
    assert all(len(row["resume"]) <= 300 for row in clean)


def test_every_stored_article_can_be_traced_back_to_its_raw_item(settings, raw_day):
    store = InMemoryCleanStore()
    run_cleaning(settings, DAY, store=store, now=NOW)
    clean, rejects = store.load_day(DAY)
    for row in clean + rejects:
        document = json.loads((settings.raw_dir / row["fichier_brut"]).read_text(encoding="utf-8"))
        raw = next(i for i in document["items"] if i["id"] == row["id"])
        assert raw["lien"] == row["lien"] and raw["source"] == row["source"]


def test_rejections_keep_the_rule_and_the_reason(settings, raw_day):
    store = InMemoryCleanStore()
    run_cleaning(settings, DAY, store=store, now=NOW)
    _, rejects = store.load_day(DAY)
    quiz = next(r for r in rejects if r["regle"] == "R01_quiz_jeux")
    assert quiz["raison"] and quiz["fichier_brut"].startswith("2026-11-12/")


# -- Rapport -----------------------------------------------------------------------------------
def test_report_files_are_archived_under_the_data_directory(settings, raw_day):
    report = run_cleaning(settings, DAY, store=InMemoryCleanStore(), now=NOW)
    markdown = settings.reports_dir / "qualite-2026-11-12.md"
    data = settings.reports_dir / "qualite-2026-11-12.json"
    assert markdown.read_text(encoding="utf-8").startswith("# Rapport de qualité du 2026-11-12")
    assert json.loads(data.read_text(encoding="utf-8")) == report


def test_report_files_can_be_skipped(settings, raw_day):
    run_cleaning(settings, DAY, store=InMemoryCleanStore(), now=NOW, write_files=False)
    assert not settings.reports_dir.exists()


# -- Rejeu et changement de verdict ----------------------------------------------------------
def test_running_the_same_day_twice_changes_nothing(settings, raw_day):
    store = InMemoryCleanStore()
    first = run_cleaning(settings, DAY, store=store, now=NOW)
    rows = store.load_day(DAY)
    second = run_cleaning(settings, DAY, store=store, now=NOW)
    assert second == first
    assert store.load_day(DAY) == rows


def test_a_stricter_threshold_moves_articles_from_clean_to_rejected(settings, raw_day):
    store = InMemoryCleanStore()
    run_cleaning(settings, DAY, store=store, now=NOW)
    before = outcome(store)
    run_cleaning(settings, DAY, store=store, now=NOW, config=RulesConfig(max_age_hours=1))
    after = outcome(store)

    assert set(after) == set(before)  # même articles, jamais en double
    assert sum(v is None for v in after.values()) < sum(v is None for v in before.values())
    # Les règles qui jugent un article seul rejettent au moins autant avec un seuil plus strict.
    # (R06 compare plusieurs articles : son verdict peut changer si le « jumeau » est écarté.)
    alone = {k for k, v in before.items() if v and v != "R06_doublon_approx"}
    assert alone <= {k for k, v in after.items() if v}
    clean, rejects = store.load_day(DAY)
    assert not {r["id"] for r in clean} & {r["id"] for r in rejects}  # jamais dans les deux

    run_cleaning(settings, DAY, store=store, now=NOW)  # retour au seuil d'origine
    assert outcome(store) == before


def test_a_day_without_raw_articles_gives_an_empty_report(settings):
    store = InMemoryCleanStore()
    report = run_cleaning(settings, date(2026, 1, 1), store=store, now=NOW)
    assert (report["entres"], report["taux_rejet"]) == (0, 0.0)
    assert store.load_day(date(2026, 1, 1)) == ([], [])
    assert (settings.reports_dir / "qualite-2026-01-01.md").exists()


def test_without_a_database_url_the_default_store_is_refused(settings, raw_day):
    with pytest.raises(ConfigError, match="GAZETTE_DATABASE_URL"):
        run_cleaning(settings, DAY, now=NOW)


# -- Ligne de commande -------------------------------------------------------------------------
def test_clean_command_prints_a_summary(settings, raw_day, monkeypatch, capsys):
    monkeypatch.setenv("GAZETTE_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("GAZETTE_CONTACT_EMAIL", "test@example.org")
    monkeypatch.setattr(
        "gazette.clean.pipeline.default_clean_store", lambda settings: InMemoryCleanStore()
    )
    assert main(["clean", "--date", "2026-11-12"]) == 0
    out = capsys.readouterr().out
    assert "Nettoyage du 2026-11-12" in out
    assert "29 entrés" in out
    assert "R01_quiz_jeux" in out
    assert "qualite-2026-11-12.md" in out
