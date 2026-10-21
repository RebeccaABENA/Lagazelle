"""Collecte d'une source, puis de toutes les sources (utilisé par la CLI et par le DAG)."""

from __future__ import annotations

import logging
import time
from datetime import date

from gazette.collect.fetcher import FetchError, PoliteFetcher
from gazette.collect.parser import ParseError, parse_feed
from gazette.config import Settings
from gazette.db.repository import ArticleIndex, SourceRepository
from gazette.sources import SLEEP_THRESHOLD, Source
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


def default_repository(settings: Settings) -> SourceRepository:
    from gazette.db import database_url
    from gazette.db.repository import PostgresSourceRepository

    return PostgresSourceRepository(database_url(settings))


def default_index(settings: Settings) -> ArticleIndex:
    from gazette.db import database_url
    from gazette.db.repository import PostgresArticleIndex

    return PostgresArticleIndex(database_url(settings))


def active_sources(repo: SourceRepository) -> list[Source]:
    """Sources actives en base, y compris celles en sommeil (pour le manifeste)."""
    return [s for s in repo.all() if s.active]


def collectable_sources(repo: SourceRepository) -> list[Source]:
    """Sources à interroger : actives et pas en sommeil."""
    return [s for s in active_sources(repo) if not s.sleeping]


def is_retryable(exc: Exception) -> bool:
    """Erreur transitoire (réseau, 429, 5xx) : vaut la peine de relancer.
    Erreur permanente (robots.txt, 404, flux illisible) : non."""
    if isinstance(exc, FetchError):
        return exc.retryable
    return not isinstance(exc, ParseError)


def collect_source(
    source: Source,
    settings: Settings,
    collection_date: date,
    fetcher: PoliteFetcher,
    repo: SourceRepository,
    index: ArticleIndex,
    *,
    final_attempt: bool = True,
) -> SourceResult:
    """Collecte une source et stocke ses items. Ne lève jamais : un échec est renvoyé
    (status="failed") et journalisé, pour ne pas bloquer les autres sources.

    Un échec n'est compté dans `echecs_consecutifs` qu'une fois par collecte : au
    dernier essai (`final_attempt`, cf. relances Airflow), ou tout de suite s'il est
    permanent, puisqu'il ne sera pas relancé.
    """
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
        # GET conditionnel avec les validateurs mémorisés en base.
        fetched = fetcher.fetch(
            source.feed_url, etag=source.etag, last_modified=source.last_modified
        )
        received, items = 0, []
        if not fetched.not_modified:
            parsed = parse_feed(fetched.content, fetched.fetched_at, source.name)
            received = parsed.received
            # Déduplication sur plusieurs jours : un article déjà écrit un jour
            # précédent (ou plus tôt aujourd'hui) n'est pas réécrit.
            fresh = index.unseen([item.id for item in parsed.items])
            items = [item for item in parsed.items if item.id in fresh]
        # Même sur un 304, le passage est noté dans `fetches`.
        new_items, total = store_items(
            path,
            source,
            collection_date,
            fetched_at=fetched.fetched_at.isoformat(),
            http_status=fetched.status,
            items=items,
        )
        # Index mis à jour après l'écriture du fichier : un plantage entre les deux
        # fait au pire réécrire l'article au passage suivant, jamais le perdre.
        index.add(source.name, [item.id for item in items], collection_date)
        # Validateurs enregistrés seulement une fois les items stockés : si le stockage
        # échoue, la collecte suivante refait un GET complet au lieu de recevoir un 304.
        repo.record_success(
            source.name,
            at=fetched.fetched_at,
            etag=fetched.etag,
            last_modified=fetched.last_modified,
        )
        result = SourceResult(
            **fields,
            consecutive_failures=0,
            status="not_modified" if fetched.not_modified else "ok",
            http_status=fetched.status,
            received=received,
            new_items=new_items,
            ignored=received - new_items,
            items_total=total,
            duration_s=round(time.monotonic() - start, 3),
        )
    except Exception as exc:  # une source en panne ne doit jamais arrêter les autres
        retryable = is_retryable(exc)
        if not isinstance(exc, (FetchError, ParseError)):
            # Erreur inattendue (bug, disque…) : trace complète en plus de la ligne de bilan.
            log.exception("%s : erreur inattendue pendant la collecte", source.slug)
        failures = None
        if final_attempt or not retryable:
            failures = _count_failure(repo, source)
        result = SourceResult(
            **fields,
            status="failed",
            http_status=getattr(exc, "status", None),
            items_total=count_items(path),
            error=f"{type(exc).__name__}: {exc}",
            retryable=retryable,
            consecutive_failures=failures,
            duration_s=round(time.monotonic() - start, 3),
        )
    log.log(logging.ERROR if result.status == "failed" else logging.INFO, summary_line(result))
    save_source_result(settings.state_dir, collection_date, result)
    return result


def summary_line(result: SourceResult) -> str:
    """Une ligne de bilan par source et par passage, au format clé=valeur :

    source=le-monde statut=ok http=200 recus=16 ecrits=3 ignores=13 duree=0.42s
    """
    parts = [
        f"source={result.slug}",
        f"statut={result.status}",
        f"http={result.http_status if result.http_status is not None else '-'}",
        f"recus={result.received}",
        f"ecrits={result.new_items}",
        f"ignores={result.ignored}",
        f"duree={result.duration_s:.2f}s",
    ]
    if result.status == "failed":
        parts.append(f"erreur_type={'transitoire' if result.retryable else 'permanente'}")
        if result.consecutive_failures is not None:
            parts.append(f"echecs_consecutifs={result.consecutive_failures}")
        parts.append(f'erreur="{result.error}"')
    return " ".join(parts)


def _count_failure(repo: SourceRepository, source: Source) -> int | None:
    try:
        failures = repo.record_failure(source.name)
    except Exception:  # base indisponible : ne pas masquer l'échec d'origine
        log.exception("%s : impossible d'enregistrer l'échec en base", source.slug)
        return None
    if failures == SLEEP_THRESHOLD:
        log.warning(
            "%s : mise en sommeil après %d échecs consécutifs ; "
            "réveil : python -m gazette sources wake %s",
            source.slug,
            failures,
            source.slug,
        )
    return failures


def finalize(settings: Settings, collection_date: date, repo: SourceRepository) -> dict:
    """Écrit le manifeste du jour : sources actives, y compris celles en sommeil."""
    sources = active_sources(repo)
    for source in sources:
        if source.sleeping:
            log.warning(
                "%s : en sommeil (%d échecs consécutifs) ; réveil : "
                "python -m gazette sources wake %s",
                source.slug,
                source.consecutive_failures,
                source.slug,
            )
    return write_manifest(
        settings.raw_dir,
        settings.state_dir,
        collection_date,
        sources,
        generated_at=settings.now().isoformat(),
        timezone=settings.timezone,
    )


def run_collection(
    settings: Settings,
    collection_date: date | None = None,
    slugs: list[str] | None = None,
    fetcher: PoliteFetcher | None = None,
    repo: SourceRepository | None = None,
    index: ArticleIndex | None = None,
) -> dict:
    """Collecte séquentielle de toutes les sources actives, puis manifeste."""
    collection_date = collection_date or settings.today()
    repo = repo or default_repository(settings)
    index = index or default_index(settings)
    sources = collectable_sources(repo)
    if slugs:
        unknown = set(slugs) - {s.slug for s in sources}
        if unknown:
            raise KeyError(f"Sources inconnues, inactives ou en sommeil : {sorted(unknown)}")
        sources = [s for s in sources if s.slug in slugs]

    owned = fetcher is None
    fetcher = fetcher or PoliteFetcher(settings)
    try:
        for source in sources:
            collect_source(source, settings, collection_date, fetcher, repo, index)
    finally:
        if owned:
            fetcher.close()
    return finalize(settings, collection_date, repo)
