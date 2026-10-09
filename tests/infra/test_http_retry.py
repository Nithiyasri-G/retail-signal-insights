import requests

from market_intelligence.infra import http as http_client


class _Response:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._payload


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(http_client.time, "sleep", lambda seconds: None)


def test_get_is_retried_after_a_server_error(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        return _Response(500) if len(calls) < 3 else _Response(200)

    monkeypatch.setattr(http_client.requests, "get", fake_get)
    ok, data, message = http_client.safe_request("https://api.fda.gov/food/enforcement.json", params={"limit": 1})
    assert ok and data == {"ok": True} and message == "success"
    assert len(calls) == 3


def test_get_gives_up_with_a_short_message(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(http_client.requests, "get", lambda *a, **k: calls.append(1) or _Response(500))
    ok, data, message = http_client.safe_request("https://api.fda.gov/food/enforcement.json?search=a+b")
    assert not ok and data is None
    assert len(calls) == http_client.GET_ATTEMPTS
    assert message == "api.fda.gov temporarily unavailable (HTTP 500)"
    assert "search=" not in message


def test_client_errors_are_not_retried(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(http_client.requests, "get", lambda *a, **k: calls.append(1) or _Response(404))
    ok, _, message = http_client.safe_request("https://api.fda.gov/x")
    assert not ok and len(calls) == 1
    assert "HTTP 404" in message


def test_post_is_never_retried(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(http_client.requests, "post", lambda *a, **k: calls.append(1) or _Response(503))
    ok, _, _ = http_client.safe_request("https://example.test/chat", method="POST", json_body={})
    assert not ok and len(calls) == 1
