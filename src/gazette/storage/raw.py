"""Stockage brut, organisé par jour de collecte (Europe/Paris) :

    data/raw/<AAAA-MM-JJ>/_manifest.json
    data/raw/<AAAA-MM-JJ>/<pqr|pqn>/<slug>.json

Le brut ne filtre rien (ni âge, ni HTML) : c'est le rôle de l'étape de nettoyage.
Les résultats intermédiaires par source (utilisés pour construire le manifeste)
vivent hors de l'arborescence brute, dans data/state/runs/<date>/<slug>.json.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from gazette.collect.parser import Item
from gazette.sources import Source
from gazette.storage.atomic import atomic_write_json, file_lock, read_json

MANIFEST_NAME = "_manifest.json"

Status = Literal["ok", "not_modified", "failed", "sleeping"]


class SourceResult(BaseModel):
    """Bilan de la collecte d'une source pour un jour donné."""

    source: str
    slug: str
    type: str
    feed_url: str
    file: str
    status: Status
    http_status: int | None = None
    received: int = 0  # entrées présentes dans le flux
    new_items: int = 0  # articles écrits (jamais vus auparavant)
    ignored: int = 0  # reçus mais non écrits : déjà vus, doublons, sans lien
    items_total: int = 0
    error: str | None = None
    retryable: bool = False
    # Valeur de `echecs_consecutifs` après cette collecte (None si non comptée).
    consecutive_failures: int | None = None
    started_at: str
    duration_s: float


def day_dir(raw_dir: Path, collection_date: date) -> Path:
    return Path(raw_dir) / collection_date.isoformat()


def relative_source_file(source: Source) -> str:
    return f"{source.subdir}/{source.slug}.json"


def source_file(raw_dir: Path, collection_date: date, source: Source) -> Path:
    return day_dir(raw_dir, collection_date) / source.subdir / f"{source.slug}.json"


def manifest_file(raw_dir: Path, collection_date: date) -> Path:
    return day_dir(raw_dir, collection_date) / MANIFEST_NAME


def store_items(
    path: Path,
    source: Source,
    collection_date: date,
    *,
    fetched_at: str,
    http_status: int,
    items: Iterable[Item],
) -> tuple[int, int]:
    """Fusionne `items` dans le fichier de la source, sans doublon (clé : `id`).

    Chaque appel ajoute une entrée dans `fetches` ; rien n'est écrasé.
    Renvoie (nombre de nouveaux items, nombre total d'items du fichier).
    """
    with file_lock(path):
        doc = read_json(path) or {
            "source": source.name,
            "slug": source.slug,
            "type": source.type,
            "feed_url": source.feed_url,
            "collection_date": collection_date.isoformat(),
            "fetches": [],
            "items": [],
        }
        doc["feed_url"] = source.feed_url
        known = {item["id"] for item in doc["items"]}
        new_items = []
        for item in items:
            if item.id not in known:
                known.add(item.id)
                new_items.append(item.model_dump())
        doc["items"].extend(new_items)
        doc["fetches"].append(
            {"fetched_at": fetched_at, "http_status": http_status, "new_items": len(new_items)}
        )
        atomic_write_json(path, doc)
    return len(new_items), len(doc["items"])


def count_items(path: Path) -> int:
    doc = read_json(path)
    return len(doc["items"]) if doc else 0


# -- résultats par source et manifeste --------------------------------------
def _result_path(state_dir: Path, collection_date: date, slug: str) -> Path:
    return Path(state_dir) / "runs" / collection_date.isoformat() / f"{slug}.json"


def save_source_result(state_dir: Path, collection_date: date, result: SourceResult) -> None:
    atomic_write_json(_result_path(state_dir, collection_date, result.slug), result.model_dump())


def load_source_result(state_dir: Path, collection_date: date, slug: str) -> SourceResult | None:
    data = read_json(_result_path(state_dir, collection_date, slug))
    return SourceResult(**data) if data else None


def write_manifest(
    raw_dir: Path,
    state_dir: Path,
    collection_date: date,
    sources: list[Source],
    *,
    generated_at: str,
    timezone: str,
) -> dict:
    """Écrit `_manifest.json` : un bilan par source active, plus un résumé.

    Une source en sommeil est signalée comme telle (statut `sleeping`) ; une source
    sans résultat (tâche jamais exécutée ou tuée) est marquée en échec.
    """
    entries = []
    for source in sources:
        result = load_source_result(state_dir, collection_date, source.slug)
        path = source_file(raw_dir, collection_date, source)
        if source.sleeping:
            entry = {
                "source": source.name,
                "slug": source.slug,
                "type": source.type,
                "feed_url": source.feed_url,
                "file": relative_source_file(source),
                "status": "sleeping",
                "http_status": None,
                "new_items": 0,
                "items_total": count_items(path),
                "error": (
                    f"en sommeil après {source.consecutive_failures} échecs consécutifs ; "
                    f"réveil : python -m gazette sources wake {source.slug}"
                ),
                "started_at": None,
                "duration_s": None,
                "consecutive_failures": source.consecutive_failures,
            }
        elif result is None:
            entry = {
                "source": source.name,
                "slug": source.slug,
                "type": source.type,
                "feed_url": source.feed_url,
                "file": relative_source_file(source),
                "status": "failed",
                "http_status": None,
                "new_items": 0,
                "items_total": count_items(path),
                "error": "aucun résultat : collecte non exécutée ou interrompue",
                "started_at": None,
                "duration_s": None,
                "consecutive_failures": source.consecutive_failures,
            }
        else:
            entry = result.model_dump(exclude={"retryable"})
            # Le total peut avoir évolué depuis (autre passage) : on relit le fichier.
            entry["items_total"] = count_items(path)
        entries.append(entry)

    statuses = [e["status"] for e in entries]
    manifest = {
        "collection_date": collection_date.isoformat(),
        "generated_at": generated_at,
        "timezone": timezone,
        "summary": {
            "sources": len(entries),
            "ok": statuses.count("ok"),
            "not_modified": statuses.count("not_modified"),
            "failed": statuses.count("failed"),
            "sleeping": statuses.count("sleeping"),
            "new_items": sum(e["new_items"] for e in entries),
            "items_total": sum(e["items_total"] for e in entries),
        },
        "sources": entries,
    }
    atomic_write_json(manifest_file(raw_dir, collection_date), manifest)
    return manifest
