import asyncio
from types import SimpleNamespace

from llm.services.providers.gemini_client import GeminiClient


class _FakeChatCompletions:
    def __init__(self, response):
        self._response = response

    async def create(self, **kwargs):
        return self._response


class _FakeResponse:
    def __init__(self):
        function = SimpleNamespace(name="repo_tree", arguments='{"root":"backend"}')
        tool_call = SimpleNamespace(id="call_123", function=function)
        message = SimpleNamespace(content="Use repo_tree.", tool_calls=[tool_call])
        choice = SimpleNamespace(message=message)
        self.choices = [choice]
        self.usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)

    def model_dump(self):
        return {"id": "gemini-response-1", "choices": [{"message": {"content": "Use repo_tree."}}]}


def test_gemini_complete_normalizes_tool_calls_and_usage():
    client = GeminiClient.__new__(GeminiClient)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeChatCompletions(_FakeResponse())))

    result = asyncio.run(
        client.complete(
            messages=[{"role": "user", "content": "Inspect the repo"}],
            model="gemini-2.5-pro",
            tools=[{"name": "repo_tree", "description": "List files", "parameters": {"type": "object"}}],
        )
    )

    assert result["text"] == "Use repo_tree."
    assert result["tool_calls"] == [
        {
            "id": "call_123",
            "name": "repo_tree",
            "arguments": '{"root":"backend"}',
        }
    ]
    assert result["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert result["raw"]["id"] == "gemini-response-1"


def test_gemini_unsupported_transport_coerces_to_http(monkeypatch):
    client = GeminiClient.__new__(GeminiClient)
    client.transport = "ws"
    monkeypatch.setenv("GEMINI_TRANSPORT", "ws")

    assert client.resolve_transport() == "http"
