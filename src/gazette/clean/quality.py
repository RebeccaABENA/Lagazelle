"""Après le nettoyage : contrôles de qualité, métriques, alerte, et purge (rétention).

    python -m gazette check [--date AAAA-MM-JJ] [--no-alert]
    python -m gazette alert-test
    python -m gazette purge [--dry-run]

Le DAG appelle `run_quality_checks` puis `run_purge` après `pipeline.run_cleaning`, à chaque
passage (toutes les 30 min). Les contrôles relisent ce que le nettoyage a écrit (même
`CleanStore`), complètent le rapport du jour (section « Contrôles de qualité ») et publient
les chiffres au format Prometheus pour le TD 8. Une même alerte ne part qu'une fois par jour.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from gazette.alert import format_check_alert, send_alert
from gazette.clean.checks import ChecksConfig, DayFacts, run_checks, summarize
from gazette.clean.report import render_markdown
from gazette.config import Settings
from gazette.db.clean_repository import CleanStore
from gazette.db.quality_repository import ChecksHistory
from gazette.storage.atomic import atomic_write_json, read_json
from gazette.storage.raw import MANIFEST_NAME

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Retention:
    """Durées de conservation, en jours (docs/donnees.md § 7)."""

    brut_jours: int = 90  # data/articles_bruts/<date>/, data/state/runs|alertes/
    rejets_jours: int = 30  # table rejets
    articles_jours: int = 365  # tables articles_propres et controles
    rapports_jours: int = 365  # data/rapports/


RETENTION = Retention()


# -- Contrôles ----------------------------------------------------------------------------
def _default_store(settings: Settings) -> CleanStore:
    from gazette.clean.pipeline import default_clean_store

    return default_clean_store(settings)


def _default_history(settings: Settings) -> ChecksHistory:
    from gazette.db import database_url
    from gazette.db.quality_repository import PostgresChecksHistory

    return PostgresChecksHistory(database_url(settings))


def run_quality_checks(
    settings: Settings,
    day: date | None = None,
    *,
    store: CleanStore | None = None,
    history: ChecksHistory | None = None,
    config: ChecksConfig | None = None,
    alert: bool = True,
    checked_at: datetime | None = None,
) -> dict:
    """Vérifie les faits du jour, les historise, publie les métriques et alerte si besoin.

    Renvoie le résumé : `ok`, `critiques_en_echec`, `avertissements`, `resultats`,
    `alerte_envoyee`.
    """
    day = day or settings.today()
    checked_at = checked_at or settings.now()
    config = config or ChecksConfig()

    clean, rejects = (store or _default_store(settings)).load_day(day)
    manifest = read_json(settings.raw_dir / day.isoformat() / MANIFEST_NAME) or {}
    facts = DayFacts(
        clean=clean,
        rejects=rejects,
        collect_failed=[
            s.get("slug") or s.get("source")
            for s in manifest.get("sources", [])
            if s.get("status") == "failed"
        ],
        # La journée se remplit au fil des passages : le volume n'est jugé qu'à partir de
        # `volume_heure_min`, ou pour un jour déjà terminé.
        volume_evaluable=day < checked_at.date() or checked_at.hour >= config.volume_heure_min,
    )
    summary = summarize(run_checks(facts, config), checked_at)
    (history or _default_history(settings)).save(day, summary)

    report_md = _complete_report(settings, day, summary, checked_at)

    failed = [r["id"] for r in summary["resultats"] if not r["ok"]]
    log.info("contrôles jour=%s echecs=%s", day, ",".join(failed) or "aucun")
    summary["alerte_envoyee"] = False
    if failed and alert:
        if _already_alerted(settings, day, failed):
            log.info("Alerte déjà envoyée aujourd'hui pour %s : pas de nouvel envoi.", failed)
        else:
            message = format_check_alert(day.isoformat(), summary, str(report_md or ""))
            summary["alerte_envoyee"] = send_alert(settings.alert_webhook_url, message)
            if summary["alerte_envoyee"]:
                _remember_alert(settings, day, failed, checked_at.isoformat())
    return summary


def _alert_state(settings: Settings, day: date) -> Path:
    return settings.state_dir / "alertes" / f"{day.isoformat()}.json"


def _already_alerted(settings: Settings, day: date, failed: list[str]) -> bool:
    """On ne réalerte que si la liste des contrôles en échec a changé depuis la dernière
    alerte de la journée (le DAG passe toutes les 30 min)."""
    state = read_json(_alert_state(settings, day)) or {}
    return sorted(state.get("controles", [])) == sorted(failed)


def _remember_alert(settings: Settings, day: date, failed: list[str], at: str) -> None:
    atomic_write_json(_alert_state(settings, day), {"controles": failed, "le": at})


# -- Rapport et métriques ------------------------------------------------------------------
def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _escape(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_checks_markdown(summary: dict) -> str:
    failed = [c for c in summary["resultats"] if not c["ok"]]
    verdict = "✅ tous les contrôles passent" if not failed else f"❌ {len(failed)} en échec"
    lines = [
        "## Contrôles de qualité",
        "",
        f"{verdict} (vérifié le {summary['verifie_le']}).",
        "",
        "| Contrôle | Gravité | Résultat | Valeur | Seuil | Détail |",
        "|---|---|---|---|---|---|",
    ]
    for c in summary["resultats"]:
        lines.append(
            f"| `{c['id']}` {_escape(c['description'])} | {c['gravite']} | "
            f"{'✅' if c['ok'] else '❌'} | {c['valeur']} | {c['seuil']} | {_escape(c['detail'])} |"
        )
    return "\n".join(lines) + "\n"


def _complete_report(
    settings: Settings, day: date, summary: dict, checked_at: datetime
) -> Path | None:
    """Ajoute les contrôles au rapport du nettoyage (JSON et Markdown) et écrit les métriques."""
    reports = settings.reports_dir
    json_path = reports / f"qualite-{day.isoformat()}.json"
    report = read_json(json_path)
    if report is None:
        log.warning("Pas de rapport de nettoyage pour le %s : métriques non écrites.", day)
        return None
    report["controles"] = summary
    md_path = reports / f"qualite-{day.isoformat()}.md"
    _write_text(md_path, render_markdown(report) + "\n" + render_checks_markdown(summary))
    _write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _write_text(settings.metrics_file, render_metrics(report, checked_at.timestamp()))
    return md_path


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def render_metrics(report: dict[str, Any], timestamp: float) -> str:
    """Métriques au format texte Prometheus (textfile collector de node_exporter, TD 8).

    Pas de date en label : on expose le dernier passage ; l'historique est dans Prometheus
    et dans la table `controles`.
    """
    out = [
        "# HELP gazette_qualite_dernier_passage_timestamp_seconds Fin du dernier passage qualité.",
        "# TYPE gazette_qualite_dernier_passage_timestamp_seconds gauge",
        f"gazette_qualite_dernier_passage_timestamp_seconds {timestamp:.0f}",
        "# HELP gazette_qualite_articles Articles du dernier jour traité, par statut.",
        "# TYPE gazette_qualite_articles gauge",
        f'gazette_qualite_articles{{statut="entres"}} {report["entres"]}',
        f'gazette_qualite_articles{{statut="gardes"}} {report["gardes"]}',
        f'gazette_qualite_articles{{statut="rejetes"}} {report["rejetes"]}',
        "# HELP gazette_qualite_taux_rejet Taux de rejet global du dernier jour traité.",
        "# TYPE gazette_qualite_taux_rejet gauge",
        f"gazette_qualite_taux_rejet {report['taux_rejet']}",
        "# HELP gazette_qualite_rejets_regle Rejets par règle.",
        "# TYPE gazette_qualite_rejets_regle gauge",
    ]
    out += [
        f'gazette_qualite_rejets_regle{{regle="{_label(rule)}"}} {stats["rejetes"]}'
        for rule, stats in report["par_regle"].items()
    ]
    out += [
        "# HELP gazette_qualite_taux_rejet_source Taux de rejet par source.",
        "# TYPE gazette_qualite_taux_rejet_source gauge",
    ]
    out += [
        f'gazette_qualite_taux_rejet_source{{source="{_label(s)}"}} {v["taux_rejet"]}'
        for s, v in sorted(report["par_source"].items())
    ]
    controles = report.get("controles")
    if controles:
        out += [
            "# HELP gazette_qualite_controle_ok 1 si le contrôle passe, 0 sinon.",
            "# TYPE gazette_qualite_controle_ok gauge",
        ]
        out += [
            f'gazette_qualite_controle_ok{{controle="{_label(c["id"])}",'
            f'gravite="{c["gravite"]}"}} {int(c["ok"])}'
            for c in controles["resultats"]
        ]
    return "\n".join(out) + "\n"


# -- Purge ---------------------------------------------------------------------------------
def run_purge(
    settings: Settings,
    today: date | None = None,
    *,
    dry_run: bool = False,
    retention: Retention = RETENTION,
    purge_database: bool = True,
) -> dict:
    """Applique la rétention : fichiers datés trop vieux, puis lignes en base."""
    today = today or settings.today()
    removed: dict[str, Any] = {"brut": [], "etat": [], "alertes": [], "rapports": []}

    def old(name: str, days: int) -> bool:
        try:
            stem = name.removesuffix(".json").removesuffix(".md").removeprefix("qualite-")
            return date.fromisoformat(stem) < today - timedelta(days=days)
        except ValueError:
            return False  # pas une date : on n'y touche pas

    targets = (
        ("brut", settings.raw_dir, retention.brut_jours),
        ("etat", settings.state_dir / "runs", retention.brut_jours),
        ("alertes", settings.state_dir / "alertes", retention.brut_jours),
        ("rapports", settings.reports_dir, retention.rapports_jours),
    )
    for key, root, days in targets:
        for path in sorted(root.glob("*")) if root.exists() else []:
            if not old(path.name, days):
                continue
            removed[key].append(path.name)
            if dry_run:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    if purge_database and not dry_run:
        from gazette.db import database_url
        from gazette.db.quality_repository import purge_tables

        removed["base"] = purge_tables(
            database_url(settings),
            today,
            rejets_days=retention.rejets_jours,
            articles_days=retention.articles_jours,
        )
    log.info("Purge%s : %s", " (simulation)" if dry_run else "", removed)
    return removed
