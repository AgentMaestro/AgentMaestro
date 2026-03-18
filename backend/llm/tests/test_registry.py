from llm.services import registry


def test_registry_returns_openai_client(monkeypatch):
    registry._CLIENTS.clear()
    monkeypatch.setattr("llm.services.registry.OpenAIClient", lambda: "openai-client")

    assert registry.get_client("openai") == "openai-client"


def test_registry_returns_gemini_client(monkeypatch):
    registry._CLIENTS.clear()
    monkeypatch.setattr("llm.services.registry.GeminiClient", lambda: "gemini-client")

    assert registry.get_client("gemini") == "gemini-client"
