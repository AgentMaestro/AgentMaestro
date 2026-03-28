import httpx
import pytest
from django.test import override_settings

from google_bridge.services.http import request_with_retries


class _FakeClient:
    init_kwargs: list[dict[str, object]] = []
    scripted: list[object] = []
    calls: list[tuple[str, str]] = []

    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)
        type(self).init_kwargs.append(self.kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def request(self, method, url, params=None, json=None, data=None):
        type(self).calls.append((method, url))
        if not type(self).scripted:
            raise AssertionError("No scripted response available.")
        outcome = type(self).scripted.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(status_code: int, *, method: str = "GET", url: str = "https://example.com", headers: dict[str, str] | None = None, json_payload: dict | None = None):
    request = httpx.Request(method, url)
    return httpx.Response(status_code, headers=headers or {}, json=json_payload or {}, request=request)


@pytest.mark.django_db
def test_google_bridge_request_retries_on_retryable_status(monkeypatch):
    _FakeClient.init_kwargs.clear()
    _FakeClient.calls.clear()
    _FakeClient.scripted = [
        _response(503, headers={"Retry-After": "2"}),
        _response(200, json_payload={"ok": True}),
    ]
    sleep_calls: list[float] = []

    monkeypatch.setattr("google_bridge.services.http.httpx.Client", _FakeClient)
    monkeypatch.setattr("google_bridge.services.http.time.sleep", lambda delay: sleep_calls.append(delay))

    with override_settings(
        GOOGLE_BRIDGE_TIMEOUT_SECONDS=7.5,
        GOOGLE_BRIDGE_RETRY_ATTEMPTS=1,
        GOOGLE_BRIDGE_RETRY_BACKOFF_SECONDS=0.25,
        GOOGLE_BRIDGE_RETRY_MAX_BACKOFF_SECONDS=5.0,
    ):
        response = request_with_retries("GET", "https://example.com/test")

    assert response.status_code == 200
    assert _FakeClient.calls == [("GET", "https://example.com/test"), ("GET", "https://example.com/test")]
    assert sleep_calls == [2.0]
    assert _FakeClient.init_kwargs[0]["timeout"].read == 7.5


@pytest.mark.django_db
def test_google_bridge_request_retries_on_transport_error(monkeypatch):
    _FakeClient.init_kwargs.clear()
    _FakeClient.calls.clear()
    request = httpx.Request("POST", "https://example.com/test")
    _FakeClient.scripted = [
        httpx.ConnectError("boom", request=request),
        _response(200, method="POST", json_payload={"ok": True}),
    ]
    sleep_calls: list[float] = []

    monkeypatch.setattr("google_bridge.services.http.httpx.Client", _FakeClient)
    monkeypatch.setattr("google_bridge.services.http.time.sleep", lambda delay: sleep_calls.append(delay))

    with override_settings(
        GOOGLE_BRIDGE_TIMEOUT_SECONDS=9,
        GOOGLE_BRIDGE_RETRY_ATTEMPTS=1,
        GOOGLE_BRIDGE_RETRY_BACKOFF_SECONDS=0.5,
        GOOGLE_BRIDGE_RETRY_MAX_BACKOFF_SECONDS=5.0,
    ):
        response = request_with_retries("POST", "https://example.com/test", data={"a": "b"})

    assert response.status_code == 200
    assert _FakeClient.calls == [("POST", "https://example.com/test"), ("POST", "https://example.com/test")]
    assert sleep_calls == [0.5]
