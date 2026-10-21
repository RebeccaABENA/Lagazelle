"""Compteur d'échecs consécutifs, mise en sommeil et réveil des sources."""

import httpx
import pytest
from conftest import DAY

from gazette.collect.runner import collect_source, collectable_sources, run_collection
from gazette.sources import SLEEP_THRESHOLD

BROKEN_FEED = "https://panne.example.fr/rss.xml"
PQR_FEED = "https://pqr.example.fr/rss.xml"


@pytest.fixture
def broken_web(web):
    web.add(BROKEN_FEED, httpx.Response(503))
    web.add(PQR_FEED, httpx.Response(200, content=b"<rss version='2.0'><channel/></rss>"))
    return web


def _collect(settings, repo, fetcher, slug, **kwargs):
    return collect_source(repo.get(slug), settings, DAY, fetcher, repo, **kwargs)


def test_failure_counter_and_sleep_after_three_failures(settings, repo, broken_web, make_fetcher):
    for expected in (1, 2, 3):
        result = _collect(settings, repo, make_fetcher(), "gazette-en-panne")
        assert result.status == "failed"
        assert result.consecutive_failures == expected

    source = repo.get("gazette-en-panne")
    assert source.consecutive_failures == SLEEP_THRESHOLD
    assert source.sleeping and source.state == "en sommeil"
    assert "gazette-en-panne" not in [s.slug for s in collectable_sources(repo)]

    # Collecte suivante : la source en sommeil n'est plus interrogée...
    broken_web.requests.clear()
    manifest = run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo)
    assert BROKEN_FEED not in broken_web.urls()
    # ...mais elle reste visible dans le manifeste, avec la commande de réveil.
    entry = next(e for e in manifest["sources"] if e["slug"] == "gazette-en-panne")
    assert entry["status"] == "sleeping"
    assert "sources wake gazette-en-panne" in entry["error"]
    assert manifest["summary"]["sleeping"] == 1


def test_wake_resets_counter_and_source_is_collected_again(
    settings, repo, broken_web, make_fetcher
):
    for _ in range(SLEEP_THRESHOLD):
        _collect(settings, repo, make_fetcher(), "gazette-en-panne")
    assert repo.get("gazette-en-panne").sleeping

    woken = repo.wake("gazette-en-panne")
    assert woken.consecutive_failures == 0 and not woken.sleeping

    broken_web.add(BROKEN_FEED, httpx.Response(200, content=b"<rss version='2.0'><channel/></rss>"))
    run_collection(settings, DAY, fetcher=make_fetcher(), repo=repo)
    assert BROKEN_FEED in broken_web.urls()
    assert repo.get("gazette-en-panne").last_success is not None


def test_success_resets_counter(settings, repo, broken_web, make_fetcher):
    _collect(settings, repo, make_fetcher(), "gazette-en-panne")
    _collect(settings, repo, make_fetcher(), "gazette-en-panne")
    broken_web.add(BROKEN_FEED, httpx.Response(200, content=b"<rss version='2.0'><channel/></rss>"))
    result = _collect(settings, repo, make_fetcher(), "gazette-en-panne")
    assert result.status == "ok"
    assert repo.get("gazette-en-panne").consecutive_failures == 0


def test_transient_failure_counted_only_on_final_attempt(settings, repo, broken_web, make_fetcher):
    # Relances Airflow : essais 1 et 2 non comptés, le 3e (dernier) l'est.
    first = _collect(settings, repo, make_fetcher(), "gazette-en-panne", final_attempt=False)
    assert first.retryable is True and first.consecutive_failures is None
    assert repo.get("gazette-en-panne").consecutive_failures == 0
    _collect(settings, repo, make_fetcher(), "gazette-en-panne", final_attempt=True)
    assert repo.get("gazette-en-panne").consecutive_failures == 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(200, content=b"<html><body>pas un flux</body"), "ParseError"),
    ],
)
def test_permanent_errors_are_not_retried_and_counted_at_once(
    settings, repo, web, make_fetcher, response, error
):
    web.add(BROKEN_FEED, response)
    result = _collect(settings, repo, make_fetcher(), "gazette-en-panne", final_attempt=False)
    assert result.status == "failed"
    assert error in result.error
    assert result.retryable is False  # le DAG lève AirflowFailException : pas de relance
    assert result.consecutive_failures == 1
    assert web.urls().count(BROKEN_FEED) == 1  # pas de relance tenacity non plus
