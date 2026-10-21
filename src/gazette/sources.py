"""Lecture et validation de sources.csv (séparateur `;`, UTF-8)."""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

REQUIRED_COLUMNS = ("nom", "type", "region", "acces", "conditions", "notes", "active")


def slugify(name: str) -> str:
    """'La Dépêche du Midi' -> 'la-depeche-du-midi' ; "L'Obs" -> 'l-obs'."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


class Source(BaseModel):
    name: str
    type: Literal["PQR", "PQN"]
    region: str = ""
    feed_url: str
    conditions: str = ""
    notes: str = ""
    active: bool = True

    @field_validator("type", mode="before")
    @classmethod
    def _upper_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("feed_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"URL de flux invalide : {value!r}")
        return value

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def subdir(self) -> str:
        """Sous-dossier du stockage brut : 'pqr' ou 'pqn'."""
        return self.type.lower()


class SourcesError(ValueError):
    """sources.csv mal formé."""


def _parse_active(value: str) -> bool:
    value = (value or "").strip().lower()
    if value in ("", "true", "1", "oui", "yes"):
        return True
    if value in ("false", "0", "non", "no"):
        return False
    raise SourcesError(f"Valeur 'active' invalide : {value!r}")


def load_sources(path: str | Path, *, include_inactive: bool = False) -> list[Source]:
    """Charge et valide les sources. Par défaut, seules les sources actives sont renvoyées."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise SourcesError(f"Colonnes manquantes dans {path} : {sorted(missing)}")
        sources = []
        for line_no, row in enumerate(reader, start=2):
            try:
                sources.append(
                    Source(
                        name=row["nom"].strip(),
                        type=row["type"],
                        region=row["region"].strip(),
                        feed_url=row["acces"],
                        conditions=row["conditions"].strip(),
                        notes=row["notes"].strip(),
                        active=_parse_active(row["active"]),
                    )
                )
            except ValueError as exc:
                raise SourcesError(f"{path}, ligne {line_no} : {exc}") from exc

    slugs = [s.slug for s in sources]
    duplicates = {s for s in slugs if slugs.count(s) > 1}
    if duplicates:
        raise SourcesError(f"Slugs en double dans {path} : {sorted(duplicates)}")
    return sources if include_inactive else [s for s in sources if s.active]


def get_source(sources: list[Source], slug: str) -> Source:
    for source in sources:
        if source.slug == slug:
            return source
    raise KeyError(f"Source inconnue : {slug}")
