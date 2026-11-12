"""Règles d'exclusion : écartent du brut ce qui n'a pas sa place dans une gazette.

Chaque règle a un identifiant (`R01_quiz_jeux`, `R02_bons_plans`...) pour que le rapport de
qualité puisse compter les rejets règle par règle. Ce que chaque règle écarte, pourquoi et un
exemple sont décrits dans docs/qualite.md. Les motifs et les seuils sont regroupés en tête de
fichier, pour les lire et les ajuster sans toucher à la logique.

Une règle lit un article brut (le dictionnaire écrit dans data/articles_bruts/) et ne le
modifie pas. Les comparaisons se font sur un texte « replié » : minuscules, sans accents,
espaces insécables et multiples ramenés à une espace. Cela ne remplace pas la normalisation
(module normalize) : le texte stocké n'est jamais changé ici.

Un article sans date n'est jamais écarté pour son âge : on ne peut pas juger ce qu'on ignore.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from urllib.parse import urlsplit

Item = Mapping[str, Any]  # un article brut : id, source, lien, titre, resume, publie_le...


# -- Réglages -------------------------------------------------------------------------------
@dataclass(frozen=True)
class RulesConfig:
    """Seuils des règles. Les valeurs par défaut sont celles de docs/qualite.md."""

    # R05 : un article publié il y a plus de N heures (à la collecte) est écarté.
    max_age_hours: int = 48
    # R08 : marge pour les horloges qui ne sont pas parfaitement synchronisées.
    future_tolerance_minutes: int = 10
    # R06 : similarité minimale (Jaccard sur les mots des titres) pour parler de doublon...
    near_duplicate_threshold: float = 0.7
    # ... entre deux titres d'au moins ce nombre de mots (en dessous, trop de faux positifs).
    near_duplicate_min_words: int = 4


# -- Motifs (appliqués au texte replié : minuscules, sans accents) ---------------------------
# R01 : quiz et jeux
QUIZ_SECTION = "les jeux du nouvel obs"  # début de la rubrique
QUIZ_TITLE = re.compile(r"^quizz?\b")  # « Quiz » et « Quizz »
# R02 : promotions commerciales
BONS_PLANS_SECTION = "bons plans"  # début de la rubrique
BONS_PLANS_URL = frozenset({"bons-plans"})
# R03 : directs et replays
LIVE_TITLE = re.compile(r"^(en )?direct\b|^replay\b")
# R04 : services locaux (météo, circulation, carnet)
SERVICE_SECTION = re.compile(r"\b(meteo|circulation|carnet)\b")
# R07 : horoscope
HOROSCOPE = re.compile(r"\bhoroscopes?\b|\bastrologie\b")
HOROSCOPE_URL = frozenset({"horoscope", "horoscopes", "astrologie"})
# R09 : programme télé
TV_PROGRAM = re.compile(
    r"\bprogrammes? (tv|tele|televises?|de la soiree)\b"
    r"|\bce soir (a|sur) (la )?(tele|tf1|france ?[2-5]|m6|canal\+?|arte)\b"
    r"|\bque regarder (ce soir|a la tele)\b"
    r"|\btv ?: le programme\b"
)
TV_PROGRAM_URL = frozenset({"programme-tv", "programmes-tv", "programme-tele"})
# R10 : publi-reportage
PUBLI = re.compile(
    r"\bpubli[- ]?reportages?\b|\badvertorials?\b"
    r"|\b(contenus?|articles?) sponsorises?\b|\bsponsorise(e|s)? par\b"
    r"|\bpubli[- ]?communiques?\b"
)
PUBLI_URL = frozenset({"publi-reportage", "publireportage", "sponsorise", "advertorial"})
# R11 : contenu partenaire
PARTNER = re.compile(
    r"\bcontenus? partenaires?\b|\bcontenus? de marque\b"
    r"|\bbrand ?content\b|\bpartner(ed)? content\b"
)
PARTNER_URL = frozenset(
    {"partenaire", "partenaires", "contenu-partenaire", "brand-content", "brandcontent"}
)

# R06 : mots vides ignorés quand on compare deux titres
_STOPWORDS = frozenset(
    [
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "de",
        "du",
        "d",
        "l",
        "et",
        "en",
        "au",
        "aux",
        "a",
        "pour",
        "par",
        "sur",
        "dans",
        "qui",
        "que",
        "qu",
        "ce",
        "se",
        "sa",
        "son",
        "ses",
        "est",
        "ne",
        "pas",
        "plus",
        "avec",
        "ou",
        "comme",
        "cette",
        "ces",
        "il",
        "elle",
        "on",
        "y",
    ]
)

# -- Identifiants ---------------------------------------------------------------------------
R01_QUIZ_JEUX = "R01_quiz_jeux"
R02_BONS_PLANS = "R02_bons_plans"
R03_DIRECT_REPLAY = "R03_direct_replay"
R04_SERVICES_LOCAUX = "R04_services_locaux"
R05_TROP_ANCIEN = "R05_trop_ancien"
R06_DOUBLON_APPROX = "R06_doublon_approx"
R07_HOROSCOPE = "R07_horoscope"
R08_DATE_FUTURE = "R08_date_future"
R09_PROGRAMME_TV = "R09_programme_tv"
R10_PUBLI_REPORTAGE = "R10_publi_reportage"
R11_CONTENU_PARTENAIRE = "R11_contenu_partenaire"

_EPOCH = datetime.min.replace(tzinfo=UTC)


# -- Outils ---------------------------------------------------------------------------------
def fold(text: object) -> str:
    """Texte replié pour comparer : minuscules, sans accents, une seule espace entre les mots."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))  # l'espace insécable devient une espace
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain.casefold()).strip()


def parse_date(value: object) -> datetime | None:
    """Date ISO 8601 -> datetime avec fuseau (UTC si absent). None si vide ou illisible."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def url_segments(item: Item) -> set[str]:
    """Segments du chemin du lien : `/bons-plans/a/b` -> {'bons-plans', 'a', 'b'}."""
    path = urlsplit(str(item.get("lien") or "")).path.lower()
    return {segment for segment in path.split("/") if segment}


def title_words(item: Item) -> frozenset[str]:
    """Mots significatifs du titre (sans mots vides), pour comparer deux titres."""
    words = re.findall(r"[a-z0-9]+", fold(item.get("titre")))
    return frozenset(w for w in words if len(w) > 1 and w not in _STOPWORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Part de mots communs : |A inter B| / |A union B| (0 si les deux sont vides)."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _pattern_or_url(item: Item, pattern: re.Pattern[str], segments: frozenset[str]) -> str | None:
    """Motif trouvé dans le titre ou la rubrique, ou section d'URL connue."""
    for field in ("titre", "rubrique"):
        match = pattern.search(fold(item.get(field)))
        if match:
            return f"{field} : motif « {match.group(0)} »"
    found = segments & url_segments(item)
    if found:
        return f"section d'URL « {sorted(found)[0]} »"
    return None


# -- Règles sans mémoire : chacune juge un article seul --------------------------------------
Check = Callable[[Item, RulesConfig, datetime], str | None]


def _quiz(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    if fold(item.get("rubrique")).startswith(QUIZ_SECTION):
        return "rubrique « Les jeux du Nouvel Obs »"
    if QUIZ_TITLE.match(fold(item.get("titre"))):
        return "titre commençant par « Quiz »"
    return None


def _bons_plans(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    if fold(item.get("rubrique")).startswith(BONS_PLANS_SECTION):
        return "rubrique « Bons plans »"
    if BONS_PLANS_URL & url_segments(item):
        return "section d'URL « bons-plans »"
    return None


def _direct_replay(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    match = LIVE_TITLE.match(fold(item.get("titre")))
    return f"titre commençant par « {match.group(0)} »" if match else None


def _services_locaux(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    match = SERVICE_SECTION.search(fold(item.get("rubrique")))
    return f"rubrique de service : « {match.group(0)} »" if match else None


def _reference_time(item: Item, now: datetime) -> datetime:
    """Instant de la collecte de l'article ; à défaut, l'instant du calcul."""
    return parse_date(item.get("recupere_le")) or now


def _too_old(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    published = parse_date(item.get("publie_le"))
    if published is None:
        return None
    age = _reference_time(item, now) - published
    if age > timedelta(hours=cfg.max_age_hours):
        return f"publié il y a {age.total_seconds() / 3600:.0f} h (seuil : {cfg.max_age_hours} h)"
    return None


def _future_date(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    published = parse_date(item.get("publie_le"))
    if published is None:
        return None
    ahead = published - _reference_time(item, now)
    if ahead > timedelta(minutes=cfg.future_tolerance_minutes):
        return f"publication annoncée dans {ahead.total_seconds() / 60:.0f} min"
    return None


def _horoscope(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    return _pattern_or_url(item, HOROSCOPE, HOROSCOPE_URL)


def _programme_tv(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    return _pattern_or_url(item, TV_PROGRAM, TV_PROGRAM_URL)


def _publi_reportage(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    return _pattern_or_url(item, PUBLI, PUBLI_URL)


def _contenu_partenaire(item: Item, cfg: RulesConfig, now: datetime) -> str | None:
    return _pattern_or_url(item, PARTNER, PARTNER_URL)


class Rule(NamedTuple):
    id: str
    check: Check


# Ordre d'évaluation : c'est la première règle qui rejette qui est retenue dans `rejets`.
STATELESS_RULES: tuple[Rule, ...] = (
    Rule(R01_QUIZ_JEUX, _quiz),
    Rule(R02_BONS_PLANS, _bons_plans),
    Rule(R03_DIRECT_REPLAY, _direct_replay),
    Rule(R04_SERVICES_LOCAUX, _services_locaux),
    Rule(R05_TROP_ANCIEN, _too_old),
    Rule(R07_HOROSCOPE, _horoscope),
    Rule(R08_DATE_FUTURE, _future_date),
    Rule(R09_PROGRAMME_TV, _programme_tv),
    Rule(R10_PUBLI_REPORTAGE, _publi_reportage),
    Rule(R11_CONTENU_PARTENAIRE, _contenu_partenaire),
)

# Toutes les règles, dans l'ordre des numéros (le rapport liste aussi celles qui n'ont rien écarté).
RULE_IDS: tuple[str, ...] = tuple(
    sorted([rule.id for rule in STATELESS_RULES] + [R06_DOUBLON_APPROX])
)


# -- Résultats ------------------------------------------------------------------------------
class Rejection(NamedTuple):
    rule: str  # identifiant de la règle, ex. « R01_quiz_jeux »
    reason: str  # ce qui a déclenché la règle, lisible dans le rapport


class Rejected(NamedTuple):
    item: Item
    rule: str
    reason: str


@dataclass(frozen=True)
class RulesResult:
    kept: list[Item]
    rejected: list[Rejected]

    @property
    def entered(self) -> int:
        return len(self.kept) + len(self.rejected)

    def rejected_by_rule(self) -> dict[str, int]:
        """Nombre de rejets par règle, toutes les règles listées (0 si aucune), dans l'ordre."""
        counts = Counter(r.rule for r in self.rejected)
        return {rule_id: counts.get(rule_id, 0) for rule_id in RULE_IDS}


# -- API ------------------------------------------------------------------------------------
def check_item(
    item: Item, *, config: RulesConfig | None = None, now: datetime | None = None
) -> list[Rejection]:
    """Règles sans mémoire qui rejettent cet article, dans l'ordre. Liste vide = rien à écarter.

    R06 (doublon approximatif) n'y figure pas : elle compare plusieurs articles entre eux,
    voir `apply_rules`.
    """
    config = config or RulesConfig()
    now = now or datetime.now(UTC)
    rejections = []
    for rule in STATELESS_RULES:
        reason = rule.check(item, config, now)
        if reason:
            rejections.append(Rejection(rule.id, reason))
    return rejections


def apply_rules(
    items: Iterable[Item], *, config: RulesConfig | None = None, now: datetime | None = None
) -> RulesResult:
    """Sépare les articles gardés et rejetés (avec la règle et la raison du rejet).

    - Chaque article rejeté l'est pour **une** règle : la première qui le rejette.
    - R06 ne regarde que les articles que les autres règles ont gardés, et ne les compare qu'entre
      eux, dans ce lot : le plus ancien est gardé, les titres proches publiés après sont rejetés.
    - `kept` et `rejected` suivent l'ordre d'entrée.
    """
    config = config or RulesConfig()
    now = now or datetime.now(UTC)
    items = list(items)

    rejected: dict[int, Rejected] = {}
    candidates: list[int] = []
    for index, item in enumerate(items):
        rejections = check_item(item, config=config, now=now)
        if rejections:
            rejected[index] = Rejected(item, rejections[0].rule, rejections[0].reason)
        else:
            candidates.append(index)

    def published_at(index: int) -> tuple[datetime, int]:
        item = items[index]
        date = parse_date(item.get("publie_le")) or parse_date(item.get("recupere_le"))
        return (date or _EPOCH, index)

    kept_words: list[tuple[frozenset[str], Item]] = []
    for index in sorted(candidates, key=published_at):
        item = items[index]
        words = title_words(item)
        twin = None
        if len(words) >= config.near_duplicate_min_words:
            twin = next(
                (
                    other
                    for other_words, other in kept_words
                    if jaccard(words, other_words) >= config.near_duplicate_threshold
                ),
                None,
            )
            if twin is None:
                kept_words.append((words, item))
        if twin is not None:
            reason = f"titre proche de « {str(twin.get('titre', ''))[:70]} » ({twin.get('source')})"
            rejected[index] = Rejected(item, R06_DOUBLON_APPROX, reason)

    kept = [item for index, item in enumerate(items) if index not in rejected]
    return RulesResult(kept=kept, rejected=[rejected[i] for i in sorted(rejected)])
