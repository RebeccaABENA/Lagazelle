"""Outils communs aux tests du nettoyage : écrire un jour de brut sur disque."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from gazette.config import Settings
from gazette.sources import slugify
from gazette.storage.atomic import atomic_write_json

FIXTURES = Path(__file__).parent / "fixtures"
NATIONAL = {"Le Monde", "Le Figaro", "Libération", "L'Obs"}


def load_sample() -> dict:
    """29 vrais articles de la collecte du 2026-11-12, et le verdict attendu de chacun."""
    return json.loads((FIXTURES / "rules_sample.json").read_text(encoding="utf-8"))


def write_raw_day(settings: Settings, items: list[dict], day: date) -> None:
    """Range les articles comme le fait la collecte : un fichier par source, plus le manifeste."""
    files: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in items:
        subdir = "pqn" if item["source"] in NATIONAL else "pqr"
        files[(subdir, slugify(item["source"]))].append(item)
    day_dir = settings.raw_dir / day.isoformat()
    for (subdir, slug), group in files.items():
        document = {"source": group[0]["source"], "slug": slug, "items": group}
        atomic_write_json(day_dir / subdir / f"{slug}.json", document)
    atomic_write_json(day_dir / "_manifest.json", {"summary": {}, "sources": []})
