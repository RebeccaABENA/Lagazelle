"""Contrôles de qualité : des faits vérifiés sur les données réelles après chaque passage.

Ils ne testent pas le code (c'est le rôle de la CI) : ils relisent ce qui est effectivement
dans `articles_propres` et `rejets` pour un jour (via `CleanStore.load_day`), et disent si
c'est vrai. Assertions maison plutôt qu'un outil dédié : voir docs/qualite.md § 7.

Les seuils reprennent ceux des règles (`RulesConfig` : âge maximal, tolérance sur le futur)
et de la normalisation (longueur d'extrait), pour que règles et contrôles disent la même chose.

Gravité :
- « critique » : la donnée est fausse, la gazette ne doit pas être publiée telle quelle ;
  la tâche de l'orchestrateur échoue (après avoir alerté).
- « avertissement » : à regarder (une source dérive, une règle mord trop) ; on alerte
  mais la tâche réussit.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from gazette.clean.normalize import MAX_EXTRACT_CHARS, fix_mojibake
from gazette.clean.rules import RulesConfig, fold

CRITIQUE = "critique"
AVERTISSEMENT = "avertissement"

_HTML_LEFTOVER = re.compile(r"</?[a-zA-Z][^<>]*>|&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,8});")

Row = Mapping[str, Any]  # une ligne de articles_propres ou de rejets


@dataclass(frozen=True)
class ChecksConfig:
    """Seuils des contrôles. Les valeurs par défaut sont celles de docs/qualite.md § 7."""

    rules: RulesConfig = field(default_factory=RulesConfig)
    extrait_max: int = MAX_EXTRACT_CHARS
    # C03 : taux de rejet maximal d'une source…
    taux_rejet_source_max: float = 0.80
    # … calculé seulement si la source a fourni au moins ce nombre d'articles.
    taux_rejet_items_min: int = 10
    # C04 : nombre minimal d'articles propres pour un jour…
    articles_min: int = 30
    # … jugé seulement à partir de cette heure (Paris) : le DAG passe toutes les 30 min et
    # la journée se remplit peu à peu ; à 6 h, avant l'édition de 7 h, le volume doit y être.
    volume_heure_min: int = 6


@dataclass(frozen=True)
class CheckResult:
    id: str
    description: str
    gravite: str
    ok: bool
    valeur: float | int | str
    seuil: float | int | str
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DayFacts:
    """Ce qu'on relit pour un jour de collecte."""

    clean: list[Row]  # lignes de articles_propres
    rejects: list[Row]  # lignes de rejets
    collect_failed: list[str] = field(default_factory=list)  # sources en échec (manifeste)
    volume_evaluable: bool = True  # False en début de journée (la collecte se remplit)

    def per_source(self) -> dict[str, dict[str, int]]:
        kept = Counter(r["source"] for r in self.clean)
        rejected = Counter(r["source"] for r in self.rejects)
        return {
            source: {
                "gardes": kept[source],
                "rejetes": rejected[source],
                "entres": kept[source] + rejected[source],
            }
            for source in sorted(set(kept) | set(rejected))
        }


def has_broken_encoding(text: str) -> bool:
    """Encodage cassé restant : réparable par la normalisation (donc oublié) ou perdu (�)."""
    return "�" in text or fix_mojibake(text) != text


def _examples(rows: list[Row], n: int = 3) -> str:
    return " ; ".join(f"{r['source']} « {str(r['titre'])[:60]} »" for r in rows[:n])


def _check(cid, description, gravite, bad, detail_rows=None, *, valeur=None, seuil=0, detail=None):
    count = len(bad) if isinstance(bad, list) else bad
    return CheckResult(
        cid,
        description,
        gravite,
        ok=not count,
        valeur=count if valeur is None else valeur,
        seuil=seuil,
        detail=detail if detail is not None else _examples(detail_rows or []),
    )


def check_future_dates(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    tol = timedelta(minutes=cfg.rules.future_tolerance_minutes)
    bad = [r for r in f.clean if r["publie_le"] and r["publie_le"] > r["recupere_le"] + tol]
    return _check(
        "C01_dates_futures", "Aucune date de publication dans le futur", CRITIQUE, bad, bad
    )


def check_empty_titles(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    bad = [r for r in f.clean if not str(r["titre"] or "").strip()]
    return _check("C02_titres_vides", "Aucun titre vide", CRITIQUE, bad, bad)


def check_source_reject_rate(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    rates = {s: v["rejetes"] / v["entres"] for s, v in f.per_source().items() if v["entres"]}
    big = {s for s, v in f.per_source().items() if v["entres"] >= cfg.taux_rejet_items_min}
    offenders = {s: r for s, r in rates.items() if s in big and r > cfg.taux_rejet_source_max}
    return CheckResult(
        "C03_taux_rejet_source",
        f"Taux de rejet de chaque source <= {cfg.taux_rejet_source_max:.0%} "
        f"(sources d'au moins {cfg.taux_rejet_items_min} articles)",
        AVERTISSEMENT,
        ok=not offenders,
        valeur=round(max(rates.values(), default=0), 3),
        seuil=cfg.taux_rejet_source_max,
        detail=", ".join(f"{s} {r:.0%}" for s, r in sorted(offenders.items())),
    )


def check_volume(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    n, minimum = len(f.clean), cfg.articles_min
    description = f"Au moins {minimum} articles propres"
    if not f.volume_evaluable:
        detail = f"non évalué avant {cfg.volume_heure_min} h (journée en cours)"
        return CheckResult("C04_volume", description, CRITIQUE, True, n, minimum, detail)
    return CheckResult("C04_volume", description, CRITIQUE, n >= minimum, n, minimum)


def check_source_coverage(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    empty = sorted(s for s, v in f.per_source().items() if v["gardes"] == 0)
    return _check(
        "C05_couverture_sources",
        "Chaque source présente dans le brut a au moins un article gardé",
        AVERTISSEMENT,
        empty,
        detail=", ".join(empty),
    )


def check_freshness(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    max_age = timedelta(hours=cfg.rules.max_age_hours)
    bad = [r for r in f.clean if r["publie_le"] and r["recupere_le"] - r["publie_le"] > max_age]
    return _check(
        "C06_fraicheur",
        f"Aucun article de plus de {cfg.rules.max_age_hours} h à la collecte",
        CRITIQUE,
        bad,
        bad,
    )


def check_html_leftovers(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    bad = [
        r
        for r in f.clean
        if _HTML_LEFTOVER.search(str(r["titre"])) or _HTML_LEFTOVER.search(str(r["resume"] or ""))
    ]
    return _check(
        "C07_html_residuel",
        "Aucune balise ni entité HTML dans les titres et extraits",
        CRITIQUE,
        bad,
        bad,
    )


def check_encoding(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    bad = [
        r
        for r in f.clean
        if has_broken_encoding(str(r["titre"])) or has_broken_encoding(str(r["resume"] or ""))
    ]
    return _check("C08_encodage", "Aucun encodage cassé (Ã©, �)", CRITIQUE, bad, bad)


def check_summary_length(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    longest = max((len(r["resume"] or "") for r in f.clean), default=0)
    return CheckResult(
        "C09_extrait_borne",
        f"Extraits de {cfg.extrait_max} caractères au plus (pas de texte intégral)",
        CRITIQUE,
        longest <= cfg.extrait_max,
        longest,
        cfg.extrait_max,
    )


def check_unique_titles(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    counts = Counter(fold(r["titre"]) for r in f.clean if fold(r["titre"]))
    dupes = {k: n for k, n in counts.items() if n > 1}
    return _check(
        "C10_titres_uniques",
        "Aucun titre en double parmi les articles du jour",
        CRITIQUE,
        len(dupes),
        detail=" ; ".join(f"« {k[:50]} » x{n}" for k, n in list(dupes.items())[:3]),
    )


def check_collect(f: DayFacts, cfg: ChecksConfig) -> CheckResult:
    return _check(
        "C11_collecte",
        "Aucune source en échec à la collecte",
        AVERTISSEMENT,
        list(f.collect_failed),
        detail=", ".join(f.collect_failed),
    )


ALL_CHECKS = (
    check_future_dates,
    check_empty_titles,
    check_source_reject_rate,
    check_volume,
    check_source_coverage,
    check_freshness,
    check_html_leftovers,
    check_encoding,
    check_summary_length,
    check_unique_titles,
    check_collect,
)


def run_checks(facts: DayFacts, cfg: ChecksConfig | None = None) -> list[CheckResult]:
    cfg = cfg or ChecksConfig()
    return [check(facts, cfg) for check in ALL_CHECKS]


def summarize(results: list[CheckResult], checked_at: datetime) -> dict:
    return {
        "verifie_le": checked_at.isoformat(),
        "ok": all(r.ok for r in results),
        "critiques_en_echec": [r.id for r in results if not r.ok and r.gravite == CRITIQUE],
        "avertissements": [r.id for r in results if not r.ok and r.gravite == AVERTISSEMENT],
        "resultats": [r.as_dict() for r in results],
    }
