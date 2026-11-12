"""Rapport de qualité d'un passage du nettoyage : entrés, gardés, rejetés, et pourquoi.

Il est fait pour repérer une règle trop gourmande : il compte les rejets par règle, par source
(avec le taux de rejet), donne les cinq rejets les plus fréquents avec un exemple, et signale
les règles qui écartent à elles seules une grande part d'une source.

`build_report` calcule un dictionnaire sans rien d'autre que des nombres et du texte (donc
sérialisable en JSON), `render_markdown` le met en forme, `write_report` l'archive dans
`<data>/rapports/qualite-AAAA-MM-JJ.md` et `.json`. Le rapport d'un jour est réécrit à chaque
passage : le dernier du jour est le bilan complet.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from gazette.clean.normalize import truncate_extract
from gazette.clean.rules import RULE_IDS, RulesConfig, RulesResult

EXAMPLE_CHARS = 90  # longueur maximale d'un titre d'exemple dans le rapport
TOP_N = 5  # nombre de rejets les plus fréquents listés
GREEDY_SHARE = 0.5  # une règle qui écarte au moins cette part d'une source est signalée...
GREEDY_MIN_ARTICLES = 5  # ... si la source compte au moins ce nombre d'articles (sinon : bruit)


def _share(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _pct(share: float) -> str:
    return f"{share * 100:.0f} %"


def build_report(
    day: date,
    result: RulesResult,
    *,
    config: RulesConfig | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Bilan d'un passage : nombres globaux, par règle, par source, top des rejets."""
    config = config or RulesConfig()
    generated_at = generated_at or datetime.now(UTC)
    entered = result.entered

    entered_by_source = Counter(i.get("source") for i in result.kept)
    entered_by_source.update(r.item.get("source") for r in result.rejected)
    rejected_by_pair = Counter((r.rule, r.item.get("source")) for r in result.rejected)
    rejected_by_source = Counter(r.item.get("source") for r in result.rejected)

    examples: dict[tuple[str, str], str] = {}
    for r in result.rejected:  # le premier rejet de chaque couple (règle, source) sert d'exemple
        examples.setdefault((r.rule, r.item.get("source")), str(r.item.get("titre", "")))

    by_rule = result.rejected_by_rule()
    by_source: dict[str, Any] = {}
    for source in sorted(
        entered_by_source, key=lambda s: (-rejected_by_source[s] / entered_by_source[s], s)
    ):
        total = entered_by_source[source]
        rejected = rejected_by_source[source]
        by_source[source] = {
            "entres": total,
            "gardes": total - rejected,
            "rejetes": rejected,
            "taux_rejet": _share(rejected, total),
            "par_regle": {
                rule: rejected_by_pair[(rule, source)]
                for rule in RULE_IDS
                if rejected_by_pair[(rule, source)]
            },
        }

    top = sorted(rejected_by_pair.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
    greedy = [
        {
            "regle": rule,
            "source": source,
            "nombre": count,
            "sur": entered_by_source[source],
            "part": _share(count, entered_by_source[source]),
        }
        for (rule, source), count in rejected_by_pair.items()
        if entered_by_source[source] >= GREEDY_MIN_ARTICLES
        and count / entered_by_source[source] >= GREEDY_SHARE
    ]

    return {
        "jour": day.isoformat(),
        "genere_le": generated_at.astimezone(UTC).isoformat(timespec="seconds"),
        "seuils": {
            "age_max_heures": config.max_age_hours,
            "tolerance_date_future_minutes": config.future_tolerance_minutes,
            "similarite_doublon": config.near_duplicate_threshold,
            "mots_min_doublon": config.near_duplicate_min_words,
        },
        "entres": entered,
        "gardes": len(result.kept),
        "rejetes": len(result.rejected),
        "taux_rejet": _share(len(result.rejected), entered),
        "par_regle": {
            rule: {"rejetes": count, "part_des_entres": _share(count, entered)}
            for rule, count in by_rule.items()
        },
        "par_source": by_source,
        "top_rejets": [
            {
                "regle": rule,
                "source": source,
                "nombre": count,
                "exemple": examples[(rule, source)],
            }
            for (rule, source), count in top
        ],
        "regles_a_surveiller": sorted(greedy, key=lambda g: (-g["part"], g["regle"], g["source"])),
    }


def summary_line(report: dict[str, Any]) -> str:
    """Une ligne de journal, au format clé=valeur comme celle de la collecte."""
    return (
        f"nettoyage jour={report['jour']} entres={report['entres']} gardes={report['gardes']} "
        f"rejetes={report['rejetes']} taux_rejet={report['taux_rejet'] * 100:.1f}%"
    )


def _escape(text: str) -> str:
    return text.replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    """Rapport lisible : à lire de haut en bas, du bilan au détail."""
    lines = [
        f"# Rapport de qualité du {report['jour']}",
        "",
        f"Généré le {report['genere_le']} (UTC). "
        f"**{report['entres']}** articles entrés, **{report['gardes']}** gardés, "
        f"**{report['rejetes']}** rejetés ({_pct(report['taux_rejet'])}).",
        "",
        f"Seuils : âge maximal {report['seuils']['age_max_heures']} h, "
        f"similarité des doublons {str(report['seuils']['similarite_doublon']).replace('.', ',')}.",
        "",
        "## Rejets par règle",
        "",
        "| Règle | Rejetés | Part des entrés |",
        "|---|---:|---:|",
    ]
    for rule, stats in report["par_regle"].items():
        lines.append(f"| `{rule}` | {stats['rejetes']} | {_pct(stats['part_des_entres'])} |")

    lines += [
        "",
        "## Par source",
        "",
        "| Source | Entrés | Gardés | Rejetés | Taux de rejet |",
        "|---|---:|---:|---:|---:|",
    ]
    for source, stats in report["par_source"].items():
        lines.append(
            f"| {_escape(str(source))} | {stats['entres']} | {stats['gardes']} | "
            f"{stats['rejetes']} | {_pct(stats['taux_rejet'])} |"
        )

    lines += ["", f"## Les {TOP_N} rejets les plus fréquents", ""]
    if report["top_rejets"]:
        lines += ["| # | Règle | Source | Nombre | Exemple |", "|---:|---|---|---:|---|"]
        for rank, top in enumerate(report["top_rejets"], start=1):
            example = _escape(truncate_extract(top["exemple"], EXAMPLE_CHARS))
            lines.append(
                f"| {rank} | `{top['regle']}` | {_escape(str(top['source']))} | "
                f"{top['nombre']} | {example} |"
            )
    else:
        lines.append("Aucun rejet.")

    lines += ["", "## Règles à surveiller", ""]
    if report["regles_a_surveiller"]:
        lines.append(
            f"Une règle qui écarte au moins {_pct(GREEDY_SHARE)} des articles d'une source "
            f"(d'au moins {GREEDY_MIN_ARTICLES} articles) est peut-être trop gourmande :"
        )
        lines.append("")
        for g in report["regles_a_surveiller"]:
            lines.append(
                f"- `{g['regle']}` écarte {_pct(g['part'])} des articles de la source "
                f"{g['source']} ({g['nombre']} sur {g['sur']})."
            )
    else:
        lines.append("Aucune.")
    return "\n".join(lines) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp_name, 0o644)  # lisible par tous (mkstemp crée un fichier privé)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_report(reports_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """Archive le rapport : `qualite-AAAA-MM-JJ.md` (à lire) et `.json` (pour les contrôles)."""
    reports_dir = Path(reports_dir)
    markdown = reports_dir / f"qualite-{report['jour']}.md"
    data = reports_dir / f"qualite-{report['jour']}.json"
    _write_atomic(markdown, render_markdown(report))
    _write_atomic(data, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return markdown, data
