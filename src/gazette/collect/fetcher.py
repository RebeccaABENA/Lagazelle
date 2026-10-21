"""Client HTTP poli.

- User-Agent qui identifie le projet et donne un e-mail de contact ;
- robots.txt vérifié avant chaque flux (mis en cache par hôte pour la durée d'une collecte) ;
- timeouts, relances avec backoff exponentiel (3 essais au plus) ;
- pause minimale entre deux requêtes vers un même domaine ;
- GET conditionnels : l'appelant fournit l'ETag / Last-Modified lus en base
  (table `sources`) et les y réenregistre après un stockage réussi.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from gazette.config import Settings

log = logging.getLogger(__name__)

# Jeton recherché dans les sections `User-agent:` du robots.txt.
ROBOTS_TOKEN = "LaGazette"
# Erreurs serveur passagères : on relance.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    """Échec de récupération d'un flux."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class RobotsDisallowed(FetchError):
    """Le robots.txt de l'éditeur interdit l'accès : inutile de relancer."""

    def __init__(self, url: str):
        super().__init__(f"Accès interdit par robots.txt : {url}", retryable=False)


class _RetryableStatus(Exception):
    def __init__(self, response: httpx.Response):
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content: bytes
    fetched_at: datetime
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class PoliteFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        retry_wait: wait_base | None = None,
    ):
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout, connect=10.0),
            follow_redirects=True,
        )
        # Le User-Agent est imposé même sur un client fourni (tests).
        self.client.headers["User-Agent"] = settings.user_agent
        self._sleep = sleep
        self._clock = clock
        self._retry_wait = retry_wait or wait_exponential(multiplier=1, min=1, max=30)
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}

    # -- cycle de vie -------------------------------------------------------
    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> PoliteFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- bas niveau ---------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.settings.domain_delay - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def _request(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """GET avec pause par domaine et relances (erreurs réseau, 429 et 5xx)."""
        retrying = Retrying(
            stop=stop_after_attempt(self.settings.max_attempts),
            wait=self._retry_wait,
            retry=retry_if_exception_type((httpx.TransportError, _RetryableStatus)),
            sleep=self._sleep,
            reraise=True,
            before_sleep=lambda rs: log.warning(
                "Essai %d/%d échoué pour %s : %s",
                rs.attempt_number,
                self.settings.max_attempts,
                url,
                rs.outcome.exception() if rs.outcome else "?",
            ),
        )
        for attempt in retrying:
            with attempt:
                self._throttle(url)
                response = self.client.get(url, headers=headers)
                if response.status_code in RETRYABLE_STATUS:
                    raise _RetryableStatus(response)
                return response
        raise AssertionError("unreachable")  # pragma: no cover

    # -- robots.txt ---------------------------------------------------------
    def _load_robots(self, base: str) -> RobotFileParser:
        parser = RobotFileParser(f"{base}/robots.txt")
        try:
            response = self._request(f"{base}/robots.txt")
        except (httpx.TransportError, _RetryableStatus) as exc:
            # robots.txt injoignable : par prudence on s'abstient (RFC 9309, §2.3.1.4).
            log.warning("robots.txt injoignable pour %s (%s) : accès refusé", base, exc)
            parser.disallow_all = True
            return parser
        if response.status_code == 200:
            parser.parse(response.text.splitlines())
            parser.modified()
        elif 400 <= response.status_code < 500:
            # Pas de robots.txt (404, 410…) : aucune restriction (RFC 9309, §2.3.1.3).
            parser.allow_all = True
        else:
            parser.disallow_all = True
        return parser

    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            self._robots[base] = self._load_robots(base)
        return self._robots[base].can_fetch(ROBOTS_TOKEN, url)

    # -- API ----------------------------------------------------------------
    def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        """Récupère un flux, en GET conditionnel si des validateurs sont fournis.

        Lève RobotsDisallowed ou FetchError en cas d'échec.
        """
        if not self.allowed_by_robots(url):
            raise RobotsDisallowed(url)

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        fetched_at = self.settings.now()
        try:
            response = self._request(url, headers=headers)
        except httpx.TransportError as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        except _RetryableStatus as exc:
            status = exc.response.status_code
            raise FetchError(
                f"HTTP {status} après {self.settings.max_attempts} essais", status=status
            ) from exc

        if response.status_code == 304:
            return FetchResult(url=url, status=304, content=b"", fetched_at=fetched_at)
        if response.status_code != 200:
            raise FetchError(
                f"HTTP {response.status_code}", status=response.status_code, retryable=False
            )
        return FetchResult(
            url=url,
            status=200,
            content=response.content,
            fetched_at=fetched_at,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )
