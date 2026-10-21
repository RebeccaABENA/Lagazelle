"""Accès à la table `sources`.

`PostgresSourceRepository` est utilisé en production ; `InMemorySourceRepository`
a le même comportement et sert aux tests sans base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from psycopg.rows import dict_row

from gazette.db import connect
from gazette.sources import Source


@dataclass(frozen=True)
class SeedReport:
    inserted: list[str]
    updated: list[str]


class SourceRepository(Protocol):
    def all(self) -> list[Source]:
        """Toutes les sources, actives ou non, triées par type puis nom."""

    def get(self, slug: str) -> Source: ...

    def seed(self, sources: list[Source]) -> SeedReport:
        """Insère les nouvelles sources ; met à jour type et URL des existantes.
        `actif` et l'état de collecte ne sont fixés qu'à l'insertion : la base fait foi."""

    def record_success(
        self, name: str, *, at: datetime, etag: str | None, last_modified: str | None
    ) -> None:
        """Réussite (200 ou 304) : date, compteur d'échecs remis à 0, validateurs HTTP."""

    def record_failure(self, name: str) -> int:
        """Échec d'une collecte : incrémente et renvoie `echecs_consecutifs`."""

    def wake(self, slug: str) -> Source:
        """Réveille une source en sommeil : compteur d'échecs remis à 0."""


def _find(sources: list[Source], slug: str) -> Source:
    for source in sources:
        if source.slug == slug:
            return source
    raise KeyError(f"Source inconnue : {slug}")


def _order(sources: list[Source]) -> list[Source]:
    # PQR avant PQN, comme dans sources.csv
    return sorted(sources, key=lambda s: (s.type != "PQR", s.name))


# -- PostgreSQL ---------------------------------------------------------------
_SELECT = """
    SELECT nom, type, url_flux, actif, derniere_reussite, echecs_consecutifs,
           etag, last_modified
    FROM sources
"""


def _from_row(row: dict) -> Source:
    return Source(
        name=row["nom"],
        type=row["type"],
        feed_url=row["url_flux"],
        active=row["actif"],
        last_success=row["derniere_reussite"],
        consecutive_failures=row["echecs_consecutifs"],
        etag=row["etag"],
        last_modified=row["last_modified"],
    )


class PostgresSourceRepository:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def all(self) -> list[Source]:
        with connect(self.dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            return _order([_from_row(row) for row in cur.execute(_SELECT)])

    def get(self, slug: str) -> Source:
        return _find(self.all(), slug)

    def seed(self, sources: list[Source]) -> SeedReport:
        inserted, updated = [], []
        with connect(self.dsn) as conn, conn.transaction():
            for source in sources:
                row = conn.execute(
                    """INSERT INTO sources (nom, type, url_flux, actif)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (nom) DO UPDATE
                           SET type = EXCLUDED.type, url_flux = EXCLUDED.url_flux
                       RETURNING (xmax = 0) AS inserted""",
                    (source.name, source.type, source.feed_url, source.active),
                ).fetchone()
                (inserted if row[0] else updated).append(source.name)
        return SeedReport(inserted, updated)

    def record_success(
        self, name: str, *, at: datetime, etag: str | None, last_modified: str | None
    ) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """UPDATE sources
                   SET derniere_reussite = %s,
                       echecs_consecutifs = 0,
                       etag = COALESCE(%s, etag),
                       last_modified = COALESCE(%s, last_modified)
                   WHERE nom = %s""",
                (at, etag, last_modified, name),
            )

    def record_failure(self, name: str) -> int:
        with connect(self.dsn) as conn:
            row = conn.execute(
                """UPDATE sources SET echecs_consecutifs = echecs_consecutifs + 1
                   WHERE nom = %s RETURNING echecs_consecutifs""",
                (name,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Source inconnue : {name}")
        return row[0]

    def wake(self, slug: str) -> Source:
        source = self.get(slug)
        with connect(self.dsn) as conn:
            conn.execute("UPDATE sources SET echecs_consecutifs = 0 WHERE nom = %s", (source.name,))
        return self.get(slug)


# -- En mémoire (tests) -----------------------------------------------------------
class InMemorySourceRepository:
    def __init__(self, sources: list[Source] | None = None):
        self._sources: dict[str, Source] = {}
        if sources:
            self.seed(sources)

    def all(self) -> list[Source]:
        return _order(list(self._sources.values()))

    def get(self, slug: str) -> Source:
        return _find(self.all(), slug)

    def _update(self, name: str, **changes) -> None:
        self._sources[name] = self._sources[name].model_copy(update=changes)

    def seed(self, sources: list[Source]) -> SeedReport:
        inserted, updated = [], []
        for source in sources:
            if source.name in self._sources:
                self._update(source.name, type=source.type, feed_url=source.feed_url)
                updated.append(source.name)
            else:
                self._sources[source.name] = Source(
                    name=source.name,
                    type=source.type,
                    feed_url=source.feed_url,
                    active=source.active,
                )
                inserted.append(source.name)
        return SeedReport(inserted, updated)

    def record_success(
        self, name: str, *, at: datetime, etag: str | None, last_modified: str | None
    ) -> None:
        current = self._sources[name]
        self._update(
            name,
            last_success=at,
            consecutive_failures=0,
            etag=etag or current.etag,
            last_modified=last_modified or current.last_modified,
        )

    def record_failure(self, name: str) -> int:
        count = self._sources[name].consecutive_failures + 1
        self._update(name, consecutive_failures=count)
        return count

    def wake(self, slug: str) -> Source:
        self._update(self.get(slug).name, consecutive_failures=0)
        return self.get(slug)
