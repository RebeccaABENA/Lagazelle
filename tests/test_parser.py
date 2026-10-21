import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import fixture_bytes

from gazette.collect.parser import Item, ParseError, canonical_url, item_id, parse_feed

FETCHED_AT = datetime(2026, 10, 21, 5, 0, 12, tzinfo=ZoneInfo("Europe/Paris"))
SOURCE = "Journal Régional"


def _parse(name="feed_pqr.xml"):
    return parse_feed(fixture_bytes(name), FETCHED_AT, SOURCE)


def test_item_has_the_expected_fields():
    assert list(Item.model_fields) == [
        "id",
        "source",
        "lien",
        "titre",
        "resume",
        "publie_le",
        "recupere_le",
        "rubrique",
        "payload",
    ]


def test_parse_rss_feed():
    parsed = _parse()
    # 5 entrées : un doublon de lien canonique et un item sans lien sont écartés.
    assert parsed.received == 5
    assert [i.titre for i in parsed.items] == [
        "Un pont rouvre à Rennes",
        "Marché de nuit à Nantes",
        "Article sans extrait",
    ]
    first = parsed.items[0]
    assert first.source == SOURCE
    assert first.lien == "https://pqr.example.fr/rennes/pont-rouvre?utm_source=rss&utm_medium=feed"
    assert first.publie_le == "2026-10-21T04:30:00+02:00"  # pubDate normalisée en ISO 8601
    assert first.recupere_le == "2026-10-21T05:00:12+02:00"
    assert first.rubrique == "Rennes"
    # Extrait tel que fourni, HTML compris (pas de nettoyage à ce stade).
    assert first.resume == "<p>Après six mois de travaux, <b>le pont</b> rouvre.</p>"
    # La valeur brute de la date reste disponible dans le payload.
    assert first.payload["published"] == "Wed, 21 Oct 2026 04:30:00 +0200"
    assert parsed.items[2].publie_le is None  # pas de date dans le flux


def test_id_is_sha256_of_source_and_canonical_link():
    item = _parse().items[0]
    expected = hashlib.sha256(
        f"{SOURCE}\nhttps://pqr.example.fr/rennes/pont-rouvre".encode()
    ).hexdigest()
    assert item.id == expected
    # Même lien, autre source : autre article.
    assert item_id("Autre Journal", item.lien) != item.id
    assert canonical_url("HTTPS://PQR.example.fr:443/a?xtor=RSS-1&b=2#frag") == (
        "https://pqr.example.fr/a?b=2"
    )


def test_guid_is_not_used_for_identity():
    # Deux entrées au même guid mais à liens différents sont deux articles.
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
      <item><guid>meme-guid</guid><title>A</title><link>https://x.fr/a</link></item>
      <item><guid>meme-guid</guid><title>B</title><link>https://x.fr/b</link></item>
    </channel></rss>"""
    assert len(parse_feed(feed, FETCHED_AT, SOURCE).items) == 2


def test_no_personal_data_nor_full_text():
    items = _parse().items
    dumped = str([i.model_dump() for i in items])
    for forbidden in ("Jeanne", "jeanne@", "#comments", "TEXTE INTÉGRAL"):
        assert forbidden not in dumped
    assert items[1].resume == "Le marché de nuit revient samedi."
    assert items[2].resume == ""  # pas de description : on ne garde pas content:encoded
    allowed = {"id", "title", "link", "links", "summary", "published", "updated", "tags"}
    assert all(set(i.payload) <= allowed for i in items)


def test_unreadable_feed_raises():
    with pytest.raises(ParseError):
        parse_feed(b"<html><body>Erreur 500</body", FETCHED_AT, SOURCE)
