"""Historique des contrôles (table `controles`, migration 006) et purge des tables du nettoyage.

Même organisation que clean_repository.py : `PostgresChecksHistory` en production,
`InMemoryChecksHistory` pour les tests sans base.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Protocol

from gazette.db import connect


class ChecksHistory(Protocol):
    def save(self, day: date, summary: dict) -> None:
        """Ajoute les résultats d'un passage des contrôles (une ligne par contrôle)."""


def _rows(day: date, summary: dict) -> list[tuple]:
    return [
        (
            day,
            summary["verifie_le"],
            r["id"],
            r["gravite"],
            r["ok"],
            json.dumps(r["valeur"]),
            json.dumps(r["seuil"]),
            r["detail"],
        )
        for r in summary["resultats"]
    ]


class PostgresChecksHistory:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def save(self, day: date, summary: dict) -> None:
        with connect(self.dsn) as conn, conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO controles (jour_collecte, verifie_le, controle, gravite, ok, "
                "valeur, seuil, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                _rows(day, summary),
            )


class InMemoryChecksHistory:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def save(self, day: date, summary: dict) -> None:
        self.rows.extend(_rows(day, summary))


def purge_tables(dsn: str, today: date, *, rejets_days: int, articles_days: int) -> dict[str, int]:
    """Supprime les lignes plus vieilles que la rétention (docs/donnees.md § 7)."""
    deleted = {}
    with connect(dsn) as conn, conn.transaction():
        for table, days in (
            ("rejets", rejets_days),
            ("articles_propres", articles_days),
            ("controles", articles_days),
        ):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE jour_collecte < %s", (today - timedelta(days=days),)
            )
            deleted[table] = cur.rowcount
    return deleted
