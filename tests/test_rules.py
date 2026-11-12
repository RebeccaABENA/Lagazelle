"""Règles d'exclusion (clean/rules.py) : chaque règle, ses faux positifs, et la fixture réelle.

Aucun appel réseau ni base : les articles sont de simples dictionnaires.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gazette.clean.rules import RULE_IDS, RulesConfig, apply_rules, check_item

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 12, 11, 0, tzinfo=UTC)

# Marseille : trois titres pour deux articles (un doublon approximatif, un article distinct).
MARSEILLE = "Incendie à Marseille : un entrepôt détruit par les flammes cette nuit"
MARSEILLE_BIS = "Incendie à Marseille : un entrepôt détruit par les flammes"  # même événement
MARSEILLE_AUTRE = "Marseille : un entrepôt détruit dans un violent incendie"  # titre trop différent


def make(**fields):
    """Un article brut ordinaire, publié il y a une heure, que aucune règle ne rejette."""
    item = {
        "id": "x",
        "source": "Test",
        "lien": "https://www.example.fr/politique/article-1",
        "titre": "Le Parlement adopte le budget après un long débat",
        "resume": "",
        "publie_le": "2026-11-12T10:00:00+01:00",
        "recupere_le": "2026-11-12T11:00:00+01:00",
        "rubrique": None,
        "payload": {},
    }
    item.update(fields)
    return item


def rules_of(item, **kwargs):
    return [r.rule for r in check_item(item, now=NOW, **kwargs)]


def test_ordinary_article_is_kept():
    assert rules_of(make()) == []


# -- R01 -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fields",
    [
        {"rubrique": "Les jeux du  Nouvel Obs"},  # la rubrique réelle a une double espace
        {"titre": "Quiz. COP31 : 12 questions sur le climat"},
        {"titre": "QUIZ - Avez-vous suivi la rentrée littéraire ?"},
        {"titre": "Quizz : testez votre culture"},
    ],
)
def test_r01_quiz_jeux(fields):
    assert rules_of(make(**fields)) == ["R01_quiz_jeux"]


@pytest.mark.parametrize(
    "fields",
    [{"titre": "Un quiz sur la rentrée"}, {"rubrique": "Culture"}, {"titre": "Quizás, quizás"}],
)
def test_r01_does_not_reject_other_articles(fields):
    assert rules_of(make(**fields)) == []


# -- R02 -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fields",
    [
        {"rubrique": "Bons plans High-Tech"},
        {"lien": "https://www.lefigaro.fr/bons-plans/amazon-baisse-les-prix"},
    ],
)
def test_r02_bons_plans(fields):
    assert rules_of(make(**fields)) == ["R02_bons_plans"]


def test_r02_does_not_reject_an_article_about_bons_plans():
    item = make(titre="Les bons plans du gouvernement pour l'emploi", rubrique="Politique")
    assert rules_of(item) == []


# -- R03 -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "titre",
    [
        "EN DIRECT, présidentielle 2027 | Suivez le grand oral",
        "DIRECT. Guerre en Ukraine : les émissaires sont arrivés",
        "REPLAY. Présidentielle 2027 : Mélenchon s'en prend à ses rivaux",
        "Replay | Où est passé l'argent de l'Etat français ?",
    ],
)
def test_r03_direct_replay(titre):
    assert rules_of(make(titre=titre)) == ["R03_direct_replay"]


@pytest.mark.parametrize(
    "titre",
    ["Directement concerné par la réforme", "Le direct de Mélenchon face aux journalistes"],
)
def test_r03_only_matches_the_start_of_the_title(titre):
    assert rules_of(make(titre=titre)) == []


# -- R04 -----------------------------------------------------------------------------------
@pytest.mark.parametrize("rubrique", ["Météo", "Circulation - Déplacements", "Carnet noir"])
def test_r04_services_locaux(rubrique):
    assert rules_of(make(rubrique=rubrique)) == ["R04_services_locaux"]


@pytest.mark.parametrize("rubrique", ["Météorologie", "Faits divers", "Culture et loisirs"])
def test_r04_does_not_reject_other_sections(rubrique):
    assert rules_of(make(rubrique=rubrique)) == []


# -- R05 -----------------------------------------------------------------------------------
def test_r05_exactly_at_the_threshold_is_kept():
    # recupere_le = 11:00+01:00 ; 48 h avant = 2026-11-10T11:00:00+01:00
    assert rules_of(make(publie_le="2026-11-10T11:00:00+01:00")) == []


def test_r05_one_minute_over_the_threshold_is_rejected():
    item = make(publie_le="2026-11-10T10:59:00+01:00")
    (rejection,) = check_item(item, now=NOW)
    assert rejection.rule == "R05_trop_ancien"
    assert "48 h" in rejection.reason


def test_r05_threshold_comes_from_the_config():
    item = make(publie_le="2026-11-11T09:00:00+01:00")  # 26 h
    assert rules_of(item) == []
    assert rules_of(item, config=RulesConfig(max_age_hours=24)) == ["R05_trop_ancien"]


@pytest.mark.parametrize("publie_le", [None, "", "pas une date"])
def test_r05_and_r08_ignore_articles_without_a_usable_date(publie_le):
    assert rules_of(make(publie_le=publie_le)) == []


def test_r05_uses_now_when_the_collection_time_is_missing():
    item = make(publie_le="2026-11-09T09:00:00+00:00", recupere_le=None)  # 74 h avant NOW
    assert rules_of(item) == ["R05_trop_ancien"]


# -- R07 -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fields",
    [
        {"titre": "Horoscope du jeudi 12 novembre : Bélier, Taureau, Gémeaux"},
        {"rubrique": "Astrologie"},
        {"lien": "https://www.example.fr/horoscope/belier"},
    ],
)
def test_r07_horoscope(fields):
    assert rules_of(make(**fields)) == ["R07_horoscope"]


@pytest.mark.parametrize(
    "titre",
    [
        "Ces restaurants isolés qui font le pari de la haute gastronomie",
        "Astronomie : une comète visible à l'oeil nu",
    ],
)
def test_r07_does_not_reject_similar_words(titre):
    assert rules_of(make(titre=titre)) == []


# -- R08 -----------------------------------------------------------------------------------
def test_r08_future_date():
    (rejection,) = check_item(make(publie_le="2026-11-12T13:00:00+01:00"), now=NOW)
    assert rejection.rule == "R08_date_future"
    assert "120 min" in rejection.reason


def test_r08_small_clock_drift_is_tolerated():
    assert rules_of(make(publie_le="2026-11-12T11:05:00+01:00")) == []


def test_r08_a_date_without_timezone_is_read_as_utc():
    # 10:00 UTC, et la collecte a eu lieu à 11:00+01:00 = 10:00 UTC : pas dans le futur.
    assert rules_of(make(publie_le="2026-11-12T10:00:00")) == []


# -- R09, R10, R11 ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fields",
    [
        {"titre": "Programme TV du jeudi 12 novembre : que regarder ce soir ?"},
        {"titre": "Ce soir à la télé : le film du dimanche"},
        {"titre": "TV : le programme de la soirée"},
        {"lien": "https://www.example.fr/programme-tv/tf1"},
    ],
)
def test_r09_programme_tv(fields):
    assert rules_of(make(**fields)) == ["R09_programme_tv"]


def test_r09_keeps_tv_reviews():
    item = make(titre="Eldorado : l'allégorie d'une société en crise", rubrique="TéléObs")
    assert rules_of(item) == []


@pytest.mark.parametrize(
    "titre",
    [
        "Publi-reportage : comment bien choisir sa mutuelle",
        "Contenu sponsorisé : nos conseils énergie",
        "Advertorial voyage : la Crète autrement",
    ],
)
def test_r10_publi_reportage(titre):
    assert rules_of(make(titre=titre)) == ["R10_publi_reportage"]


def test_r10_does_not_reject_an_article_about_a_sponsor():
    assert rules_of(make(titre="Le sponsor de l'Euro annonce un nouveau contrat")) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"titre": "Contenu partenaire : les assurances en 2027"},
        {"titre": "Brand content : la marque se raconte"},
        {"lien": "https://www.example.fr/partenaires/assurance"},
    ],
)
def test_r11_contenu_partenaire(fields):
    assert rules_of(make(**fields)) == ["R11_contenu_partenaire"]


def test_r11_does_not_reject_an_article_about_a_partnership():
    assert rules_of(make(titre="Le ministère signe un partenariat avec l'ONU")) == []


# -- Plusieurs règles, une seule retenue ---------------------------------------------------------
def test_first_matching_rule_is_the_one_kept():
    old_quiz = make(titre="Quiz. Test de culture générale", publie_le="2026-11-01T10:00:00+01:00")
    assert rules_of(old_quiz) == ["R01_quiz_jeux", "R05_trop_ancien"]  # toutes, dans l'ordre

    result = apply_rules([old_quiz], now=NOW)
    assert [(r.rule) for r in result.rejected] == ["R01_quiz_jeux"]  # une seule : la première


# -- R06 -----------------------------------------------------------------------------------
def test_r06_rejects_the_later_title_of_the_same_event():
    first = make(
        id="a", source="Libération", titre=MARSEILLE, publie_le="2026-11-12T09:00:00+01:00"
    )
    later = make(
        id="b", source="Le Monde", titre=MARSEILLE_BIS, publie_le="2026-11-12T09:30:00+01:00"
    )
    result = apply_rules([first, later], now=NOW)
    assert [i["id"] for i in result.kept] == ["a"]
    (rejected,) = result.rejected
    assert (rejected.item["id"], rejected.rule) == ("b", "R06_doublon_approx")
    assert "Libération" in rejected.reason and "Incendie" in rejected.reason


def test_r06_keeps_the_oldest_whatever_the_input_order():
    later = make(id="b", titre=MARSEILLE_BIS, publie_le="2026-11-12T09:30:00+01:00")
    first = make(id="a", titre=MARSEILLE, publie_le="2026-11-12T09:00:00+01:00")
    result = apply_rules([later, first], now=NOW)
    assert [i["id"] for i in result.kept] == ["a"]
    assert [r.item["id"] for r in result.rejected] == ["b"]


def test_r06_keeps_titles_that_are_too_different():
    items = [make(id="a", titre=MARSEILLE), make(id="b", titre=MARSEILLE_AUTRE)]
    assert apply_rules(items, now=NOW).rejected == []


def test_r06_threshold_comes_from_the_config():
    items = [make(id="a", titre=MARSEILLE), make(id="b", titre=MARSEILLE_BIS)]
    strict = RulesConfig(near_duplicate_threshold=0.95)
    assert apply_rules(items, now=NOW, config=strict).rejected == []


def test_r06_ignores_very_short_titles():
    items = [make(id="a", titre="Le bilan du jour"), make(id="b", titre="Le bilan du jour")]
    assert apply_rules(items, now=NOW).rejected == []


def test_r06_ignores_articles_already_rejected_by_another_rule():
    rejected_quiz = make(id="q", titre=f"Quiz. {MARSEILLE}", publie_le="2026-11-12T08:00:00+01:00")
    legit = make(id="a", titre=MARSEILLE, publie_le="2026-11-12T09:00:00+01:00")
    result = apply_rules([rejected_quiz, legit], now=NOW)
    assert [i["id"] for i in result.kept] == ["a"]
    assert [r.rule for r in result.rejected] == ["R01_quiz_jeux"]


# -- Résultat --------------------------------------------------------------------------------
def test_result_lists_every_rule_even_without_rejection():
    assert len(RULE_IDS) == 11
    assert list(RULE_IDS) == sorted(RULE_IDS)  # R01 ... R11 dans l'ordre
    result = apply_rules([make(), make(rubrique="Météo")], now=NOW)
    counts = result.rejected_by_rule()
    assert list(counts) == list(RULE_IDS)
    assert counts["R04_services_locaux"] == 1
    assert sum(counts.values()) == 1
    assert result.entered == 2


def test_input_order_is_kept():
    items = [
        make(id="1", rubrique="Météo"),
        make(id="2"),
        make(id="3", titre="DIRECT. Foot"),
        make(id="4", titre="La tempête Charles touche le Sud-Ouest avec des rafales"),
    ]
    result = apply_rules(items, now=NOW)
    assert [i["id"] for i in result.kept] == ["2", "4"]
    assert [r.item["id"] for r in result.rejected] == ["1", "3"]


# -- Fixture : 29 vrais articles de la collecte du 2026-11-12 ------------------------------------
def test_rules_on_real_articles():
    sample = json.loads((FIXTURES / "rules_sample.json").read_text(encoding="utf-8"))
    result = apply_rules(sample["items"], now=NOW)

    outcome = {i["id"][:8]: None for i in result.kept}
    outcome |= {r.item["id"][:8]: r.rule for r in result.rejected}
    assert outcome == sample["expected"]  # les attendus ont été posés à la main, avant les règles

    assert result.rejected_by_rule() == {
        "R01_quiz_jeux": 3,
        "R02_bons_plans": 2,
        "R03_direct_replay": 2,
        "R04_services_locaux": 3,
        "R05_trop_ancien": 2,
        "R06_doublon_approx": 1,
        "R07_horoscope": 0,
        "R08_date_future": 0,
        "R09_programme_tv": 0,
        "R10_publi_reportage": 0,
        "R11_contenu_partenaire": 0,
    }
    assert result.entered == len(sample["items"]) == 29
