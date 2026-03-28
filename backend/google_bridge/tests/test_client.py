from __future__ import annotations

from types import SimpleNamespace

from google_bridge.services.client import GoogleBridgeClient


def test_google_bridge_client_gmail_filter_endpoints(monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]):
            self._payload = payload
            self.content = b"{}"
            self.text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_request_with_retries(method, url, headers=None, params=None, json=None, data=None):
        calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
            }
        )
        if method == "GET" and url.endswith("/settings/filters"):
            return FakeResponse({"filter": [{"id": "filter-1"}]})
        if method == "GET" and "/settings/filters/" in url:
            return FakeResponse({"id": "filter-1", "criteria": {"from": "alerts@example.com"}, "action": {"addLabelIds": ["Label_1"]}})
        if method == "POST":
            return FakeResponse({"id": "filter-2", "criteria": json.get("criteria", {}), "action": json.get("action", {})})
        if method == "DELETE":
            return FakeResponse({})
        raise AssertionError(f"Unexpected request: {method} {url}")

    monkeypatch.setattr("google_bridge.services.client.request_with_retries", fake_request_with_retries)
    monkeypatch.setattr(GoogleBridgeClient, "_access_token", lambda self: "access-token")

    client = GoogleBridgeClient(SimpleNamespace())

    list_result = client.list_gmail_filters()
    get_result = client.get_gmail_filter("filter-1")
    create_result = client.create_gmail_filter(
        criteria={"from": "alerts@example.com"},
        action={"addLabelIds": ["Label_1"]},
    )
    delete_result = client.delete_gmail_filter("filter-1")

    assert list_result["filter"][0]["id"] == "filter-1"
    assert get_result["id"] == "filter-1"
    assert create_result["id"] == "filter-2"
    assert delete_result == {}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/gmail/v1/users/me/settings/filters")
    assert calls[1]["url"].endswith("/gmail/v1/users/me/settings/filters/filter-1")
    assert calls[2]["method"] == "POST"
    assert calls[2]["json"] == {
        "criteria": {"from": "alerts@example.com"},
        "action": {"addLabelIds": ["Label_1"]},
    }
    assert calls[3]["method"] == "DELETE"
    assert calls[3]["url"].endswith("/gmail/v1/users/me/settings/filters/filter-1")
