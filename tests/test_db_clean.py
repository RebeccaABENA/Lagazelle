"""Tables articles_propres et rejets sur une vraie base PostgreSQL (ignorés sans
GAZETTE_TEST_DATABASE_URL, comme tests/test_db.py)."""

from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest
from clean_helpers import load_sample, write_raw_day
from conftest import FIXTURES

from gazette.clean.pipeline import run_cleaning
from gazette.clean.rules import Rejected
from gazette.db import connect
from gazette.db.clean_repository import PostgresCleanStore
from gazette.db.migrate import migrate
from gazette.db.repository import PostgresSourceRepository
from gazette.sources import load_sources

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 12)
NOW = datetime(2026, 11, 12, 13, 0, tzinfo=UTC)

CLEAN_COLUMNS = [
    "id",
    "source",
    "lien",
    "titre",
    "resume",
    "rubrique",
    "publie_le",
    "recupere_le",
    "jour_collecte",
    "fichier_brut",
    "nettoye_le",
]
REJECT_COLUMNS = [
    "id",
    "source",
    "regle",
    "raison",
    "lien",
    "titre",
    "publie_le",
    "recupere_le",
    "jour_collecte",
    "fichier_brut",
    "rejete_le",
]


def article(n, source="Journal Régional", **fields):
    item = {
        "id": f"id-{n}",
        "source": source,
        "lien": f"https://example.fr/a{n}",
        "titre": f"Titre {n}",
        "resume": "Un extrait.",
        "rubrique": "Politique",
        "publie_le": "2026-11-12T05:00:00+00:00",
        "recupere_le": "2026-11-12T10:00:00+00:00",
        "_fichier": "2026-11-12/pqr/journal-regional.json",
    }
    return item | fields


def reject(n, rule="R01_quiz_jeux", **fields):
    return Rejected(article(n, **fields), rule, "titre commençant par « Quiz »")


@pytest.fixture
def store(pg_dsn):
    with connect(pg_dsn) as conn:
        migrate(conn)
    PostgresSourceRepository(pg_dsn).seed(
        load_sources(FIXTURES / "sources.csv", include_inactive=True)
    )
    return PostgresCleanStore(pg_dsn)


def columns(dsn, table):
    with connect(dsn) as conn:
        rows = conn.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = %s
               ORDER BY ordinal_position""",
            (table,),
        )
        return [row[0] for row in rows]


def test_migrations_create_the_clean_tables(store):
    assert columns(store.dsn, "articles_propres") == CLEAN_COLUMNS
    assert columns(store.dsn, "rejets") == REJECT_COLUMNS


def test_save_day_round_trip(store):
    store.save_day(DAY, [article(1), article(2, rubrique=None)], [reject(3)])
    clean, rejects = store.load_day(DAY)
    assert [r["id"] for r in clean] == ["id-1", "id-2"]
    assert clean[1]["rubrique"] is None
    assert clean[0]["publie_le"] == datetime(2026, 11, 12, 5, 0, tzinfo=UTC)
    assert clean[0]["fichier_brut"] == "2026-11-12/pqr/journal-regional.json"
    assert clean[0]["jour_collecte"] == DAY
    (rejected,) = rejects
    assert (rejected["id"], rejected["regle"]) == ("id-3", "R01_quiz_jeux")
    assert "Quiz" in rejected["raison"]


def test_an_article_without_publication_date_is_stored(store):
    store.save_day(DAY, [article(1, publie_le=None)], [])
    (row,), _ = store.load_day(DAY)
    assert row["publie_le"] is None


def test_save_day_is_idempotent(store):
    for _ in range(3):
        store.save_day(DAY, [article(1), article(2)], [reject(3)])
    clean, rejects = store.load_day(DAY)
    assert (len(clean), len(rejects)) == (2, 1)


def test_save_day_updates_an_existing_row(store):
    store.save_day(DAY, [article(1)], [])
    store.save_day(DAY, [article(1, titre="Titre corrigé")], [])
    (row,), _ = store.load_day(DAY)
    assert row["titre"] == "Titre corrigé"


def test_a_changed_verdict_moves_the_article_to_the_other_table(store):
    store.save_day(DAY, [article(1)], [])
    store.save_day(DAY, [], [reject(1, "R05_trop_ancien")])
    clean, rejects = store.load_day(DAY)
    assert ([r["id"] for r in clean], [(r["id"], r["regle"]) for r in rejects]) == (
        [],
        [("id-1", "R05_trop_ancien")],
    )
    store.save_day(DAY, [article(1)], [])
    clean, rejects = store.load_day(DAY)
    assert ([r["id"] for r in clean], rejects) == (["id-1"], [])


def test_an_unknown_source_is_refused_and_nothing_is_written(store):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.save_day(DAY, [article(1), article(2, source="Source inconnue")], [])
    assert store.load_day(DAY) == ([], [])  # la transaction est annulée en entier


def test_load_day_only_returns_the_requested_day(store):
    store.save_day(DAY, [article(1)], [])
    store.save_day(date(2026, 11, 13), [article(2)], [])
    assert [r["id"] for r in store.load_day(DAY)[0]] == ["id-1"]
    assert store.load_day(date(2026, 11, 14)) == ([], [])


def test_run_cleaning_on_postgres(store, settings):
    sample = load_sample()
    write_raw_day(settings, sample["items"], DAY)
    PostgresSourceRepository(store.dsn).seed(
        load_sources(ROOT / "sources.csv", include_inactive=True)
    )

    first = run_cleaning(settings, DAY, store=store, now=NOW)
    clean, rejects = store.load_day(DAY)
    assert (len(clean), len(rejects)) == (16, 13)
    assert (first["gardes"], first["rejetes"]) == (16, 13)
    verdicts = {r["id"][:8]: None for r in clean} | {r["id"][:8]: r["regle"] for r in rejects}
    assert verdicts == sample["expected"]

    again = run_cleaning(settings, DAY, store=store, now=NOW)  # rejeu : rien ne change
    assert again == first

    # Mêmes lignes ; seules les dates de dernier passage (nettoye_le, rejete_le) ont avancé.
    def stable(rows):
        return [{k: v for k, v in r.items() if k not in ("nettoye_le", "rejete_le")} for r in rows]

    again_clean, again_rejects = store.load_day(DAY)
    assert (stable(again_clean), stable(again_rejects)) == (stable(clean), stable(rejects))

    with connect(store.dsn) as conn:  # un article n'est jamais dans les deux tables
        both = conn.execute(
            "SELECT count(*) FROM articles_propres JOIN rejets USING (id)"
        ).fetchone()[0]
    assert both == 0
