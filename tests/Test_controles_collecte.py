"""Tests complémentaires : backoff du 503, deux passages identiques, même lien deux fois.

À copier dans tests/ du dépôt (utilise les fixtures de conftest.py).
"""

import contextlib
import json
from datetime import timedelta

import httpx
from conftest import DAY, fixture_bytes

from gazette.collect.fetcher import FetchError, PoliteFetcher
from gazette.collect.runner import run_collection

PQR_FEED = "https://pqr.example.fr/rss.xml"
PQN_FEED = "https://pqn.example.fr/rss/une.xml"

DOUBLON = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
  <item><title>A</title><link>https://pqr.example.fr/a?utm_source=rss</link></item>
  <item><title>A bis</title><link>https://PQR.example.fr/a#haut</link></item>
  <item><title>B</title><link>https://pqr.example.fr/b</link></item>
</channel></rss>"""


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _items(path):
    return json.loads(path.read_text(encoding="utf-8"))["items"]


# 1. 503 : l'attente entre les essais augmente (le test existant la neutralise)
def test_503_backoff_waits_grow(settings, web):
    web.add(PQN_FEED, httpx.Response(503))
    sleeps = []
    fetcher = PoliteFetcher(
        settings.model_copy(update={"domain_delay": 0}),
        client=httpx.Client(transport=httpx.MockTransport(web.handler)),
        sleep=sleeps.append,
    )
    with contextlib.suppress(FetchError):
        fetcher.fetch(PQN_FEED)
    assert sleeps == [1, 2]  # 3 essais = 2 attentes, la 2e plus longue


# 2. deux passages identiques : aucun nouveau fichier, aucun article de plus
def test_second_identical_pass_creates_no_file_and_no_item(
    settings, web, make_fetcher, repo, index
):
    web.add(PQR_FEED, httpx.Response(200, content=fixture_bytes("feed_pqr.xml")))
    web.add(PQN_FEED, httpx.Response(200, content=fixture_bytes("feed_pqn.xml")))
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)
    day = settings.raw_dir / DAY.isoformat()
    files_1 = _files(settings.raw_dir)
    items_1 = {f: _items(day / f) for f in _files(day) if f.endswith(".json") and "/" in f}
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)
    assert _files(settings.raw_dir) == files_1
    assert {f: _items(day / f) for f in items_1} == items_1


# 3. même lien deux fois : une seule ligne stockée, de bout en bout
def test_same_link_twice_in_one_feed_is_stored_once(settings, web, make_fetcher, repo, index):
    web.add(PQR_FEED, httpx.Response(200, content=DOUBLON))
    run_collection(
        settings, DAY, slugs=["journal-regional"], fetcher=make_fetcher(), repo=repo, index=index
    )
    path = settings.raw_dir / DAY.isoformat() / "pqr" / "journal-regional.json"
    assert [i["titre"] for i in _items(path)] == ["A", "B"]


def test_same_link_in_another_variant_next_day_is_not_rewritten(
    settings, web, make_fetcher, repo, index
):
    web.add(PQR_FEED, httpx.Response(200, content=DOUBLON))
    run_collection(
        settings, DAY, slugs=["journal-regional"], fetcher=make_fetcher(), repo=repo, index=index
    )
    again = DOUBLON.replace(
        b"https://pqr.example.fr/a?utm_source=rss", b"https://pqr.example.fr/a?utm_medium=x"
    )
    web.add(PQR_FEED, httpx.Response(200, content=again))
    run_collection(
        settings,
        DAY + timedelta(days=1),
        slugs=["journal-regional"],
        fetcher=make_fetcher(),
        repo=repo,
        index=index,
    )
    path = (
        settings.raw_dir / (DAY + timedelta(days=1)).isoformat() / "pqr" / "journal-regional.json"
    )
    assert _items(path) == []