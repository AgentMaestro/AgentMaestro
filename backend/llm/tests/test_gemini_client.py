import asyncio
from types import SimpleNamespace

from llm.services.providers.gemini_client import GeminiClient
from llm.services.providers.gemini_http import GeminiHTTPService


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


def test_gemini_complete_delegates_to_http_service():
    client = GeminiClient.__new__(GeminiClient)

    async def _fake_complete(*args, **kwargs):
        return {
            "text": "Use repo_tree.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "name": "repo_tree",
                    "arguments": '{"root":"backend"}',
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
            "raw": {"id": "gemini-response-1"},
        }

    client.http_service = SimpleNamespace(complete=_fake_complete)

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


def test_gemini_http_build_contents_uses_full_history_on_fresh_turn():
    service = GeminiHTTPService(base_url="https://example.com", api_key="test")

    contents, instruction = service._build_contents(
        [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "old answer"},
            {"role": "tool", "content": '{"tool_name": "file_read", "tool_result": {"ok": true}}', "provider_call_id": "call_old", "tool_name": "file_read"},
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "tool request"},
            {"role": "tool", "content": '{"tool_name": "file_read", "tool_result": {"ok": true}}', "provider_call_id": "call_new", "tool_name": "file_read"},
        ]
    )

    assert instruction == "system"
    assert [item["role"] for item in contents] == ["model", "user", "user", "model", "user"]
    assert contents[0]["parts"][0]["text"] == "old answer"
    assert contents[1]["parts"][0]["functionResponse"]["name"] == "file_read"
    assert contents[-1]["parts"][0]["functionResponse"]["name"] == "file_read"


def test_gemini_http_build_contents_uses_last_user_turn_on_continuation():
    service = GeminiHTTPService(base_url="https://example.com", api_key="test")

    contents, instruction = service._build_contents(
        [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "old answer"},
            {"role": "tool", "content": '{"tool_name": "file_read", "tool_result": {"ok": true}}', "provider_call_id": "call_old", "tool_name": "file_read"},
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "tool request"},
            {"role": "tool", "content": '{"tool_name": "file_read", "tool_result": {"ok": true}}', "provider_call_id": "call_new", "tool_name": "file_read"},
        ],
        previous_response_id="resp_123",
        outstanding_provider_call_id="call_new",
    )

    assert instruction == "system"
    assert [item["role"] for item in contents] == ["user", "model", "user"]
    assert contents[0]["parts"][0]["text"] == "new question"
    assert contents[-1]["parts"][0]["functionResponse"]["name"] == "file_read"


def test_gemini_http_build_contents_serializes_assistant_tool_calls():
    service = GeminiHTTPService(base_url="https://example.com", api_key="test")

    contents, instruction = service._build_contents(
        [
            {"role": "system", "content": "system"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "name": "remember",
                        "arguments": {"content": "keep this"},
                    }
                ],
            },
            {
                "role": "tool",
                "content": '{"tool_name": "remember", "tool_result": {"ok": true}}',
                "provider_call_id": "call_123",
                "tool_name": "remember",
            },
        ]
    )

    assert instruction == "system"
    assert [item["role"] for item in contents] == ["model", "user"]
    assert contents[0]["parts"][0]["functionCall"]["name"] == "remember"
    assert contents[0]["parts"][0]["functionCall"]["args"] == {"content": "keep this"}
    assert contents[1]["parts"][0]["functionResponse"]["name"] == "remember"


def test_gemini_unsupported_transport_coerces_to_http(monkeypatch):
    client = GeminiClient.__new__(GeminiClient)
    client.transport = "ws"
    monkeypatch.setenv("GEMINI_TRANSPORT", "ws")

    assert client.resolve_transport() == "http"
