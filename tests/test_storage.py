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


def test_tree_and_file_names(settings, healthy_web, make_fetcher, repo, index):
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)

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


def test_failing_source_does_not_block_others(settings, healthy_web, make_fetcher, repo, index):
    manifest = run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)

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
        "sleeping": 0,
        "new_items": 5,
        "items_total": 5,
    }
    # Le manifeste sur disque est identique à celui renvoyé.
    assert _load(settings.raw_dir / "2026-10-21" / "_manifest.json") == manifest
    assert not (settings.raw_dir / "2026-10-21" / "pqr" / "gazette-en-panne.json").exists()


def test_second_collection_same_day_adds_no_duplicates(
    settings, healthy_web, make_fetcher, repo, index
):
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)
    # Deuxième passage : un item nouveau et un item déjà vu dans le flux national.
    healthy_web.add(PQN_FEED, httpx.Response(200, content=fixture_bytes("feed_pqn_update.xml")))
    manifest = run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)

    doc = _load(settings.raw_dir / "2026-10-21" / "pqn" / "l-echo-national.json")
    ids = [item["id"] for item in doc["items"]]
    assert len(ids) == len(set(ids)) == 3
    assert [f["new_items"] for f in doc["fetches"]] == [2, 1]

    pqr = _load(settings.raw_dir / "2026-10-21" / "pqr" / "journal-regional.json")
    assert len(pqr["items"]) == 3
    assert [f["new_items"] for f in pqr["fetches"]] == [3, 0]
    assert manifest["summary"]["items_total"] == 6


def test_not_modified_feed_is_reported(settings, web, make_fetcher, repo, index):
    def feed(request):
        if request.headers.get("If-None-Match") == '"abc"':
            return httpx.Response(304)
        return httpx.Response(200, content=fixture_bytes("feed_pqn.xml"), headers={"ETag": '"abc"'})

    web.add(PQN_FEED, feed)
    run_collection(
        settings, DAY, slugs=["l-echo-national"], fetcher=make_fetcher(), repo=repo, index=index
    )
    manifest = run_collection(
        settings, DAY, slugs=["l-echo-national"], fetcher=make_fetcher(), repo=repo, index=index
    )

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


def test_manifest_marks_sources_without_result_as_failed(settings, repo, index):
    from gazette.collect.runner import finalize

    manifest = finalize(settings, DAY, repo)
    assert manifest["summary"]["failed"] == 3
    assert all("aucun résultat" in e["error"] for e in manifest["sources"])


def test_article_seen_yesterday_is_not_written_again(
    settings, healthy_web, make_fetcher, repo, index
):
    from datetime import timedelta

    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)
    # Le lendemain, le flux national publie un article nouveau et en garde un ancien.
    healthy_web.add(PQN_FEED, httpx.Response(200, content=fixture_bytes("feed_pqn_update.xml")))
    next_day = DAY + timedelta(days=1)
    manifest = run_collection(settings, next_day, fetcher=make_fetcher(), repo=repo, index=index)

    doc = _load(settings.raw_dir / "2026-10-22" / "pqn" / "l-echo-national.json")
    assert [item["titre"] for item in doc["items"]] == ["Sport : victoire en coupe d'Europe"]
    # Flux régional inchangé : rien de réécrit, mais le passage est tracé.
    pqr = _load(settings.raw_dir / "2026-10-22" / "pqr" / "journal-regional.json")
    assert pqr["items"] == []
    assert pqr["fetches"][0]["new_items"] == 0
    by_slug = {e["slug"]: e for e in manifest["sources"]}
    assert (by_slug["l-echo-national"]["received"], by_slug["l-echo-national"]["new_items"]) == (
        2,
        1,
    )
    assert by_slug["l-echo-national"]["ignored"] == 1


def test_stored_items_follow_the_data_dictionary(settings, healthy_web, make_fetcher, repo, index):
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)
    doc = _load(settings.raw_dir / "2026-10-21" / "pqr" / "journal-regional.json")
    assert set(doc["items"][0]) == {
        "id",
        "source",
        "lien",
        "titre",
        "resume",
        "publie_le",
        "recupere_le",
        "rubrique",
        "payload",
    }
    assert {item["source"] for item in doc["items"]} == {"Journal Régional"}
    assert set(index.entries) == {item["id"] for item in doc["items"]} | {
        item["id"]
        for item in _load(settings.raw_dir / "2026-10-21" / "pqn" / "l-echo-national.json")["items"]
    }


def test_one_log_line_per_source_with_counts(
    settings, healthy_web, make_fetcher, repo, index, caplog
):
    import logging
    import re

    caplog.set_level(logging.INFO, logger="gazette.collect.runner")
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("source=")]
    assert len(lines) == 6  # 3 sources x 2 passages
    pattern = re.compile(
        r"^source=(?P<slug>[a-z0-9-]+) statut=(?P<status>\w+) http=(?P<http>\d+|-) "
        r"recus=(?P<recus>\d+) ecrits=(?P<ecrits>\d+) ignores=(?P<ignores>\d+) "
        r"duree=\d+\.\d{2}s"
    )
    parsed = [pattern.match(line).groupdict() for line in lines]
    regional = [p for p in parsed if p["slug"] == "journal-regional"]
    # 1er passage : 5 entrées, 3 écrites ; 2e passage : 5 reçues, 0 écrite, 5 ignorées.
    assert [(p["recus"], p["ecrits"], p["ignores"]) for p in regional] == [
        ("5", "3", "2"),
        ("5", "0", "5"),
    ]
    failed = next(line for line in lines if "gazette-en-panne" in line)
    assert "statut=failed http=500" in failed
    assert "erreur_type=transitoire" in failed and "echecs_consecutifs=1" in failed


def test_manifest_entries_share_the_same_keys(settings, healthy_web, make_fetcher, repo, index):
    for _ in range(3):  # la source en panne passe en sommeil
        manifest = run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo, index=index)
    keys = [set(entry) for entry in manifest["sources"]]
    assert {e["status"] for e in manifest["sources"]} == {"ok", "sleeping"}
    assert all(k == keys[0] for k in keys)
