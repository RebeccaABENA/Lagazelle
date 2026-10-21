"""Outils de test : aucun appel réseau, tout passe par httpx.MockTransport."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from gazette.collect.fetcher import PoliteFetcher
from gazette.config import Settings
from gazette.db.repository import InMemoryArticleIndex, InMemorySourceRepository
from gazette.sources import load_sources

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 10, 21)

Route = httpx.Response | Callable[[httpx.Request], httpx.Response]


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeWeb:
    """Serveur simulé : une réponse (ou une fonction) par URL, journal des requêtes."""

    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url: str, route: Route) -> None:
        self.routes[url] = route

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is None:
            # Par défaut : pas de robots.txt (aucune restriction), 404 ailleurs.
            return httpx.Response(404)
        return route(request) if callable(route) else route

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        sources_file=FIXTURES / "sources.csv",
        contact_email="test@example.org",
        domain_delay=0.5,
    )


@pytest.fixture
def repo() -> InMemorySourceRepository:
    """Table `sources` simulée, amorcée depuis tests/fixtures/sources.csv."""
    return InMemorySourceRepository(load_sources(FIXTURES / "sources.csv", include_inactive=True))


@pytest.fixture
def index() -> InMemoryArticleIndex:
    """Table `articles_vus` simulée."""
    return InMemoryArticleIndex()


@pytest.fixture
def pg_dsn() -> Iterator[str]:
    """Vraie base PostgreSQL, dans un schéma jetable. Test ignoré sans
    GAZETTE_TEST_DATABASE_URL (fourni par la CI, ou pointant vers le postgres Docker)."""
    psycopg = pytest.importorskip("psycopg")
    base = os.environ.get("GAZETTE_TEST_DATABASE_URL")
    if not base:
        pytest.skip("GAZETTE_TEST_DATABASE_URL non défini")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    separator = "&" if "?" in base else "?"
    try:
        yield f"{base}{separator}options=-csearch_path%3D{schema}"
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def make_fetcher(settings: Settings, web: FakeWeb):
    """Fabrique un PoliteFetcher branché sur le faux web, sans attente réelle."""
    sleeps: list[float] = []

    def _make() -> PoliteFetcher:
        fetcher = PoliteFetcher(
            settings,
            client=httpx.Client(transport=httpx.MockTransport(web.handler)),
            sleep=sleeps.append,
            retry_wait=wait_none(),
        )
        fetcher.sleeps = sleeps
        return fetcher

    return _make
