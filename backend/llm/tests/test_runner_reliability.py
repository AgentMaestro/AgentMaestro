from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Iterable, Sequence

import pytest
from django.conf import settings

from llm.models import AgentRole, LLMModelProfile, LLMRun, LLMToolCall
from llm.services.runner import LLMRunner


pytestmark = pytest.mark.django_db(transaction=True)


class FakeClient:
    def __init__(self, responses: Sequence[Dict[str, Any]], *, transport: str = "http"):
        self._responses = list(responses)
        self.transport = transport

    async def complete(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if not self._responses:
            return {"text": "done", "tool_calls": [], "usage": {}}
        return self._responses.pop(0)

    def resolve_transport(self) -> str:
        return self.transport

    def is_transient_error(self, exc: Exception) -> bool:
        return False


async def _identity_retry(func, **kwargs):
    return await func()


def _make_profile(name_suffix: str | None = None, *, provider: str = "openai", model: str = "test-model") -> LLMModelProfile:
    suffix = name_suffix or uuid.uuid4().hex[:8]
    return LLMModelProfile.objects.create(
        name=f"reliability-{suffix}",
        agent_role=AgentRole.GENERIC,
        provider=provider,
        model=model,
        is_active=True,
    )


def _setup_runner(
    profile: LLMModelProfile,
    monkeypatch,
    client_responses: Iterable[Dict[str, Any]],
    run_tool_stub,
) -> LLMRunner:
    return _setup_runner_with_provider(
        profile,
        monkeypatch,
        FakeClient(client_responses),
        run_tool_stub,
    )


def _setup_runner_with_provider(
    profile: LLMModelProfile,
    monkeypatch,
    client,
    run_tool_stub,
) -> LLMRunner:
    runner = LLMRunner()

    async def fake_resolve(self, *args, **kwargs):
        return profile

    monkeypatch.setattr(LLMRunner, "_resolve_profile", fake_resolve)
    monkeypatch.setattr("llm.services.runner.retry_with_backoff", _identity_retry)
    monkeypatch.setattr("llm.services.runner.get_client", lambda provider: client)
    monkeypatch.setattr("llm.services.runner.run_tool", run_tool_stub)
    return runner


def _simple_tool_list() -> list[dict]:
    return [{"name": "repo_tree", "description": "", "parameters": {}}]


def _tool_call_response(identifier: str) -> dict[str, Any]:
    return {
        "text": "call tool",
        "tool_calls": [
            {"id": identifier, "name": "repo_tree", "arguments": "{}"},
        ],
        "usage": {},
    }


@pytest.mark.parametrize("tools", [_simple_tool_list()])
def test_toolrunner_down_does_not_crash(monkeypatch, tools):
    profile = _make_profile("down")
    responses = [
        _tool_call_response("down-1"),
        {"text": "done", "tool_calls": [], "usage": {}},
    ]

    async def run_tool_error(*args, **kwargs):
        return {"ok": False, "error": "server 500"}

    runner = _setup_runner(profile, monkeypatch, responses, run_tool_error)
    result = asyncio.run(
        runner.run(
            prompt="Describe repo",
            tools=tools,
            max_tool_rounds=1,
        )
    )

    assert result["status"] == "completed"
    assert result["tool_calls_executed"] == 1
    assert result["error"] is None
    run = LLMRun.objects.get(id=result["run_id"])
    tool_call = run.tool_calls.first()
    assert tool_call.success is False
    assert "server 500" in tool_call.error


@pytest.mark.parametrize("tools", [_simple_tool_list()])
def test_timeout_marks_run_failed(monkeypatch, tools):
    profile = _make_profile("timeout")
    responses = [
        _tool_call_response("timeout-1"),
    ]

    async def slow_tool(*args, **kwargs):
        await asyncio.sleep(0.05)
        return {"ok": True, "result": {"value": "ok"}}

    monkeypatch.setattr(settings, "TOOLRUNNER_TIMEOUT", 0.01)
    runner = _setup_runner(profile, monkeypatch, responses, slow_tool)
    result = asyncio.run(
        runner.run(
            prompt="Timeout test",
            tools=tools,
            max_tool_rounds=1,
        )
    )

    assert result["status"] == "failed"
    assert "TimeoutError" in result["error"]
    assert result["tool_calls_executed"] == 0


@pytest.mark.parametrize("tools", [_simple_tool_list()])
def test_large_output_flags_truncated(monkeypatch, tools):
    profile = _make_profile("trunc")
    responses = [
        _tool_call_response("trunc-1"),
        {"text": "done", "tool_calls": [], "usage": {}},
    ]

    async def truncated_tool(*args, **kwargs):
        return {"ok": True, "result": {"stdout_truncated": True}}

    runner = _setup_runner(profile, monkeypatch, responses, truncated_tool)
    result = asyncio.run(
        runner.run(
            prompt="Truncate",
            tools=tools,
            max_tool_rounds=1,
        )
    )

    assert result["status"] == "completed"
    run = LLMRun.objects.get(id=result["run_id"])
    tool_call = run.tool_calls.first()
    assert tool_call.result.get("stdout_truncated") is True


@pytest.mark.parametrize("tools", [_simple_tool_list()])
def test_ten_sequential_tool_calls(monkeypatch, tools):
    profile = _make_profile("session")
    responses = [_tool_call_response(f"session-{i}") for i in range(10)] + [{"text": "done", "tool_calls": [], "usage": {}}]

    async def success_tool(*args, **kwargs):
        return {"ok": True, "result": {"index": True}}

    runner = _setup_runner(profile, monkeypatch, responses, success_tool)
    result = asyncio.run(
        runner.run(
            prompt="Ten sequential calls",
            tools=tools,
            max_tool_rounds=1,
        )
    )

    assert result["status"] == "failed"
    assert result["error"] == "max_tool_rounds_exceeded"
    assert result["tool_calls_executed"] >= 1
    run = LLMRun.objects.get(id=result["run_id"])
    assert LLMToolCall.objects.filter(run=run).count() == result["tool_calls_executed"]


def test_gemini_http_path_completes_without_ws(monkeypatch):
    profile = _make_profile("gemini", provider="gemini", model="gemini-2.5-pro")
    client = FakeClient(
        [
            {
                "text": "Gemini answer",
                "tool_calls": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            }
        ],
        transport="http",
    )

    async def success_tool(*args, **kwargs):
        return {"ok": True, "result": {"index": True}}

    runner = _setup_runner_with_provider(profile, monkeypatch, client, success_tool)
    result = asyncio.run(
        runner.run(
            prompt="Gemini prompt",
            tools=[],
            max_tool_rounds=1,
        )
    )

    assert result["status"] == "completed"
    assert result["text"] == "Gemini answer"
    run = LLMRun.objects.get(id=result["run_id"])
    assert run.provider == "gemini"
    assert run.token_total == 17
