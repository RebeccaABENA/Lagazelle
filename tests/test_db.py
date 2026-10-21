"""Tests sur une vraie base PostgreSQL (ignorés sans GAZETTE_TEST_DATABASE_URL)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from conftest import FIXTURES

from gazette.db import connect
from gazette.db.migrate import available_migrations, migrate
from gazette.db.repository import PostgresArticleIndex, PostgresSourceRepository
from gazette.sources import load_sources

EXPECTED_COLUMNS = [
    "nom",
    "type",
    "url_flux",
    "actif",
    "derniere_reussite",
    "echecs_consecutifs",
    "etag",
    "last_modified",
]


def test_migrations_are_numbered_and_versioned():
    names = [m.name for m in available_migrations()]
    assert names[:3] == [
        "001_create_sources.sql",
        "002_sources_en_sommeil.sql",
        "003_create_articles_vus.sql",
    ]
    assert [m.version for m in available_migrations()] == sorted(
        m.version for m in available_migrations()
    )


def test_migrate_creates_sources_table_once(pg_dsn):
    with connect(pg_dsn) as conn:
        first = migrate(conn)
        second = migrate(conn)
        columns = [
            row[0]
            for row in conn.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'sources'
                   ORDER BY ordinal_position"""
            )
        ]
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
    assert first == [m.name for m in available_migrations()]
    assert second == []
    assert columns == EXPECTED_COLUMNS
    assert versions == [m.version for m in available_migrations()]


def test_seed_then_state_round_trip(pg_dsn):
    with connect(pg_dsn) as conn:
        migrate(conn)
    repo = PostgresSourceRepository(pg_dsn)
    csv_sources = load_sources(FIXTURES / "sources.csv", include_inactive=True)

    report = repo.seed(csv_sources)
    assert len(report.inserted) == 4 and report.updated == []
    assert repo.seed(csv_sources).updated == [s.name for s in csv_sources]  # idempotent

    by_slug = {s.slug: s for s in repo.all()}
    assert by_slug["source-desactivee"].active is False
    assert by_slug["journal-regional"].consecutive_failures == 0

    at = datetime(2026, 10, 21, 5, 0, tzinfo=ZoneInfo("Europe/Paris"))
    repo.record_success("Journal Régional", at=at, etag='"v1"', last_modified="Wed, 21 Oct")
    # Un 304 ne renvoie pas forcément les validateurs : on garde les précédents.
    repo.record_success("Journal Régional", at=at, etag=None, last_modified=None)
    source = repo.get("journal-regional")
    assert (source.etag, source.last_modified, source.last_success) == ('"v1"', "Wed, 21 Oct", at)


def test_failure_counter_wake_and_sleeping_view(pg_dsn):
    with connect(pg_dsn) as conn:
        migrate(conn)
    repo = PostgresSourceRepository(pg_dsn)
    repo.seed(load_sources(FIXTURES / "sources.csv", include_inactive=True))

    assert [repo.record_failure("Gazette en Panne") for _ in range(3)] == [1, 2, 3]
    assert repo.get("gazette-en-panne").sleeping
    with connect(pg_dsn) as conn:
        sleeping = conn.execute("SELECT nom, echecs_consecutifs FROM sources_en_sommeil").fetchall()
    assert sleeping == [("Gazette en Panne", 3)]

    assert repo.wake("gazette-en-panne").consecutive_failures == 0
    with connect(pg_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM sources_en_sommeil").fetchone()[0] == 0


def test_article_index_round_trip(pg_dsn):
    from datetime import date

    with connect(pg_dsn) as conn:
        migrate(conn)
    PostgresSourceRepository(pg_dsn).seed(load_sources(FIXTURES / "sources.csv"))
    index = PostgresArticleIndex(pg_dsn)

    assert index.unseen(["a", "b"]) == {"a", "b"}
    index.add("Journal Régional", ["a"], date(2026, 10, 21))
    index.add("Journal Régional", ["a", "b"], date(2026, 10, 22))  # « a » ignoré, pas d'erreur
    assert index.unseen(["a", "b", "c"]) == {"c"}
    with connect(pg_dsn) as conn:
        rows = conn.execute("SELECT id, premiere_collecte FROM articles_vus ORDER BY id").fetchall()
    assert rows == [("a", date(2026, 10, 21)), ("b", date(2026, 10, 22))]
