import json

import pytest
from django.conf import settings

from llm.services.providers.openai_client import OpenAIClient, _resolve_openai_base_url
from llm.services.providers.openai_http import OpenAIHTTPService
from llm.services.providers.openai_ws import OpenAIResponsesWebSocketSession, _classify_error


class _FakeWebSocket:
    def __init__(self, response_event: dict):
        self.closed = False
        self.sent_payloads: list[str] = []
        self._response_event = response_event

    async def send(self, payload: str) -> None:
        self.sent_payloads.append(payload)

    async def recv(self) -> str:
        return json.dumps(self._response_event)


def test_messages_to_responses_input_prefers_provider_call_id_for_tool_outputs():
    client = OpenAIClient.__new__(OpenAIClient)

    items = client._messages_to_responses_input(
        [
            {
                "role": "tool",
                "tool_call_id": "local-tool-call-id",
                "provider_call_id": "provider-call-id",
                "content": {"ok": False, "error": {"message": "use git_add"}},
            }
        ]
    )

    assert items == [
        {
            "type": "function_call_output",
            "call_id": "provider-call-id",
            "output": '{"ok": false, "error": {"message": "use git_add"}}',
        }
    ]


@pytest.mark.asyncio
async def test_ws_session_includes_tools_on_first_response_create(monkeypatch):
    session = OpenAIResponsesWebSocketSession(
        api_key="test-key",
        base_url="https://api.openai.com",
        model="gpt-5-mini",
        run_id="run-1",
        agent_id="agent-1",
    )
    session._ws = _FakeWebSocket(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_123",
                "output": [],
            },
        }
    )
    session.previous_response_id = None

    async def _noop_ensure_connection():
        return None

    monkeypatch.setattr(session, "_ensure_connection", _noop_ensure_connection)

    result = await session.create_or_continue(
        input_items=[
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            }
        ],
        tools=[
            {
                "type": "function",
                "name": "file_read",
                "description": "Read a local file path.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
    )

    assert result["response_id"] == "resp_123"
    sent_payload = json.loads(session._ws.sent_payloads[0])
    assert sent_payload["type"] == "response.create"
    assert sent_payload["store"] is True
    assert "tools" in sent_payload
    assert sent_payload["tools"][0]["name"] == "file_read"


@pytest.mark.asyncio
async def test_ws_session_stores_continuations_with_previous_response_id(monkeypatch):
    session = OpenAIResponsesWebSocketSession(
        api_key="test-key",
        base_url="https://api.openai.com",
        model="gpt-5-mini",
        run_id="run-2",
        agent_id="agent-2",
    )
    session._ws = _FakeWebSocket(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_456",
                "output": [],
            },
        }
    )
    session.previous_response_id = "resp_prev_123"

    async def _noop_ensure_connection():
        return None

    monkeypatch.setattr(session, "_ensure_connection", _noop_ensure_connection)

    await session.create_or_continue(
        input_items=[
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "continue"}],
            }
        ],
    )

    sent_payload = json.loads(session._ws.sent_payloads[0])
    assert sent_payload["store"] is True
    assert sent_payload["previous_response_id"] == "resp_prev_123"



def test_openai_preferred_transport_prefers_django_settings_over_env(monkeypatch):
    client = OpenAIClient.__new__(OpenAIClient)
    monkeypatch.setattr(settings, "OPENAI_TRANSPORT", "ws", raising=False)
    monkeypatch.setenv("OPENAI_TRANSPORT", "http")

    assert client.preferred_transport() == "ws"


def test_openai_preferred_transport_falls_back_to_env_then_default(monkeypatch):
    client = OpenAIClient.__new__(OpenAIClient)
    monkeypatch.setattr(settings, "OPENAI_TRANSPORT", None, raising=False)
    monkeypatch.setenv("OPENAI_TRANSPORT", "http")

    assert client.preferred_transport() == "http"

    monkeypatch.delenv("OPENAI_TRANSPORT", raising=False)
    assert client.preferred_transport() == "ws"



def test_openai_previous_response_not_found_detection_handles_http_errors():
    client = OpenAIClient.__new__(OpenAIClient)

    class _FakeHttpPreviousResponseNotFound(Exception):
        code = "previous_response_not_found"
        param = "previous_response_id"

        def __str__(self):
            return "Previous response with id 'resp_123' not found."

    assert client.is_previous_response_not_found(_FakeHttpPreviousResponseNotFound())


def test_openai_previous_response_not_found_detection_handles_missing_tool_output():
    client = OpenAIClient.__new__(OpenAIClient)

    class _FakeMissingToolOutput(Exception):
        def __str__(self):
            return "No tool output found for function call call_abc123."

    assert client.is_previous_response_not_found(_FakeMissingToolOutput())


def test_openai_previous_response_not_found_detection_handles_tool_call_mismatch():
    client = OpenAIClient.__new__(OpenAIClient)

    class _FakeToolCallMismatch(Exception):
        def __str__(self):
            return "No tool call found for function call output with call_id call_abc123."

    assert client.is_previous_response_not_found(_FakeToolCallMismatch())


def test_openai_ws_invalid_request_error_is_treated_as_validation_error():
    assert (
        _classify_error(
            "invalid_request_error",
            "No tool call found for function call output with call_id call_abc123.",
            400,
        )
        == "validation_error"
    )


def test_openai_base_url_is_normalized_to_v1(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    assert _resolve_openai_base_url() == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_openai_http_responses_payload_includes_previous_response_id():
    class _FakeResponses:
        def __init__(self):
            self.payload = None

        async def create(self, **kwargs):
            self.payload = kwargs
            return type("Resp", (), {"model_dump": lambda self: {"id": "resp_123", "output": []}})()

    class _FakeClient:
        def __init__(self):
            self.responses = _FakeResponses()

    service = OpenAIHTTPService(_FakeClient())

    await service.complete(
        [{"role": "user", "content": "hi"}],
        model="gpt-5-mini",
        previous_response_id="resp_prev_123",
    )

    assert service.client.responses.payload["previous_response_id"] == "resp_prev_123"


@pytest.mark.asyncio
async def test_openai_http_responses_uses_last_user_only_for_continuations():
    class _FakeResponses:
        def __init__(self):
            self.payload = None

        async def create(self, **kwargs):
            self.payload = kwargs
            return type("Resp", (), {"model_dump": lambda self: {"id": "resp_789", "output": []}})()

    class _FakeClient:
        def __init__(self):
            self.responses = _FakeResponses()

    service = OpenAIHTTPService(_FakeClient())

    await service.complete(
        [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new question"},
        ],
        model="gpt-5-mini",
        previous_response_id="resp_prev_789",
    )

    payload = service.client.responses.payload
    assert payload["previous_response_id"] == "resp_prev_789"
    assert len(payload["input"]) == 1
    assert payload["input"][0]["role"] == "user"
    assert payload["input"][0]["content"][0]["text"] == "new question"


@pytest.mark.asyncio
async def test_openai_http_responses_excludes_stale_tool_outputs_without_previous_response_id():
    class _FakeResponses:
        def __init__(self):
            self.payload = None

        async def create(self, **kwargs):
            self.payload = kwargs
            return type("Resp", (), {"model_dump": lambda self: {"id": "resp_999", "output": []}})()

    class _FakeClient:
        def __init__(self):
            self.responses = _FakeResponses()

    service = OpenAIHTTPService(_FakeClient())

    await service.complete(
        [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new question"},
            {"role": "tool", "content": '{"ok": true}', "tool_call_id": "local-1", "provider_call_id": "call_1"},
        ],
        model="gpt-5-mini",
    )

    payload = service.client.responses.payload
    assert "previous_response_id" not in payload
    assert all(item.get("type") != "function_call_output" for item in payload["input"])
    assert [item["role"] for item in payload["input"]] == ["system", "assistant", "user"]


@pytest.mark.asyncio
async def test_openai_http_responses_uses_tool_output_batch_continuation_input():
    class _FakeResponses:
        def __init__(self):
            self.payload = None

        async def create(self, **kwargs):
            self.payload = kwargs
            return type("Resp", (), {"model_dump": lambda self: {"id": "resp_456", "output": []}})()

    class _FakeClient:
        def __init__(self):
            self.responses = _FakeResponses()

    service = OpenAIHTTPService(_FakeClient())

    await service.complete(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": '{"ok": true}', "tool_call_id": "local-1", "provider_call_id": "call_1"},
            {"role": "tool", "content": '{"ok": true}', "tool_call_id": "local-2", "provider_call_id": "call_2"},
        ],
        model="gpt-5-mini",
        previous_response_id="resp_prev_456",
        outstanding_provider_call_ids=["call_1", "call_2"],
    )

    payload = service.client.responses.payload
    assert payload["previous_response_id"] == "resp_prev_456"
    assert len(payload["input"]) == 2
    assert [item["call_id"] for item in payload["input"]] == ["call_1", "call_2"]
