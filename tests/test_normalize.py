"""Normalisation (clean/normalize.py) : un test par fonction, puis sur de vrais articles.

Aucun appel réseau ni base. Les caractères spéciaux sont écrits en \\u...... pour qu'on les voie :
\\u00a0 espace insécable, \\u202f espace fine insécable, \\u2019 apostrophe courbe.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from gazette.clean.normalize import (
    ELLIPSIS,
    MAX_EXTRACT_CHARS,
    clean_text,
    decode_entities,
    fix_mojibake,
    normalize_item,
    normalize_whitespace,
    strip_html,
    to_utc,
    truncate_extract,
)
from gazette.clean.rules import apply_rules

FIXTURES = Path(__file__).parent / "fixtures"
NBSP = "\u00a0"


# -- fix_mojibake ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("broken", "expected"),
    [
        ("\u00c3\u00a9conomie", "\u00e9conomie"),  # Ã© -> é
        ("caf\u00c3\u00a9s \u00c3\u00a0 Paris", "caf\u00e9s \u00e0 Paris"),
        ("l\u00e2\u20ac\u2122\u00c3\u00a9tat", "l\u2019\u00e9tat"),  # â€™ -> apostrophe courbe
        ("\u00c2\u00ab\u00a0Bonjour\u00a0\u00c2\u00bb", "\u00ab\u00a0Bonjour\u00a0\u00bb"),
        ("\u00c5\u201cuvre", "\u0153uvre"),  # Å“ -> œ
    ],
)
def test_fix_mojibake_repairs_broken_encoding(broken, expected):
    assert fix_mojibake(broken) == expected


@pytest.mark.parametrize(
    "healthy",
    [
        "\u00e9conomie \u00e0 l\u2019\u00e9cole",
        "Il \u00e9tait l\u00e0\u2026 puis parti",  # é suivi d'un point de suspension
        "Prix : 5 \u20ac, soit 10 \u00e2ge",
        "",
    ],
)
def test_fix_mojibake_leaves_healthy_text_alone(healthy):
    assert fix_mojibake(healthy) == healthy


def test_fix_mojibake_only_repairs_the_broken_part():
    assert fix_mojibake("D\u00e9j\u00e0 \u00c3\u00a9crit") == "D\u00e9j\u00e0 \u00e9crit"


# -- decode_entities ---------------------------------------------------------------------------
def test_decode_entities_decodes_named_and_numeric_entities():
    assert decode_entities("son &quot;Projet France&quot;") == 'son "Projet France"'
    assert (
        decode_entities("l&#039;\u00e9conomie &amp; la politique")
        == "l'\u00e9conomie & la politique"
    )
    assert decode_entities("a&nbsp;: b") == "a\u00a0: b"


def test_decode_entities_handles_double_encoding():
    assert decode_entities("&amp;quot;Bonjour&amp;quot;") == '"Bonjour"'


def test_decode_entities_leaves_plain_text_alone():
    assert decode_entities("R&D, Q&A et AT&T") == "R&D, Q&A et AT&T"


# -- strip_html --------------------------------------------------------------------------------
def test_strip_html_removes_tags_and_keeps_the_text():
    assert strip_html("<p>Apr\u00e8s six mois, <b>le pont</b> rouvre.</p>") == (
        " Apr\u00e8s six mois, le pont rouvre. "
    )


def test_strip_html_separates_words_around_block_tags():
    assert normalize_whitespace(strip_html("ligne 1<br/>ligne 2<p>ligne 3</p>")) == (
        "ligne 1 ligne 2 ligne 3"
    )


def test_strip_html_removes_comments_scripts_and_styles():
    html_text = "avant<!-- note --><script>alert(1)</script><style>p{}</style>apr\u00e8s"
    assert strip_html(html_text) == "avantapr\u00e8s"


def test_strip_html_keeps_a_less_than_sign_in_text():
    assert strip_html("Moins de 5 < 10 euros, et 12 > 8") == "Moins de 5 < 10 euros, et 12 > 8"


def test_strip_html_does_not_decode_entities():
    assert strip_html("<b>R&amp;D</b>") == "R&amp;D"


# -- normalize_whitespace ----------------------------------------------------------------------
def test_normalize_whitespace_replaces_non_breaking_spaces():
    assert normalize_whitespace("Quiz\u00a0: 12 questions\u202f?") == "Quiz : 12 questions ?"


def test_normalize_whitespace_collapses_runs_and_trims():
    assert normalize_whitespace("  un \t deux\n\ntrois   ") == "un deux trois"


def test_normalize_whitespace_removes_invisible_characters():
    assert normalize_whitespace("ex\u200bemple\u00ad de\ufeff texte") == "exemple de texte"


# -- clean_text --------------------------------------------------------------------------------
def test_clean_text_chains_every_correction():
    dirty = "<p>Un &quot;test&quot;\u00a0: \u00c3\u00a9t\u00c3\u00a9</p>"
    assert clean_text(dirty) == 'Un "test" : \u00e9t\u00e9'


def test_clean_text_handles_entity_encoded_html():
    assert clean_text("&lt;p&gt;Bonjour &lt;b&gt;tous&lt;/b&gt;&lt;/p&gt;") == "Bonjour tous"


@pytest.mark.parametrize("empty", [None, "", "   ", "<br/>"])
def test_clean_text_of_nothing_is_an_empty_string(empty):
    assert clean_text(empty) == ""


# -- truncate_extract --------------------------------------------------------------------------
def test_truncate_extract_keeps_short_text():
    assert truncate_extract("Un extrait court.") == "Un extrait court."


def test_truncate_extract_keeps_text_of_exactly_the_maximum_length():
    text = "a" * MAX_EXTRACT_CHARS
    assert truncate_extract(text) == text


def test_truncate_extract_cuts_on_a_word_and_adds_an_ellipsis():
    text = "mot " * 100  # 400 caractères
    result = truncate_extract(text, max_chars=50)
    assert len(result) <= 50
    assert result.endswith("mot" + ELLIPSIS)  # coupé entre deux mots, sans espace avant
    assert "mo" + ELLIPSIS not in result


def test_truncate_extract_drops_the_punctuation_before_the_ellipsis():
    text = "Le gouvernement a annonc\u00e9, ce matin, une r\u00e9forme, " * 3
    result = truncate_extract(text, max_chars=60)
    assert len(result) <= 60
    assert not result[:-1].endswith((",", " ", ";", ":", "."))


def test_truncate_extract_hard_cuts_a_text_without_spaces():
    result = truncate_extract("x" * 500, max_chars=100)
    assert result == "x" * 99 + ELLIPSIS


def test_truncate_extract_never_exceeds_the_limit():
    text = "Un extrait tr\u00e8s long, avec des mots de longueurs vari\u00e9es. " * 30
    for limit in (20, 80, 150, MAX_EXTRACT_CHARS):
        assert len(truncate_extract(text, max_chars=limit)) <= limit


# -- to_utc ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-12T06:00:25+01:00", "2026-11-12T05:00:25+00:00"),  # heure d'hiver
        ("2026-07-12T06:00:25+02:00", "2026-07-12T04:00:25+00:00"),  # heure d'été
        ("2026-11-12T05:00:25+00:00", "2026-11-12T05:00:25+00:00"),  # déjà en UTC
        ("2026-11-12T05:00:25Z", "2026-11-12T05:00:25+00:00"),
        ("2026-11-12T05:00:25", "2026-11-12T05:00:25+00:00"),  # sans fuseau : lu comme UTC
        ("Thu, 12 Nov 2026 06:00:25 +0100", "2026-11-12T05:00:25+00:00"),  # format e-mail
        ("Thu, 12 Nov 2026 06:00:25 GMT", "2026-11-12T06:00:25+00:00"),
    ],
)
def test_to_utc(value, expected):
    assert to_utc(value) == expected


def test_to_utc_accepts_a_datetime():
    paris = timezone(timedelta(hours=1))
    assert to_utc(datetime(2026, 11, 12, 6, 0, tzinfo=paris)) == "2026-11-12T05:00:00+00:00"


@pytest.mark.parametrize("value", [None, "", "   ", "pas une date", "2026-13-45"])
def test_to_utc_of_nothing_usable_is_none(value):
    assert to_utc(value) is None


# -- normalize_item ----------------------------------------------------------------------------
def make(**fields):
    item = {
        "id": "abc123",
        "source": "L'Obs",
        "lien": "https://www.example.fr/politique/a?x=1",
        "titre": "Quiz\u00a0: 12 questions sur le climat",
        "resume": "<p>Un &quot;extrait&quot;\u00a0:  de test.</p>",
        "publie_le": "2026-11-12T06:00:25+01:00",
        "recupere_le": "2026-11-12T11:43:48+01:00",
        "rubrique": "Les jeux du  Nouvel Obs",
        "payload": {
            "title": "Quiz\u00a0: 12 questions",
            "published": "Thu, 12 Nov 2026 06:00:25 +0100",
        },
    }
    item.update(fields)
    return item


def test_normalize_item_cleans_text_and_converts_dates():
    result = normalize_item(make())
    assert result["titre"] == "Quiz : 12 questions sur le climat"
    assert result["resume"] == 'Un "extrait" : de test.'
    assert result["rubrique"] == "Les jeux du Nouvel Obs"
    assert result["publie_le"] == "2026-11-12T05:00:25+00:00"
    assert result["recupere_le"] == "2026-11-12T10:43:48+00:00"


def test_normalize_item_keeps_identity_fields_and_payload_untouched():
    item = make()
    result = normalize_item(item)
    for key in ("id", "source", "lien", "payload"):
        assert result[key] == item[key]
    assert result["payload"]["title"] == "Quiz\u00a0: 12 questions"  # valeur d'origine du flux


def test_normalize_item_does_not_modify_its_input():
    item = make()
    snapshot = json.loads(json.dumps(item))
    normalize_item(item)
    assert item == snapshot


def test_normalize_item_bounds_the_extract():
    result = normalize_item(make(resume="Un mot de plus. " * 80), max_extract=100)
    assert len(result["resume"]) <= 100
    assert result["resume"].endswith(ELLIPSIS)


def test_normalize_item_handles_missing_fields():
    result = normalize_item(make(resume="", rubrique=None, publie_le=None, titre=""))
    assert (result["titre"], result["resume"], result["rubrique"], result["publie_le"]) == (
        "",
        "",
        None,
        None,
    )


def test_normalize_item_is_idempotent():
    once = normalize_item(make())
    assert normalize_item(once) == once


# -- Sur de vrais articles (collecte du 2026-11-12) -----------------------------------------------
@pytest.fixture
def sample():
    return json.loads((FIXTURES / "rules_sample.json").read_text(encoding="utf-8"))


def test_real_articles_come_out_clean(sample):
    for original in sample["items"]:
        item = normalize_item(original)
        for field in ("titre", "resume"):
            assert NBSP not in item[field]
            assert "  " not in item[field]
            assert item[field] == item[field].strip()
        assert len(item["resume"]) <= MAX_EXTRACT_CHARS
        assert item["publie_le"].endswith("+00:00")
        assert item["recupere_le"].endswith("+00:00")
        for key in ("id", "source", "lien"):
            assert item[key] == original[key]
        assert normalize_item(item) == item  # idempotent


def test_real_articles_with_non_breaking_spaces_were_fixed(sample):
    before = [i for i in sample["items"] if NBSP in i["titre"] or NBSP in i["resume"]]
    assert before, "la fixture doit contenir des espaces insécables"
    assert not any(NBSP in normalize_item(i)["titre"] for i in before)


def test_rules_give_the_same_verdicts_on_normalized_articles(sample):
    normalized = [normalize_item(i) for i in sample["items"]]
    now = datetime(2026, 11, 12, 11, 0, tzinfo=UTC)
    result = apply_rules(normalized, now=now)
    outcome = {i["id"][:8]: None for i in result.kept}
    outcome |= {r.item["id"][:8]: r.rule for r in result.rejected}
    assert outcome == sample["expected"]
