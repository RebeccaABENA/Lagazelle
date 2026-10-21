"""Ligne de commande : python -m gazette collect [--date AAAA-MM-JJ] [--source SLUG ...]"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from gazette.collect.runner import run_collection
from gazette.config import ConfigError, load_settings


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    # Les journaux httpx (une ligne par requête) sont utiles mais bavards.
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)

    settings = load_settings()
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
