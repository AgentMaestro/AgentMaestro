import json

from toolrunner.app.models import WebSearchArgs
from toolrunner.app.tools import web_search as web_search_module


class _FakeProvider:
    def search(self, query: str, max_results: int):
        return [
            web_search_module.SearchResult(
                title="AgentMaestro",
                url="https://example.com/agentmaestro",
                snippet=f"Result for {query}",
                source="fake",
            )
        ]


def test_web_search_success(monkeypatch):
    monkeypatch.setattr(web_search_module, "_get_provider", lambda: _FakeProvider())
    response = web_search_module.run_web_search(None, WebSearchArgs(query="agentmaestro", max_results=3))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["result"]["query"] == "agentmaestro"
    assert payload["result"]["results"][0]["source"] == "fake"


def test_web_search_provider_error(monkeypatch):
    monkeypatch.setattr(
        web_search_module,
        "_get_provider",
        lambda: (_ for _ in ()).throw(web_search_module.WebSearchError("CONFIG_ERROR", "missing key")),
    )
    response = web_search_module.run_web_search(None, WebSearchArgs(query="agentmaestro"))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool_runner.CONFIG_ERROR"
