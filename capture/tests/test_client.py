import httpx
import pytest

from capture.claude_code.client import fetch_repositories, post_event


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


def test_fetch_repositories_returns_list():
    def handler(request):
        assert request.url.path == "/repositories"
        return httpx.Response(
            200, json=[{"repository_id": "r1", "project_id": "p1", "remote_url": "u"}]
        )

    with _client(handler) as http:
        repos = fetch_repositories(http, "http://test")
    assert repos[0]["repository_id"] == "r1"


def test_post_event_created():
    def handler(request):
        return httpx.Response(201, json={"status": "created", "run_id": "x", "event_id": "y"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1})
    assert (status, code) == ("created", 201)


def test_post_event_duplicate():
    def handler(request):
        return httpx.Response(200, json={"status": "duplicate", "run_id": "x", "event_id": "y"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1})
    assert (status, code) == ("duplicate", 200)


def test_post_event_4xx_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, json={"detail": "nope"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1}, sleep=lambda _: None)
    assert (status, code) == ("error", 404)
    assert calls["n"] == 1


def test_post_event_retries_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503)
        return httpx.Response(201, json={"status": "created"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1}, sleep=lambda _: None)
    assert status == "created"
    assert calls["n"] == 2


def test_post_event_raises_after_exhaustion():
    def handler(request):
        return httpx.Response(500)

    with _client(handler) as http:
        with pytest.raises(RuntimeError):
            post_event(http, "http://test", {"a": 1}, retries=2, sleep=lambda _: None)
