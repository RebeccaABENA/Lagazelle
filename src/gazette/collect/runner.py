"""Collecte d'une source, puis de toutes les sources (utilisé par la CLI et par le DAG)."""

from __future__ import annotations

import logging
import time
from datetime import date

from gazette.collect.fetcher import FetchError, PoliteFetcher
from gazette.collect.parser import parse_feed
from gazette.config import Settings
from gazette.sources import Source, load_sources
from gazette.storage.raw import (
    SourceResult,
    count_items,
    relative_source_file,
    save_source_result,
    source_file,
    store_items,
    write_manifest,
)

log = logging.getLogger(__name__)


def collect_source(
    source: Source,
    settings: Settings,
    collection_date: date,
    fetcher: PoliteFetcher,
) -> SourceResult:
    """Collecte une source et stocke ses items. Ne lève jamais : un échec est renvoyé
    (status="failed") et journalisé, pour ne pas bloquer les autres sources."""
    started_at = settings.now()
    start = time.monotonic()
    path = source_file(settings.raw_dir, collection_date, source)
    fields = {
        "source": source.name,
        "slug": source.slug,
        "type": source.type,
        "feed_url": source.feed_url,
        "file": relative_source_file(source),
        "started_at": started_at.isoformat(),
    }
    try:
        # GET conditionnel seulement si le fichier du jour existe déjà : le premier
        # passage d'une journée récupère toujours le flux complet.
        fetched = fetcher.fetch(source.feed_url, conditional=path.exists())
        items = [] if fetched.not_modified else parse_feed(fetched.content, fetched.fetched_at)
        new_items, total = store_items(
            path,
            source,
            collection_date,
            fetched_at=fetched.fetched_at.isoformat(),
            http_status=fetched.status,
            items=items,
        )
        fetcher.remember(fetched)
        result = SourceResult(
            **fields,
            status="not_modified" if fetched.not_modified else "ok",
            http_status=fetched.status,
            new_items=new_items,
            items_total=total,
            duration_s=round(time.monotonic() - start, 3),
        )
        log.info(
            "%s : %s, %d nouveaux items (%d au total)",
            source.slug,
            result.status,
            new_items,
            total,
        )
    except Exception as exc:  # une source en panne ne doit jamais arrêter les autres
        if isinstance(exc, FetchError):
            log.error("%s : échec de la collecte : %s", source.slug, exc)
        else:
            log.exception("%s : échec de la collecte", source.slug)
        result = SourceResult(
            **fields,
            status="failed",
            http_status=getattr(exc, "status", None),
            items_total=count_items(path),
            error=f"{type(exc).__name__}: {exc}",
            retryable=exc.retryable if isinstance(exc, FetchError) else True,
            duration_s=round(time.monotonic() - start, 3),
        )
    save_source_result(settings.state_dir, collection_date, result)
    return result


def finalize(settings: Settings, collection_date: date) -> dict:
    """Écrit le manifeste du jour pour toutes les sources actives."""
    return write_manifest(
        settings.raw_dir,
        settings.state_dir,
        collection_date,
        load_sources(settings.sources_file),
        generated_at=settings.now().isoformat(),
        timezone=settings.timezone,
    )


def run_collection(
    settings: Settings,
    collection_date: date | None = None,
    slugs: list[str] | None = None,
    fetcher: PoliteFetcher | None = None,
) -> dict:
    """Collecte séquentielle de toutes les sources actives, puis manifeste."""
    collection_date = collection_date or settings.today()
    sources = load_sources(settings.sources_file)
    if slugs:
        unknown = set(slugs) - {s.slug for s in sources}
        if unknown:
            raise KeyError(f"Sources inconnues ou inactives : {sorted(unknown)}")
        sources = [s for s in sources if s.slug in slugs]

    owned = fetcher is None
    fetcher = fetcher or PoliteFetcher(settings)
    try:
        for source in sources:
            collect_source(source, settings, collection_date, fetcher)
    finally:
        if owned:
            fetcher.close()
    return finalize(settings, collection_date)
