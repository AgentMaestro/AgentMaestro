import json

from toolrunner.app.models import FetchUrlArgs
from toolrunner.app.tools import fetch_url as fetch_url_module


def test_fetch_url_rejects_unsafe_url():
    response = fetch_url_module.run_fetch_url(None, FetchUrlArgs(url="http://127.0.0.1/test"))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool_runner.UNSAFE_URL"


def test_fetch_url_success_and_truncation(monkeypatch):
    monkeypatch.setattr(
        fetch_url_module,
        "_fetch_url",
        lambda url: {
            "status_code": 200,
            "final_url": url,
            "content_type": "text/html",
            "encoding": "utf-8",
            "body": b"<html><title>Example</title><body><main>Hello world from fetched content.</main></body></html>",
            "download_truncated": False,
        },
    )
    response = fetch_url_module.run_fetch_url(
        None,
        FetchUrlArgs(url="https://example.com", max_chars=10),
    )
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["result"]["title"] == "Example"
    assert payload["result"]["truncated"] is True


def test_fetch_url_timeout(monkeypatch):
    def _raise(_url):
        raise fetch_url_module.httpx.TimeoutException("timeout")

    monkeypatch.setattr(fetch_url_module, "_fetch_url", _raise)
    response = fetch_url_module.run_fetch_url(None, FetchUrlArgs(url="https://example.com"))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool_runner.TIMEOUT"
