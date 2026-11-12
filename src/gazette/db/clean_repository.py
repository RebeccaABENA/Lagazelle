"""Accès aux tables `articles_propres` et `rejets`.

`PostgresCleanStore` est utilisé en production ; `InMemoryCleanStore` a le même comportement
et sert aux tests sans base (comme `repository.py` pour la table `sources`).

Les articles reçus sont ceux de `clean.normalize.normalize_item`, avec une clé en plus,
`_fichier` : le fichier brut d'origine, relatif à `data/articles_bruts/`. C'est elle qui permet
de remonter d'un article propre jusqu'à l'article brut.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol

from psycopg.rows import dict_row

from gazette.clean.rules import Item, Rejected, parse_date
from gazette.db import connect


class CleanStore(Protocol):
    def save_day(self, day: date, kept: Sequence[Item], rejected: Sequence[Rejected]) -> None:
        """Enregistre les verdicts du jour. Rejouer le même jour ne crée aucun doublon.

        Un article est soit propre, soit rejeté : si son verdict change d'un passage à
        l'autre, il est déplacé d'une table à l'autre.
        """

    def load_day(self, day: date) -> tuple[list[dict], list[dict]]:
        """Lignes de `articles_propres` et de `rejets` pour ce jour de collecte."""


def _clean_row(day: date, item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "source": item["source"],
        "lien": item["lien"],
        "titre": item.get("titre") or "",
        "resume": item.get("resume") or "",
        "rubrique": item.get("rubrique"),
        "publie_le": parse_date(item.get("publie_le")),
        "recupere_le": parse_date(item.get("recupere_le")),
        "jour_collecte": day,
        "fichier_brut": item["_fichier"],
    }


def _reject_row(day: date, rejected: Rejected) -> dict[str, Any]:
    row = _clean_row(day, rejected.item)
    row.pop("resume")
    row.pop("rubrique")
    return {**row, "regle": rejected.rule, "raison": rejected.reason}


# -- PostgreSQL -----------------------------------------------------------------------------
_UPSERT_CLEAN = """
    INSERT INTO articles_propres
        (id, source, lien, titre, resume, rubrique, publie_le, recupere_le,
         jour_collecte, fichier_brut)
    VALUES
        (%(id)s, %(source)s, %(lien)s, %(titre)s, %(resume)s, %(rubrique)s, %(publie_le)s,
         %(recupere_le)s, %(jour_collecte)s, %(fichier_brut)s)
    ON CONFLICT (id) DO UPDATE SET
        source = EXCLUDED.source, lien = EXCLUDED.lien, titre = EXCLUDED.titre,
        resume = EXCLUDED.resume, rubrique = EXCLUDED.rubrique,
        publie_le = EXCLUDED.publie_le, recupere_le = EXCLUDED.recupere_le,
        jour_collecte = EXCLUDED.jour_collecte, fichier_brut = EXCLUDED.fichier_brut,
        nettoye_le = now()
"""

_UPSERT_REJECT = """
    INSERT INTO rejets
        (id, source, regle, raison, lien, titre, publie_le, recupere_le,
         jour_collecte, fichier_brut)
    VALUES
        (%(id)s, %(source)s, %(regle)s, %(raison)s, %(lien)s, %(titre)s, %(publie_le)s,
         %(recupere_le)s, %(jour_collecte)s, %(fichier_brut)s)
    ON CONFLICT (id) DO UPDATE SET
        source = EXCLUDED.source, regle = EXCLUDED.regle, raison = EXCLUDED.raison,
        lien = EXCLUDED.lien, titre = EXCLUDED.titre, publie_le = EXCLUDED.publie_le,
        recupere_le = EXCLUDED.recupere_le, jour_collecte = EXCLUDED.jour_collecte,
        fichier_brut = EXCLUDED.fichier_brut, rejete_le = now()
"""


class PostgresCleanStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def save_day(self, day: date, kept: Sequence[Item], rejected: Sequence[Rejected]) -> None:
        clean_rows = [_clean_row(day, item) for item in kept]
        reject_rows = [_reject_row(day, r) for r in rejected]
        # Une seule transaction : on ne voit jamais un article dans les deux tables à la fois.
        with connect(self.dsn) as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "DELETE FROM articles_propres WHERE id = ANY(%s)", ([r["id"] for r in reject_rows],)
            )
            cur.execute("DELETE FROM rejets WHERE id = ANY(%s)", ([r["id"] for r in clean_rows],))
            if clean_rows:
                cur.executemany(_UPSERT_CLEAN, clean_rows)
            if reject_rows:
                cur.executemany(_UPSERT_REJECT, reject_rows)

    def load_day(self, day: date) -> tuple[list[dict], list[dict]]:
        with connect(self.dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM articles_propres WHERE jour_collecte = %s ORDER BY id", (day,)
            )
            clean = cur.fetchall()
            cur.execute("SELECT * FROM rejets WHERE jour_collecte = %s ORDER BY id", (day,))
            rejects = cur.fetchall()
        return clean, rejects


# -- En mémoire (tests) ------------------------------------------------------------------------
class InMemoryCleanStore:
    def __init__(self) -> None:
        self.clean: dict[str, dict] = {}
        self.rejects: dict[str, dict] = {}

    def save_day(self, day: date, kept: Sequence[Item], rejected: Sequence[Rejected]) -> None:
        for r in rejected:
            self.clean.pop(r.item["id"], None)
            self.rejects[r.item["id"]] = _reject_row(day, r)
        for item in kept:
            self.rejects.pop(item["id"], None)
            self.clean[item["id"]] = _clean_row(day, item)

    def load_day(self, day: date) -> tuple[list[dict], list[dict]]:
        def of_day(rows: dict[str, dict]) -> list[dict]:
            return sorted(
                (r for r in rows.values() if r["jour_collecte"] == day), key=lambda r: r["id"]
            )

        return of_day(self.clean), of_day(self.rejects)
