"""Pipeline de nettoyage : brut -> normalisation -> règles -> tables propres et rejets -> rapport.

    python -m gazette clean [--date AAAA-MM-JJ]

Le nettoyage traite **tous les fichiers bruts d'un jour de collecte**, pas seulement ce qui est
arrivé depuis le dernier passage : le même jour, rejoué, donne le même résultat (aucun doublon,
même rapport). C'est ce qui permet de le lancer après chaque collecte, ou de le relancer à la main.
Un article n'apparaît que dans un seul fichier brut (la déduplication de la collecte y veille),
donc chaque article est jugé une seule fois par jour.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from gazette.clean.normalize import normalize_item
from gazette.clean.report import build_report, summary_line, write_report
from gazette.clean.rules import RulesConfig, apply_rules
from gazette.config import Settings
from gazette.db.clean_repository import CleanStore
from gazette.storage.atomic import read_json

log = logging.getLogger(__name__)


def default_clean_store(settings: Settings) -> CleanStore:
    from gazette.db import database_url
    from gazette.db.clean_repository import PostgresCleanStore

    return PostgresCleanStore(database_url(settings))


def read_raw_items(raw_dir: Path, day: date) -> list[dict]:
    """Articles bruts de tous les fichiers du jour, chacun avec `_fichier` : son fichier d'origine.

    `_fichier` est relatif à `raw_dir` (ex. `2026-11-12/pqr/sud-ouest.json`) : c'est le lien de
    traçabilité entre un article propre et le brut. Le manifeste du jour n'est pas lu.
    """
    raw_dir = Path(raw_dir)
    items: list[dict] = []
    for path in sorted((raw_dir / day.isoformat()).glob("*/*.json")):
        document = read_json(path) or {}
        origin = path.relative_to(raw_dir).as_posix()
        items.extend({**item, "_fichier": origin} for item in document.get("items", []))
    return items


def run_cleaning(
    settings: Settings,
    day: date | None = None,
    *,
    store: CleanStore | None = None,
    config: RulesConfig | None = None,
    now: datetime | None = None,
    write_files: bool = True,
) -> dict:
    """Nettoie le brut d'un jour et renvoie le rapport de qualité (aussi archivé sur disque)."""
    day = day or settings.today()
    now = now or settings.now()
    config = config or RulesConfig()

    raw = read_raw_items(settings.raw_dir, day)
    if not raw:
        log.warning("nettoyage jour=%s : aucun article brut à nettoyer", day)
    result = apply_rules([normalize_item(item) for item in raw], config=config, now=now)

    (store or default_clean_store(settings)).save_day(day, result.kept, result.rejected)

    report = build_report(day, result, config=config, generated_at=now)
    if write_files:
        write_report(settings.reports_dir, report)
    log.info(summary_line(report))
    return report
