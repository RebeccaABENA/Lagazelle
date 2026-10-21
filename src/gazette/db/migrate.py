"""Migrations SQL numérotées (`NNN_description.sql`), appliquées une seule fois, dans l'ordre.

Les versions appliquées sont notées dans la table `schema_migrations`. Un verrou
consultatif évite que deux processus migrent en même temps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib import resources

import psycopg

log = logging.getLogger(__name__)

_FILENAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
_LOCK_KEY = 7_303_011  # identifiant arbitraire du verrou consultatif


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str


def available_migrations() -> list[Migration]:
    folder = resources.files("gazette.db") / "migrations"
    migrations = []
    for entry in folder.iterdir():
        match = _FILENAME.match(entry.name)
        if match:
            migrations.append(Migration(match.group(1), entry.name, entry.read_text("utf-8")))
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"Numéros de migration en double : {versions}")
    return migrations


def migrate(conn: psycopg.Connection) -> list[str]:
    """Applique les migrations manquantes. Renvoie les noms des fichiers appliqués."""
    conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version     text PRIMARY KEY,
                   fichier     text NOT NULL,
                   applique_le timestamptz NOT NULL DEFAULT now()
               )"""
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        done = []
        for migration in available_migrations():
            if migration.version in applied:
                continue
            with conn.transaction():
                conn.execute(migration.sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, fichier) VALUES (%s, %s)",
                    (migration.version, migration.name),
                )
            log.info("Migration appliquée : %s", migration.name)
            done.append(migration.name)
        return done
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
