"""Normalisation des articles bruts : texte propre, dates en UTC, extrait borné.

Une fonction pure par transformation (même entrée, même sortie, aucune lecture de fichier ni
d'horloge) pour les tester une par une. `clean_text` les enchaîne sur un texte, et
`normalize_item` sur un article entier. Le brut n'est jamais modifié : on renvoie un nouvel
article, et son `payload` (les valeurs d'origine du flux) est conservé tel quel.

Ce qu'on corrige : l'encodage cassé (`Ã©`), les entités (`&quot;`), le HTML, les espaces
(insécables, multiples, invisibles), les dates (en UTC) et la longueur de l'extrait. Ce qu'on
ne touche pas : le lien, l'identifiant, la source et le `payload`.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

# Longueur maximale d'un extrait, points de suspension compris.
MAX_EXTRACT_CHARS = 300
ELLIPSIS = "\u2026"

# -- Encodage cassé --------------------------------------------------------------------------
# Un texte UTF-8 lu comme du cp1252 donne `Ã©` pour « é » ou `â€™` pour l'apostrophe courbe.
# Un caractère de tête (Â, Ã, Å, â) suivi de 1 à 3 caractères de suite d'octet.
_CONTINUATION = (
    "\u0080-\u00bf\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152"
    "\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122\u0161\u203a\u0153\u017e\u0178"
)
_RE_MOJIBAKE = re.compile(rf"[\u00c2\u00c3\u00c5\u00e2][{_CONTINUATION}]{{1,3}}")

# -- HTML ------------------------------------------------------------------------------------
_RE_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
# Les balises de bloc séparent des mots : on les remplace par une espace pour ne pas les coller.
_RE_BLOCK_TAG = re.compile(
    r"</?(?:br|p|div|li|ul|ol|h[1-6]|tr|td|th|table|blockquote|section|article)\b[^>]*>",
    re.IGNORECASE,
)
# Une balise commence par une lettre : « 5 < 10 » n'est pas touché.
_RE_TAG = re.compile(r"</?[a-zA-Z][^>]*>")

# -- Espaces ---------------------------------------------------------------------------------
# Invisibles : espace de largeur nulle, liaison, mot-joint, BOM, trait d'union conditionnel.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))


def fix_mojibake(text: str) -> str:
    """Répare `Ã©` -> `é`, `â€™` -> apostrophe courbe. Un texte sain est rendu tel quel.

    On ne répare que les séquences qui redeviennent de l'UTF-8 valide : un « é » suivi d'un
    point de suspension, par exemple, n'est pas confondu avec de l'encodage cassé.
    """

    def repair(match: re.Match[str]) -> str:
        run = match.group(0)
        for length in range(len(run), 1, -1):  # le plus long préfixe réparable
            fixed = _decode_cp1252_utf8(run[:length])
            if fixed is not None:
                return fixed + run[length:]
        return run

    return _RE_MOJIBAKE.sub(repair, text)


def _decode_cp1252_utf8(run: str) -> str | None:
    for codec in ("cp1252", "latin-1"):
        try:
            return run.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return None


def decode_entities(text: str, max_passes: int = 3) -> str:
    """Décode les entités HTML (`&quot;`, `&#039;`, `&amp;`...). Gère le double encodage."""
    for _ in range(max_passes):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def strip_html(text: str) -> str:
    """Retire les balises et le contenu de `<script>` / `<style>`. Les entités sont laissées."""
    text = _RE_COMMENT.sub("", text)
    text = _RE_SCRIPT_STYLE.sub("", text)
    text = _RE_BLOCK_TAG.sub(" ", text)
    return _RE_TAG.sub("", text)


def normalize_whitespace(text: str) -> str:
    """Espaces insécables, tabulations et retours à la ligne -> une espace ; invisibles retirés."""
    return re.sub(r"\s+", " ", text.translate(_INVISIBLE)).strip()


def clean_text(text: str | None) -> str:
    """Enchaîne les corrections de texte : encodage, entités, HTML, espaces."""
    if not text:
        return ""
    text = fix_mojibake(text)
    text = decode_entities(text)
    text = strip_html(text)
    return normalize_whitespace(text)


def truncate_extract(text: str, max_chars: int = MAX_EXTRACT_CHARS) -> str:
    """Borne un extrait à `max_chars` caractères, points de suspension compris.

    La coupe se fait sur un espace (jamais au milieu d'un mot, sauf s'il n'y en a pas assez
    près de la fin), sans ponctuation avant les points de suspension.
    """
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    space = cut.rfind(" ")
    if space >= max_chars // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-.") + ELLIPSIS


def to_utc(value: str | datetime | None) -> str | None:
    """Date (ISO 8601, format e-mail `Thu, 12 Nov 2026 06:00:25 +0100`, ou datetime) -> ISO en UTC.

    Une date sans fuseau est lue comme étant déjà en UTC. Vide ou illisible : None.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def normalize_item(item: Mapping[str, Any], *, max_extract: int = MAX_EXTRACT_CHARS) -> dict:
    """Article brut -> article normalisé (nouveau dictionnaire ; l'entrée n'est pas modifiée).

    Titre, rubrique et extrait sont nettoyés ; l'extrait est borné ; `publie_le` et `recupere_le`
    passent en UTC. `id`, `source`, `lien` et `payload` sont repris tels quels.
    """
    return {
        **item,
        "titre": clean_text(item.get("titre")),
        "resume": truncate_extract(clean_text(item.get("resume")), max_extract),
        "rubrique": clean_text(item.get("rubrique")) or None,
        "publie_le": to_utc(item.get("publie_le")),
        "recupere_le": to_utc(item.get("recupere_le")),
    }
