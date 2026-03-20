from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from llm.models import LLMRun, ModelsAvailable
from llm.services.runner import LLMRunner


pytestmark = pytest.mark.django_db(transaction=True)


RETRYABLE_STATUS_CODES = (408, 409, 429, 500, 502, 503, 504)


class FakeProviderError(Exception):
    def __init__(self, status_code: int, message: str | None = None):
        self.status_code = status_code
        self.status = status_code
        self.code = str(status_code)
        self.classification = "network_error"
        self.request_id = f"req_{status_code}"
        super().__init__(message or f"provider error {status_code}")


class StubClient:
    def __init__(
        self,
        *,
        provider: str,
        behavior_by_model: dict[str, list[dict[str, Any]]],
        call_log: list[tuple[str, str]],
    ):
        self.provider = provider
        self.behavior_by_model = behavior_by_model
        self.call_log = call_log
        self.transport = "http"

    def resolve_transport(self) -> str:
        return "http"

    def is_transient_error(self, exc: Exception) -> bool:
        return int(getattr(exc, "status_code", 0) or 0) in RETRYABLE_STATUS_CODES

    async def complete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        model = str(kwargs.get("model") or "").strip()
        self.call_log.append((self.provider, model))
        queue = self.behavior_by_model.setdefault(model, [])
        if not queue:
            return {
                "text": f"{model} success",
                "tool_calls": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "raw": {"model": model},
            }
        action = queue.pop(0)
        if action["type"] == "error":
            raise FakeProviderError(int(action["status_code"]))
        return action["response"]


def _make_profile_suffix() -> str:
    return uuid.uuid4().hex[:8]


def _make_backup_models() -> list[dict[str, str]]:
    return [
        {"company": "google", "api": "gemini", "name": "gemini-2.5-flash"},
        {"company": "google", "api": "gemini", "name": "gemini-2.5-pro"},
    ]


def _seed_backup_models() -> None:
    for name in ("gemini-2.5-flash", "gemini-2.5-pro"):
        ModelsAvailable.objects.get_or_create(
            company="google",
            api="gemini",
            name=name,
            defaults={"metadata": {}},
        )


async def _noop_sleep(*args: Any, **kwargs: Any) -> None:
    return None


def _build_runner(
    monkeypatch,
    *,
    clients: dict[str, StubClient],
    call_log: list[tuple[str, str]],
) -> LLMRunner:
    runner = LLMRunner()
    monkeypatch.setattr("llm.services.runner.get_client", lambda provider: clients[provider])
    monkeypatch.setattr("llm.services.providers.retry.asyncio.sleep", _noop_sleep)
    return runner


def _run_failover_scenario(
    monkeypatch,
    *,
    retry_same_model_attempts: int,
    status_code: int,
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    _seed_backup_models()
    call_log: list[tuple[str, str]] = []
    primary_model = "primary-model"
    backup_1 = "gemini-2.5-flash"
    backup_2 = "gemini-2.5-pro"
    clients = {
        "openai": StubClient(
            provider="openai",
            behavior_by_model={
                primary_model: [{"type": "error", "status_code": status_code} for _ in range(retry_same_model_attempts + 1)],
            },
            call_log=call_log,
        ),
        "gemini": StubClient(
            provider="gemini",
            behavior_by_model={
                backup_1: [{"type": "error", "status_code": status_code} for _ in range(retry_same_model_attempts + 1)],
                backup_2: [
                    {
                        "type": "success",
                        "response": {
                            "text": "final answer",
                            "tool_calls": [],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                            "raw": {"model": backup_2},
                        },
                    }
                ],
            },
            call_log=call_log,
        ),
    }
    runner = _build_runner(monkeypatch, clients=clients, call_log=call_log)
    result = asyncio.run(
        runner.run(
            prompt="trigger failover",
            provider="openai",
            model_name=primary_model,
            tools=[],
            backup_models=_make_backup_models(),
            backup_retry_policy={
                "retry_same_model_attempts": retry_same_model_attempts,
                "retryable_status_codes": [408, 409, 429, 500, 502, 503, 504],
            },
            max_tool_rounds=1,
        )
    )
    return result, call_log


@pytest.mark.parametrize("status_code", RETRYABLE_STATUS_CODES)
def test_model_failover_retries_retryable_status_codes_and_preserves_backup_order(monkeypatch, status_code):
    result, call_log = _run_failover_scenario(
        monkeypatch,
        retry_same_model_attempts=1,
        status_code=status_code,
    )

    assert result["status"] == "completed"
    assert result["text"] == "final answer"
    assert result["error"] is None
    assert call_log == [
        ("openai", "primary-model"),
        ("openai", "primary-model"),
        ("gemini", "gemini-2.5-flash"),
        ("gemini", "gemini-2.5-flash"),
        ("gemini", "gemini-2.5-pro"),
    ]
    run = LLMRun.objects.get(id=result["run_id"])
    assert run.provider == "gemini"
    assert run.model == "gemini-2.5-pro"


def test_model_failover_honors_zero_same_model_retries(monkeypatch):
    result, call_log = _run_failover_scenario(
        monkeypatch,
        retry_same_model_attempts=0,
        status_code=503,
    )

    assert result["status"] == "completed"
    assert call_log == [
        ("openai", "primary-model"),
        ("gemini", "gemini-2.5-flash"),
        ("gemini", "gemini-2.5-pro"),
    ]
    run = LLMRun.objects.get(id=result["run_id"])
    assert run.provider == "gemini"
    assert run.model == "gemini-2.5-pro"


def test_model_failover_does_not_switch_on_nonretryable_error(monkeypatch):
    _seed_backup_models()
    call_log: list[tuple[str, str]] = []

    class NonRetryableClient(StubClient):
        def is_transient_error(self, exc: Exception) -> bool:
            return False

    clients = {
        "openai": NonRetryableClient(
            provider="openai",
            behavior_by_model={
                "primary-model": [{"type": "error", "status_code": 400}],
            },
            call_log=call_log,
        ),
        "gemini": StubClient(
            provider="gemini",
            behavior_by_model={},
            call_log=call_log,
        ),
    }
    runner = _build_runner(monkeypatch, clients=clients, call_log=call_log)
    result = asyncio.run(
        runner.run(
            prompt="no failover",
            provider="openai",
            model_name="primary-model",
            tools=[],
            backup_models=_make_backup_models(),
            backup_retry_policy={
                "retry_same_model_attempts": 1,
                "retryable_status_codes": [408, 409, 429, 500, 502, 503, 504],
            },
            max_tool_rounds=1,
        )
    )

    assert result["status"] == "failed"
    assert "400" in result["error"]
    assert call_log == [("openai", "primary-model")]
    run = LLMRun.objects.get(id=result["run_id"])
    assert run.provider == "openai"
    assert run.model == "primary-model"
