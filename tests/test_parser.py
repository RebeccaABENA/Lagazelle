import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import fixture_bytes

from gazette.collect.parser import Item, ParseError, canonical_url, parse_feed

FETCHED_AT = datetime(2026, 10, 21, 5, 0, 12, tzinfo=ZoneInfo("Europe/Paris"))


def test_parse_feed_keeps_only_title_summary_link_date():
    items = parse_feed(fixture_bytes("feed_pqr.xml"), FETCHED_AT)

    # 5 entrées : un doublon d'URL canonique et un item sans lien sont écartés.
    assert [i.title for i in items] == [
        "Un pont rouvre à Rennes",
        "Marché de nuit à Nantes",
        "Article sans extrait",
    ]
    first = items[0]
    assert first.link == "https://pqr.example.fr/rennes/pont-rouvre?utm_source=rss&utm_medium=feed"
    assert first.published == "Wed, 21 Oct 2026 04:30:00 +0200"
    assert first.fetched_at == "2026-10-21T05:00:12+02:00"
    # Extrait tel que fourni, HTML compris (pas de nettoyage à ce stade).
    assert first.summary == "<p>Après six mois de travaux, <b>le pont</b> rouvre.</p>"
    # Aucune donnée personnelle ni champ supplémentaire.
    assert set(Item.model_fields) == {"id", "title", "summary", "link", "published", "fetched_at"}
    assert "Jeanne" not in str([i.model_dump() for i in items])


def test_id_is_sha256_of_canonical_url():
    items = parse_feed(fixture_bytes("feed_pqr.xml"), FETCHED_AT)
    expected = hashlib.sha256(b"https://pqr.example.fr/rennes/pont-rouvre").hexdigest()
    assert items[0].id == expected
    assert canonical_url("HTTPS://PQR.example.fr:443/a?xtor=RSS-1&b=2#frag") == (
        "https://pqr.example.fr/a?b=2"
    )


def test_full_text_is_never_stored():
    items = parse_feed(fixture_bytes("feed_pqr.xml"), FETCHED_AT)
    dumped = str([i.model_dump() for i in items])
    assert "TEXTE INTÉGRAL" not in dumped
    assert items[1].summary == "Le marché de nuit revient samedi."
    assert items[2].summary == ""  # pas de description : on ne garde pas content:encoded


def test_unreadable_feed_raises():
    with pytest.raises(ParseError):
        parse_feed(b"<html><body>Erreur 500</body", FETCHED_AT)
