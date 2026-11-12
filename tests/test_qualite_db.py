"""Contrôles de qualité de bout en bout sur un vrai PostgreSQL : nettoyage (binôme) ->
contrôles -> table `controles` -> purge. Ignorés sans GAZETTE_TEST_DATABASE_URL."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from clean_helpers import load_sample, write_raw_day

from gazette.clean import quality
from gazette.clean.pipeline import run_cleaning
from gazette.db import connect
from gazette.db.clean_repository import PostgresCleanStore
from gazette.db.migrate import available_migrations, migrate
from gazette.db.quality_repository import PostgresChecksHistory
from gazette.db.repository import PostgresSourceRepository
from gazette.sources import load_sources

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 12)
CLEANED = datetime(2026, 11, 12, 13, 0, tzinfo=UTC)
AFTERNOON = datetime(2026, 11, 12, 14, 0, tzinfo=ZoneInfo("Europe/Paris"))


@pytest.fixture
def db(settings, pg_dsn, monkeypatch):
    with connect(pg_dsn) as conn:
        migrate(conn)
    PostgresSourceRepository(pg_dsn).seed(load_sources(ROOT / "sources.csv", include_inactive=True))
    monkeypatch.setattr(quality, "send_alert", lambda url, msg: True)
    write_raw_day(settings, load_sample()["items"], DAY)
    settings = settings.model_copy(update={"database_url": pg_dsn})
    run_cleaning(settings, DAY, store=PostgresCleanStore(pg_dsn), now=CLEANED)
    return settings


def count(dsn, sql):
    with connect(dsn) as conn:
        return conn.execute(sql).fetchone()[0]


def test_migration_006_follows_the_cleaning_tables():
    names = [m.name for m in available_migrations()]
    assert names.index("006_create_controles.sql") > names.index("005_create_rejets.sql")


def test_checks_on_real_tables_and_history(db):
    first = quality.run_quality_checks(db, DAY, checked_at=AFTERNOON)
    quality.run_quality_checks(db, DAY, checked_at=AFTERNOON + timedelta(minutes=30))
    assert first["critiques_en_echec"] == ["C04_volume"]
    dsn = db.database_url
    assert count(dsn, "SELECT count(*) FROM controles") == 22  # 2 passages x 11
    assert count(dsn, "SELECT count(*) FROM controles WHERE NOT ok") == 2


def test_purge_tables(db):
    dsn = db.database_url
    PostgresChecksHistory(dsn)  # même base
    quality.run_quality_checks(db, DAY, checked_at=AFTERNOON)
    rejets = count(dsn, "SELECT count(*) FROM rejets")
    assert rejets > 0

    removed = quality.run_purge(db, DAY + timedelta(days=40))
    assert removed["base"] == {"rejets": rejets, "articles_propres": 0, "controles": 0}
    assert count(dsn, "SELECT count(*) FROM articles_propres") > 0
