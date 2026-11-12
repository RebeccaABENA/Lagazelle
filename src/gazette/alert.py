"""Alertes vers le canal de l'équipe : webhook Discord ou Slack (GAZETTE_ALERT_WEBHOOK_URL).

Le format est choisi d'après l'URL : Discord attend {"content": ...}, Slack {"text": ...}.
Sans URL configurée, l'alerte est seulement journalisée (et la fonction renvoie False).
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

# Discord refuse les messages de plus de 2 000 caractères.
_MAX_LEN = 1900


def _payload(url: str, message: str) -> dict:
    host = urlsplit(url).hostname or ""
    if host.endswith(("discord.com", "discordapp.com")):
        return {"content": message[:_MAX_LEN], "username": "La gazette"}
    return {"text": message[:_MAX_LEN]}


def send_alert(
    webhook_url: str | None, message: str, *, client: httpx.Client | None = None
) -> bool:
    """Envoie `message`. Renvoie True si le webhook a accepté l'alerte.

    Ne lève jamais : une alerte qui échoue ne doit pas masquer l'erreur qu'elle signale.
    """
    if not webhook_url:
        log.warning("Aucun GAZETTE_ALERT_WEBHOOK_URL : alerte non envoyée.\n%s", message)
        return False
    owned = client is None
    client = client or httpx.Client(timeout=10)
    try:
        response = client.post(webhook_url, json=_payload(webhook_url, message))
        response.raise_for_status()
        log.info("Alerte envoyée (HTTP %s).", response.status_code)
        return True
    except httpx.HTTPError as exc:
        log.error("Échec de l'envoi de l'alerte : %s\n%s", exc, message)
        return False
    finally:
        if owned:
            client.close()


def format_check_alert(collection_date: str, summary: dict, report_path: str | None = None) -> str:
    failed = [r for r in summary["resultats"] if not r["ok"]]
    critical = bool(summary["critiques_en_echec"])
    head = "🔴" if critical else "🟠"
    lines = [
        f"{head} **La gazette — contrôles qualité du {collection_date}** : "
        f"{len(failed)} contrôle(s) en échec"
        + (" (dont critiques : l'édition ne doit pas partir telle quelle)" if critical else ""),
    ]
    for r in failed:
        detail = f" — {r['detail']}" if r["detail"] else ""
        lines.append(
            f"• `{r['id']}` ({r['gravite']}) : valeur {r['valeur']}, seuil {r['seuil']}{detail}"
        )
    if report_path:
        lines.append(f"Rapport : {report_path}")
    return "\n".join(lines)
