"""Après le nettoyage (clean/quality.py) : contrôles sur ce que `run_cleaning` a écrit,
rapport complété, métriques, alerte une fois par jour, purge. Sans base : stores en mémoire.

Les données sont les 29 vrais articles du 2026-11-12 (tests/fixtures/rules_sample.json).
"""

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from clean_helpers import load_sample, write_raw_day

from gazette.clean import quality
from gazette.clean.checks import ChecksConfig
from gazette.clean.pipeline import run_cleaning
from gazette.db.clean_repository import InMemoryCleanStore
from gazette.db.quality_repository import InMemoryChecksHistory

DAY = date(2026, 11, 12)
CLEANED = datetime(2026, 11, 12, 13, 0, tzinfo=UTC)
PARIS = ZoneInfo("Europe/Paris")
AFTERNOON = datetime(2026, 11, 12, 14, 0, tzinfo=PARIS)


@pytest.fixture
def cleaned(settings):
    """Le nettoyage de la binôme a tourné sur le jour : tables (en mémoire) et rapport."""
    write_raw_day(settings, load_sample()["items"], DAY)
    store = InMemoryCleanStore()
    report = run_cleaning(settings, DAY, store=store, now=CLEANED)
    return store, report


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(quality, "send_alert", lambda url, msg: sent.append(msg) or True)
    return sent


def check(settings, store, history=None, **kw):
    return quality.run_quality_checks(
        settings, DAY, store=store, history=history or InMemoryChecksHistory(), **kw
    )


def test_checks_read_what_the_cleaning_wrote(settings, cleaned, alerts):
    store, report = cleaned
    history = InMemoryChecksHistory()
    summary = check(settings, store, history, checked_at=AFTERNOON)
    assert len(summary["resultats"]) == 11
    # 29 articles réels, dont 13 gardés : le volume du jour (30) n'est pas atteint.
    assert report["gardes"] < ChecksConfig().articles_min
    assert summary["critiques_en_echec"] == ["C04_volume"]
    # Les faits de forme sont vrais : la normalisation a fait son travail.
    ok = {r["id"] for r in summary["resultats"] if r["ok"]}
    assert {"C01_dates_futures", "C02_titres_vides", "C07_html_residuel", "C08_encodage"} <= ok
    assert len(history.rows) == 11


def test_volume_not_judged_before_six(settings, cleaned, alerts):
    store, _ = cleaned
    summary = check(settings, store, checked_at=datetime(2026, 11, 12, 5, 30, tzinfo=PARIS))
    c04 = next(r for r in summary["resultats"] if r["id"] == "C04_volume")
    assert c04["ok"] and "non évalué" in c04["detail"]


def test_report_is_completed_and_metrics_written(settings, cleaned, alerts):
    store, report = cleaned
    check(settings, store, checked_at=AFTERNOON)

    md = (settings.reports_dir / "qualite-2026-11-12.md").read_text(encoding="utf-8")
    assert md.startswith("# Rapport de qualité du 2026-11-12")  # le rapport de la binôme…
    assert "## Règles à surveiller" in md
    assert "## Contrôles de qualité" in md  # … complété par les contrôles
    data = json.loads((settings.reports_dir / "qualite-2026-11-12.json").read_text("utf-8"))
    assert data["gardes"] == report["gardes"]
    assert len(data["controles"]["resultats"]) == 11

    metrics = settings.metrics_file.read_text(encoding="utf-8")
    assert f'gazette_qualite_articles{{statut="gardes"}} {report["gardes"]}' in metrics
    assert 'gazette_qualite_rejets_regle{regle="R05_trop_ancien"}' in metrics
    assert 'gazette_qualite_controle_ok{controle="C04_volume",gravite="critique"} 0' in metrics
    assert metrics.endswith("\n")


def test_alert_once_per_day_until_failures_change(settings, cleaned, alerts):
    store, _ = cleaned
    first = check(settings, store, checked_at=AFTERNOON)
    again = check(settings, store, checked_at=AFTERNOON + timedelta(minutes=30))
    assert first["alerte_envoyee"] is True
    assert again["alerte_envoyee"] is False
    assert len(alerts) == 1
    assert "C04_volume" in alerts[0]

    # Un nouvel échec (seuil de C03 abaissé) change la liste : nouvelle alerte.
    strict = ChecksConfig(taux_rejet_source_max=0.1, taux_rejet_items_min=1)
    third = check(settings, store, config=strict, checked_at=AFTERNOON + timedelta(hours=1))
    assert third["alerte_envoyee"] is True
    assert len(alerts) == 2


def test_no_alert_when_disabled(settings, cleaned, alerts):
    store, _ = cleaned
    summary = check(settings, store, alert=False, checked_at=AFTERNOON)
    assert summary["critiques_en_echec"] and not summary["alerte_envoyee"]
    assert alerts == []


def test_without_cleaning_report_checks_still_run(settings, alerts):
    summary = check(settings, InMemoryCleanStore(), checked_at=AFTERNOON)
    assert "C04_volume" in summary["critiques_en_echec"]
    assert not settings.metrics_file.exists()


def test_purge_applies_retention(settings, cleaned, alerts):
    store, _ = cleaned
    check(settings, store, checked_at=AFTERNOON)
    later = DAY + timedelta(days=400)

    dry = quality.run_purge(settings, later, dry_run=True)
    assert dry["brut"] == ["2026-11-12"]
    assert dry["alertes"] == ["2026-11-12.json"]
    assert sorted(dry["rapports"]) == ["qualite-2026-11-12.json", "qualite-2026-11-12.md"]
    assert (settings.raw_dir / "2026-11-12").exists()

    quality.run_purge(settings, later, purge_database=False)
    assert not (settings.raw_dir / "2026-11-12").exists()
    assert not list(settings.reports_dir.glob("qualite-*"))


def test_purge_keeps_recent_and_unknown_files(settings, cleaned):
    (settings.raw_dir / "pas-une-date").mkdir(parents=True)
    removed = quality.run_purge(settings, DAY + timedelta(days=10), purge_database=False)
    assert removed["brut"] == []
    assert (settings.raw_dir / "2026-11-12").exists()
    assert (settings.raw_dir / "pas-une-date").exists()
