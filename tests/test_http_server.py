"""Collecte contre un vrai serveur HTTP local (http.server dans un thread).

Aucun appel vers Internet : le serveur écoute sur 127.0.0.1, port choisi par l'OS.
On utilise le vrai client httpx du PoliteFetcher (pas de transport simulé).
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from conftest import DAY, fixture_bytes
from tenacity import wait_none

from gazette.collect.fetcher import PoliteFetcher
from gazette.collect.runner import run_collection
from gazette.db.repository import InMemoryArticleIndex, InMemorySourceRepository
from gazette.sources import Source

ROUTES = {
    "/ok/rss.xml": (200, "application/rss+xml", fixture_bytes("feed_pqn.xml")),
    "/atom/feed.atom": (200, "application/atom+xml", fixture_bytes("feed_atom.xml")),
    "/panne/rss.xml": (503, "text/plain", b"Service Unavailable"),
}


class _Handler(BaseHTTPRequestHandler):
    hits: ClassVar[Counter] = Counter()  # requêtes reçues, par chemin

    def do_GET(self):  # nom imposé par http.server
        _Handler.hits[self.path] += 1
        status, content_type, body = ROUTES.get(self.path, (404, "text/plain", b"Not Found"))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence dans la sortie de pytest
        pass


@pytest.fixture
def local_server() -> Iterator[str]:
    _Handler.hits = Counter()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_source_returning_503_does_not_block_the_others(settings, local_server):
    repo = InMemorySourceRepository(
        [
            Source(name="Source OK", type="PQN", feed_url=f"{local_server}/ok/rss.xml"),
            Source(name="Source Atom", type="PQR", feed_url=f"{local_server}/atom/feed.atom"),
            Source(name="Source En Panne", type="PQR", feed_url=f"{local_server}/panne/rss.xml"),
        ]
    )
    settings = settings.model_copy(update={"domain_delay": 0.0})
    fetcher = PoliteFetcher(settings, retry_wait=wait_none())  # vrai client HTTP
    try:
        manifest = run_collection(
            settings, DAY, fetcher=fetcher, repo=repo, index=InMemoryArticleIndex()
        )
    finally:
        fetcher.close()

    by_slug = {e["slug"]: e for e in manifest["sources"]}
    broken = by_slug["source-en-panne"]
    assert (broken["status"], broken["http_status"]) == ("failed", 503)
    assert "HTTP 503 après 3 essais" in broken["error"]
    assert _Handler.hits["/panne/rss.xml"] == 3  # 3 essais au maximum, pas plus
    assert repo.get("source-en-panne").consecutive_failures == 1

    # Les autres sources ont bien été écrites malgré la panne.
    day = settings.raw_dir / "2026-10-21"
    ok = json.loads((day / "pqn" / "source-ok.json").read_text(encoding="utf-8"))
    atom = json.loads((day / "pqr" / "source-atom.json").read_text(encoding="utf-8"))
    assert len(ok["items"]) == 2 and len(atom["items"]) == 2
    assert manifest["summary"]["ok"] == 2 and manifest["summary"]["failed"] == 1
    assert not (day / "pqr" / "source-en-panne.json").exists()
    # robots.txt demandé une fois pour l'hôte (404 : aucune restriction).
    assert _Handler.hits["/robots.txt"] == 1
