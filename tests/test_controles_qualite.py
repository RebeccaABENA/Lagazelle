"""Contrôles de qualité (clean/checks.py) : chaque fait est vrai sur des données saines,
et faux quand on y glisse l'anomalie qu'il doit détecter."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from gazette.alert import format_check_alert, send_alert
from gazette.clean.checks import (
    AVERTISSEMENT,
    CRITIQUE,
    ChecksConfig,
    DayFacts,
    has_broken_encoding,
    run_checks,
    summarize,
)

RECUPERE = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
CFG = ChecksConfig()


def row(i: int, source: str = "Sud Ouest", **kw) -> dict:
    """Une ligne de articles_propres (colonnes de la migration 004)."""
    return {
        "id": f"id{i}",
        "source": source,
        "titre": f"Un titre d'article numéro {i} tout à fait correct",
        "resume": "Un extrait court.",
        "publie_le": RECUPERE - timedelta(hours=2),
        "recupere_le": RECUPERE,
    } | kw


def rejected(i: int, source: str) -> dict:
    return {"id": f"rej{i}", "source": source, "titre": "Quiz", "regle": "R01_quiz_jeux"}


@pytest.fixture
def healthy():
    n = CFG.articles_min
    clean = [row(i, "Sud Ouest" if i % 2 else "Le Monde") for i in range(n)]
    rejects = [rejected(i, "Sud Ouest") for i in range(5)]
    return DayFacts(clean=clean, rejects=rejects)


def by_id(facts, cfg=CFG):
    return {r.id: r for r in run_checks(facts, cfg)}


def test_all_checks_pass_on_healthy_data(healthy):
    results = run_checks(healthy)
    assert len(results) == 11
    assert all(r.ok for r in results), [r for r in results if not r.ok]


@pytest.mark.parametrize(
    ("check_id", "bad"),
    [
        ("C01_dates_futures", {"publie_le": RECUPERE + timedelta(hours=3)}),
        ("C02_titres_vides", {"titre": "  "}),
        ("C06_fraicheur", {"publie_le": RECUPERE - timedelta(days=3)}),  # seuil R05 : 48 h
        ("C07_html_residuel", {"resume": "Texte <b>gras</b>"}),
        ("C07_html_residuel", {"titre": "AT&amp;T rachète un concurrent pour la forme"}),
        ("C08_encodage", {"titre": "Ã‰lections rÃ©gionales du dimanche"}),
        ("C08_encodage", {"resume": "Le march� de No�l"}),
        ("C09_extrait_borne", {"resume": "x" * 2000}),
        ("C10_titres_uniques", {"titre": "Un titre d'article NUMÉRO 1 tout à fait correct"}),
    ],
)
def test_check_detects_bad_article(healthy, check_id, bad):
    healthy.clean[0] = row(0, **bad)
    result = by_id(healthy)[check_id]
    assert not result.ok
    assert result.gravite == CRITIQUE


def test_missing_publication_date_is_not_an_error(healthy):
    """Une date absente n'est ni future ni trop vieille (la règle R05 l'accepte aussi)."""
    healthy.clean[0] = row(0, publie_le=None)
    assert all(r.ok for r in run_checks(healthy))


def test_c03_reject_rate_per_source(healthy):
    healthy.rejects += [rejected(100 + i, "L'Obs") for i in range(18)]
    healthy.clean.append(row(999, "L'Obs"))
    result = by_id(healthy)["C03_taux_rejet_source"]
    assert not result.ok
    assert result.gravite == AVERTISSEMENT
    assert "L'Obs 95%" in result.detail


def test_c03_ignores_small_sources(healthy):
    healthy.rejects += [rejected(100 + i, "Petite source") for i in range(3)]
    assert by_id(healthy)["C03_taux_rejet_source"].ok


def test_c04_volume_only_judged_when_evaluable(healthy):
    few = replace(healthy, clean=healthy.clean[:5])
    assert not by_id(few)["C04_volume"].ok
    early = replace(few, volume_evaluable=False)
    result = by_id(early)["C04_volume"]
    assert result.ok and "non évalué" in result.detail


def test_c05_and_c11_are_warnings(healthy):
    healthy.rejects += [rejected(200 + i, "Muette") for i in range(4)]
    healthy.collect_failed.append("ouest-france")
    results = by_id(healthy)
    assert results["C05_couverture_sources"].detail == "Muette"
    assert not results["C11_collecte"].ok
    summary = summarize(list(results.values()), RECUPERE)
    assert summary["critiques_en_echec"] == []
    assert summary["avertissements"] == ["C05_couverture_sources", "C11_collecte"]


def test_has_broken_encoding():
    assert has_broken_encoding("rÃ©gion")
    assert has_broken_encoding("caf�")
    assert not has_broken_encoding("région ÀÉÈ « » — … ✓")


def test_alert_message_and_webhooks(healthy):
    healthy.clean[0] = row(0, publie_le=RECUPERE + timedelta(hours=3))
    summary = summarize(run_checks(healthy), RECUPERE)
    message = format_check_alert("2026-11-12", summary, "data/rapports/qualite-2026-11-12.md")
    assert "C01_dates_futures" in message
    assert message.startswith("🔴")

    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.read())
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_alert("https://discord.com/api/webhooks/1/abc", message, client=client)
    assert send_alert("https://hooks.slack.com/services/T/B/C", message, client=client)
    assert b'"content"' in sent[0]
    assert b'"text"' in sent[1]


def test_alert_never_raises():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert send_alert("https://hooks.slack.com/x", "msg", client=client) is False
    assert send_alert(None, "msg") is False
