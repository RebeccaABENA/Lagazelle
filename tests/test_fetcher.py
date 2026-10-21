import httpx
import pytest
from conftest import fixture_bytes

from gazette.collect.fetcher import FetchError, RobotsDisallowed

FEED = "https://pqn.example.fr/rss/une.xml"


def test_user_agent_identifies_project_with_contact(web, make_fetcher):
    web.add(FEED, httpx.Response(200, content=fixture_bytes("feed_pqn.xml")))
    make_fetcher().fetch(FEED)
    agents = {r.headers["User-Agent"] for r in web.requests}
    assert agents == {
        "LaGazette/0.1.0 (+https://github.com/RebeccaABENA/Lagazelle; contact: test@example.org)"
    }


def test_robots_txt_disallow_blocks_the_feed(web, make_fetcher):
    web.add(
        "https://pqn.example.fr/robots.txt",
        httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
    )
    web.add(FEED, httpx.Response(200, content=fixture_bytes("feed_pqn.xml")))

    with pytest.raises(RobotsDisallowed) as exc_info:
        make_fetcher().fetch(FEED)

    assert exc_info.value.retryable is False
    # Le flux n'est jamais demandé : seul robots.txt l'a été.
    assert web.urls() == ["https://pqn.example.fr/robots.txt"]


def test_robots_txt_is_checked_once_per_host(web, make_fetcher):
    web.add(FEED, httpx.Response(200, content=b"<rss/>"))
    fetcher = make_fetcher()
    fetcher.fetch(FEED)
    fetcher.fetch(FEED)
    assert web.urls().count("https://pqn.example.fr/robots.txt") == 1


def test_unreachable_robots_txt_means_no_access(web, make_fetcher):
    web.add("https://pqn.example.fr/robots.txt", httpx.Response(503))
    with pytest.raises(RobotsDisallowed):
        make_fetcher().fetch(FEED)
    assert FEED not in web.urls()


def test_retries_three_times_then_fails(web, make_fetcher):
    web.add(FEED, httpx.Response(503))
    with pytest.raises(FetchError, match="HTTP 503 après 3 essais"):
        make_fetcher().fetch(FEED)
    assert web.urls().count(FEED) == 3


def test_transient_network_error_is_retried(web, make_fetcher):
    attempts = []

    def flaky(request):
        attempts.append(request)
        if len(attempts) < 3:
            raise httpx.ConnectTimeout("délai dépassé", request=request)
        return httpx.Response(200, content=b"<rss/>")

    web.add(FEED, flaky)
    assert make_fetcher().fetch(FEED).status == 200
    assert len(attempts) == 3


def test_pause_between_requests_to_same_domain(web, make_fetcher):
    web.add(FEED, httpx.Response(200, content=b"<rss/>"))
    fetcher = make_fetcher()
    fetcher.fetch(FEED)
    # robots.txt puis le flux sur le même hôte : au moins une pause demandée.
    assert fetcher.sleeps
    assert all(0 < s <= 0.5 for s in fetcher.sleeps)


def test_conditional_get_uses_saved_etag(web, make_fetcher):
    def feed(request):
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"<rss/>", headers={"ETag": '"v1"'})

    web.add(FEED, feed)
    fetcher = make_fetcher()
    first = fetcher.fetch(FEED)
    assert first.status == 200
    fetcher.remember(first)

    second = make_fetcher().fetch(FEED)  # nouveau processus : état relu sur disque
    assert second.not_modified
    assert make_fetcher().fetch(FEED, conditional=False).status == 200
