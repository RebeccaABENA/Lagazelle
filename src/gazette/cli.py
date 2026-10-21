"""Ligne de commande.

python -m gazette collect [--date AAAA-MM-JJ] [--source SLUG ...] [--strict]
python -m gazette db migrate
python -m gazette db seed [--csv sources.csv]
python -m gazette sources list
python -m gazette sources wake SLUG [SLUG ...] | --all
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from gazette.config import ConfigError, Settings, load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gazette", description="La gazette")
    parser.add_argument("-v", "--verbose", action="store_true", help="journaux détaillés")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="collecte les flux et écrit le stockage brut")
    collect.add_argument(
        "--date",
        type=date.fromisoformat,
        help="jour de collecte AAAA-MM-JJ (défaut : aujourd'hui, heure de Paris)",
    )
    collect.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="SLUG",
        help="limiter à une source (option répétable)",
    )
    collect.add_argument(
        "--strict",
        action="store_true",
        help="code retour 1 dès qu'une source échoue (défaut : seulement si toutes échouent)",
    )

    db = sub.add_parser("db", help="base gazette : migrations et amorçage")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("migrate", help="applique les migrations SQL manquantes")
    seed = db_sub.add_parser("seed", help="amorce la table sources depuis le CSV")
    seed.add_argument("--csv", type=Path, help="fichier CSV (défaut : GAZETTE_SOURCES_FILE)")

    sources = sub.add_parser("sources", help="état des sources en base")
    sources_sub = sources.add_subparsers(dest="sources_command", required=True)
    sources_sub.add_parser("list", help="liste les sources et leur état de collecte")
    wake = sources_sub.add_parser("wake", help="réveille des sources en sommeil")
    wake.add_argument("slugs", nargs="*", metavar="SLUG")
    wake.add_argument("--all", action="store_true", help="toutes les sources en sommeil")
    return parser


def _cmd_collect(args: argparse.Namespace, settings: Settings) -> int:
    from gazette.collect.runner import run_collection

    try:
        settings.user_agent  # noqa: B018 - vérifie l'e-mail de contact avant tout appel réseau
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2

    manifest = run_collection(settings, args.date, args.sources)

    print(f"\nCollecte du {manifest['collection_date']}")
    for entry in manifest["sources"]:
        line = f"  {entry['status']:<13} {entry['file']:<32} +{entry['new_items']:<4}"
        line += f" total {entry['items_total']}"
        if entry["error"]:
            line += f"  ({entry['error']})"
        print(line)
    summary = manifest["summary"]
    print(
        f"  => {summary['ok']} ok, {summary['not_modified']} non modifiées, "
        f"{summary['failed']} en échec ; {summary['new_items']} nouveaux items."
    )
    if summary["failed"] and (args.strict or summary["failed"] == summary["sources"]):
        return 1
    return 0


def _cmd_db(args: argparse.Namespace, settings: Settings) -> int:
    from gazette.db import connect, database_url

    dsn = database_url(settings)
    if args.db_command == "migrate":
        from gazette.db.migrate import migrate

        with connect(dsn) as conn:
            applied = migrate(conn)
        print("Migrations appliquées : " + (", ".join(applied) if applied else "aucune (à jour)"))
        return 0

    from gazette.db.repository import PostgresSourceRepository
    from gazette.sources import load_sources

    csv_path = args.csv or settings.sources_file
    report = PostgresSourceRepository(dsn).seed(load_sources(csv_path, include_inactive=True))
    print(
        f"Sources depuis {csv_path} : {len(report.inserted)} ajoutées, "
        f"{len(report.updated)} mises à jour (type, URL)."
    )
    return 0


def _cmd_sources(args: argparse.Namespace, settings: Settings) -> int:
    from gazette.collect.runner import default_repository

    repo = default_repository(settings)
    if args.sources_command == "wake":
        slugs = [s.slug for s in repo.all() if s.sleeping] if args.all else args.slugs
        if not slugs:
            print("Aucune source à réveiller.")
            return 0 if args.all else 2
        for slug in slugs:
            source = repo.wake(slug)
            print(f"{slug} : réveillée (échecs consécutifs = {source.consecutive_failures})")
        return 0

    print(f"{'slug':<20} {'type':<4} {'état':<10} {'échecs':>6}  {'dernière réussite':<25} etag")
    for s in repo.all():
        last = s.last_success.isoformat(timespec="seconds") if s.last_success else "-"
        print(
            f"{s.slug:<20} {s.type:<4} {s.state:<10} {s.consecutive_failures:>6}  "
            f"{last:<25} {'oui' if s.etag else '-'}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    # Les journaux httpx (une ligne par requête) sont utiles mais bavards.
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)

    settings = load_settings()
    handlers = {"collect": _cmd_collect, "db": _cmd_db, "sources": _cmd_sources}
    try:
        return handlers[args.command](args, settings)
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2
