import json

import httpx
import pytest
from conftest import DAY, fixture_bytes

from gazette.collect.runner import run_collection
from gazette.storage import atomic
from gazette.storage.atomic import atomic_write_json

PQR_FEED = "https://pqr.example.fr/rss.xml"
PQN_FEED = "https://pqn.example.fr/rss/une.xml"
BROKEN_FEED = "https://panne.example.fr/rss.xml"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def healthy_web(web):
    web.add(PQR_FEED, httpx.Response(200, content=fixture_bytes("feed_pqr.xml")))
    web.add(PQN_FEED, httpx.Response(200, content=fixture_bytes("feed_pqn.xml")))
    web.add(BROKEN_FEED, httpx.Response(500))
    return web


def test_tree_and_file_names(settings, healthy_web, make_fetcher):
    run_collection(settings, DAY, fetcher=make_fetcher())

    day = settings.raw_dir / "2026-10-21"
    files = sorted(p.relative_to(day).as_posix() for p in day.rglob("*") if p.is_file())
    assert files == ["_manifest.json", "pqn/l-echo-national.json", "pqr/journal-regional.json"]

    doc = _load(day / "pqr" / "journal-regional.json")
    assert doc["source"] == "Journal Régional"
    assert doc["slug"] == "journal-regional"
    assert doc["type"] == "PQR"
    assert doc["feed_url"] == PQR_FEED
    assert doc["collection_date"] == "2026-10-21"
    assert [f["new_items"] for f in doc["fetches"]] == [3]
    assert doc["fetches"][0]["http_status"] == 200
    assert len(doc["items"]) == 3


def test_failing_source_does_not_block_others(settings, healthy_web, make_fetcher):
    manifest = run_collection(settings, DAY, fetcher=make_fetcher())

    by_slug = {e["slug"]: e for e in manifest["sources"]}
    assert by_slug["journal-regional"]["status"] == "ok"
    assert by_slug["l-echo-national"]["status"] == "ok"
    broken = by_slug["gazette-en-panne"]
    assert broken["status"] == "failed"
    assert broken["http_status"] == 500
    assert "HTTP 500" in broken["error"]
    assert manifest["summary"] == {
        "sources": 3,
        "ok": 2,
        "not_modified": 0,
        "failed": 1,
        "new_items": 5,
        "items_total": 5,
    }
    # Le manifeste sur disque est identique à celui renvoyé.
    assert _load(settings.raw_dir / "2026-10-21" / "_manifest.json") == manifest
    assert not (settings.raw_dir / "2026-10-21" / "pqr" / "gazette-en-panne.json").exists()


def test_second_collection_same_day_adds_no_duplicates(settings, healthy_web, make_fetcher):
    run_collection(settings, DAY, fetcher=make_fetcher())
    # Deuxième passage : un item nouveau et un item déjà vu dans le flux national.
    healthy_web.add(PQN_FEED, httpx.Response(200, content=fixture_bytes("feed_pqn_update.xml")))
    manifest = run_collection(settings, DAY, fetcher=make_fetcher())

    doc = _load(settings.raw_dir / "2026-10-21" / "pqn" / "l-echo-national.json")
    ids = [item["id"] for item in doc["items"]]
    assert len(ids) == len(set(ids)) == 3
    assert [f["new_items"] for f in doc["fetches"]] == [2, 1]

    pqr = _load(settings.raw_dir / "2026-10-21" / "pqr" / "journal-regional.json")
    assert len(pqr["items"]) == 3
    assert [f["new_items"] for f in pqr["fetches"]] == [3, 0]
    assert manifest["summary"]["items_total"] == 6


def test_not_modified_feed_is_reported(settings, web, make_fetcher):
    def feed(request):
        if request.headers.get("If-None-Match") == '"abc"':
            return httpx.Response(304)
        return httpx.Response(200, content=fixture_bytes("feed_pqn.xml"), headers={"ETag": '"abc"'})

    web.add(PQN_FEED, feed)
    run_collection(settings, DAY, slugs=["l-echo-national"], fetcher=make_fetcher())
    manifest = run_collection(settings, DAY, slugs=["l-echo-national"], fetcher=make_fetcher())

    entry = next(e for e in manifest["sources"] if e["slug"] == "l-echo-national")
    assert entry["status"] == "not_modified"
    assert entry["items_total"] == 2
    doc = _load(settings.raw_dir / "2026-10-21" / "pqn" / "l-echo-national.json")
    assert [(f["http_status"], f["new_items"]) for f in doc["fetches"]] == [(200, 2), (304, 0)]


def test_atomic_write_keeps_previous_file_on_crash(tmp_path, monkeypatch):
    target = tmp_path / "source.json"
    atomic_write_json(target, {"items": [1, 2, 3]})

    def crashing_dump(obj, fh, **kwargs):
        fh.write('{"items": [1, 2')  # écriture partielle...
        raise OSError("disque plein")  # ...puis plantage

    monkeypatch.setattr(atomic.json, "dump", crashing_dump)
    with pytest.raises(OSError, match="disque plein"):
        atomic_write_json(target, {"items": [4]})

    # L'ancien fichier est intact et aucun fichier temporaire ne traîne.
    assert _load(target) == {"items": [1, 2, 3]}
    assert [p.name for p in tmp_path.iterdir()] == ["source.json"]


def test_manifest_marks_sources_without_result_as_failed(settings):
    from gazette.collect.runner import finalize

    manifest = finalize(settings, DAY)
    assert manifest["summary"]["failed"] == 3
    assert all("aucun résultat" in e["error"] for e in manifest["sources"])
