"""Flux RSS 2.0 / Atom -> articles bruts (champs du dictionnaire docs/donnees.md).

On ne lit volontairement ni l'auteur, ni le contenu intégral (`content:encoded`),
ni les commentaires : aucune donnée personnelle, jamais le texte complet.
Le `guid` / `id` du flux n'est pas utilisé pour dédupliquer : la clé est (source, lien).
"""

from __future__ import annotations

import calendar
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
from pydantic import BaseModel

# Paramètres de suivi retirés pour calculer le lien canonique.
_TRACKING_PARAMS = frozenset({"xtor", "xts", "at_medium", "at_campaign", "at_source", "fbclid"})


class ParseError(ValueError):
    """Contenu illisible comme flux RSS/Atom."""


class Item(BaseModel):
    """Un article brut, tel que stocké dans data/raw/<date>/<type>/<slug>.json."""

    id: str
    source: str
    lien: str
    titre: str
    resume: str
    publie_le: str | None
    recupere_le: str
    rubrique: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class ParsedFeed:
    received: int  # entrées présentes dans le flux
    items: list[Item]  # articles retenus (avec lien, sans doublon dans le flux)


def canonical_url(url: str) -> str:
    """Lien normalisé pour la déduplication : schéma/hôte en minuscules, sans fragment
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


def item_id(source: str, url: str) -> str:
    """Clé de déduplication : sha256 de « source + lien canonique »."""
    return hashlib.sha256(f"{source}\n{canonical_url(url)}".encode()).hexdigest()


def _raw_get(entry: dict, key: str) -> Any:
    """Lecture sans les correspondances implicites de feedparser (`updated` -> `published`,
    dépréciées) : on ne garde que ce que le flux contient vraiment."""
    return dict.get(entry, key)


def _entry_link(entry: dict) -> str | None:
    if entry.get("link"):
        return entry["link"].strip()
    for link in entry.get("links", []):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link["href"].strip()
    return None


def _entry_summary(entry: dict) -> str:
    """Extrait fourni par le flux (RSS `description`, Atom `summary`). Sans extrait,
    feedparser recopie `content:encoded` (souvent le texte intégral) dans `summary` :
    dans ce cas on renvoie une chaîne vide."""
    summary = entry.get("summary", "")
    contents = [c.get("value") for c in entry.get("content", [])]
    if summary and summary in contents:
        return ""
    return summary


def _entry_published(entry: dict, tz) -> str | None:
    """Date de publication normalisée en ISO 8601 (RSS `pubDate`, Atom `published`,
    à défaut `updated`). None si absente ou illisible ; la valeur brute reste dans payload."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = _raw_get(entry, key)
        if parsed:
            stamp = datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
            return stamp.astimezone(tz).isoformat()
    return None


def _entry_section(entry: dict) -> str | None:
    """Rubrique : première catégorie du flux (RSS `category`, Atom `category term`)."""
    for tag in entry.get("tags", []):
        term = (tag.get("term") or tag.get("label") or "").strip()
        if term:
            return term
    return None


def _entry_payload(entry: dict) -> dict[str, Any]:
    """Champs bruts du flux conservés pour rejouer le nettoyage, par liste blanche :
    jamais d'auteur, de contributeur, de commentaire ni de contenu intégral."""
    payload: dict[str, Any] = {}
    for key in ("id", "title", "link", "published", "updated"):
        if _raw_get(entry, key):
            payload[key] = _raw_get(entry, key)
    summary = _entry_summary(entry)
    if summary:
        payload["summary"] = summary
    links = [
        {k: link[k] for k in ("href", "rel", "type") if link.get(k)}
        for link in entry.get("links", [])
        if link.get("href")
    ]
    if links:
        payload["links"] = links
    tags = [
        {k: tag[k] for k in ("term", "scheme", "label") if tag.get(k)}
        for tag in entry.get("tags", [])
    ]
    if any(tags):
        payload["tags"] = [t for t in tags if t]
    return payload


def parse_feed(content: bytes, fetched_at: datetime, source: str) -> ParsedFeed:
    """Parse un flux RSS 2.0 ou Atom. L'extrait est conservé tel que fourni (HTML compris)."""
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
        uid = item_id(source, link)
        if uid in seen:
            continue
        seen.add(uid)
        items.append(
            Item(
                id=uid,
                source=source,
                lien=link,
                titre=entry.get("title", "").strip(),
                resume=_entry_summary(entry),
                publie_le=_entry_published(entry, fetched_at.tzinfo),
                recupere_le=stamp,
                rubrique=_entry_section(entry),
                payload=_entry_payload(entry),
            )
        )
    return ParsedFeed(received=len(parsed.entries), items=items)
