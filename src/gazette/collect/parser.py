"""Flux RSS/Atom -> items minimaux (titre, extrait, lien, date).

On ne lit volontairement ni l'auteur, ni le contenu intégral (`content:encoded`),
ni les commentaires : aucune donnée personnelle, jamais le texte complet.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
from pydantic import BaseModel

# Paramètres de suivi retirés pour calculer l'URL canonique.
_TRACKING_PARAMS = frozenset({"xtor", "xts", "at_medium", "at_campaign", "at_source", "fbclid"})


class ParseError(ValueError):
    """Contenu illisible comme flux RSS/Atom."""


class Item(BaseModel):
    id: str
    title: str
    summary: str
    link: str
    published: str | None
    fetched_at: str


def canonical_url(url: str) -> str:
    """URL normalisée pour la déduplication : schéma/hôte en minuscules, sans fragment
    ni paramètres de suivi (utm_*, xtor…)."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    netloc = parts.netloc.lower()
    if parts.scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    elif parts.scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(query), ""))


def item_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def _entry_link(entry: dict) -> str | None:
    if entry.get("link"):
        return entry["link"].strip()
    for link in entry.get("links", []):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link["href"].strip()
    return None


def _entry_summary(entry: dict) -> str:
    """Extrait fourni par le flux. Sans `description`, feedparser recopie `content:encoded`
    (souvent le texte intégral) dans `summary` : dans ce cas on renvoie une chaîne vide."""
    summary = entry.get("summary", "")
    contents = [c.get("value") for c in entry.get("content", [])]
    if summary and summary in contents:
        return ""
    return summary


def parse_feed(content: bytes, fetched_at: datetime) -> list[Item]:
    """Parse un flux. L'extrait est conservé tel que fourni (HTML compris, non nettoyé)."""
    parsed = feedparser.parse(content, sanitize_html=False)
    if parsed.bozo and not parsed.entries:
        raise ParseError(f"Flux illisible : {parsed.get('bozo_exception')!r}")

    stamp = fetched_at.isoformat()
    items: list[Item] = []
    seen: set[str] = set()
    for entry in parsed.entries:
        link = _entry_link(entry)
        if not link:
            continue
        uid = item_id(link)
        if uid in seen:
            continue
        seen.add(uid)
        items.append(
            Item(
                id=uid,
                title=entry.get("title", "").strip(),
                summary=_entry_summary(entry),
                link=link,
                published=entry.get("published") or entry.get("updated"),
                fetched_at=stamp,
            )
        )
    return items
