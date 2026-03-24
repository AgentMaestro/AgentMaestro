import asyncio
from types import SimpleNamespace

import pytest
from agents.consumers import AgentChatConsumer
from agents.models import Agent
from core.models import Workspace
from django.contrib.auth import get_user_model
from django.utils import timezone
from runs.models import AgentRun, AgentStep
from tools.models import ToolCall


def test_flush_pending_tool_results(monkeypatch):
    consumer = AgentChatConsumer(scope={"type": "websocket"})
    consumer.channel_name = "test-channel"
    consumer.run_id = "run-123"
    sent = []

    async def fake_send(payload):
        sent.append(payload)

    consumer.send_json = fake_send

    expected_payload = {
        "tool_call_id": "call-1",
        "tool_name": "repo_tree",
        "status": "COMPLETED",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "result": {"ok": True},
        "duration_ms": 42,
        "run_id": "run-123",
        "correlation_id": "corr-1",
    }

    monkeypatch.setattr(
        "agents.consumers.pop_pending_tool_results",
        lambda run_id, limit=20: [dict(expected_payload)],
    )

    asyncio.run(consumer._flush_pending_tool_results())
    assert sent == [{"type": "tool_result", **expected_payload}]


@pytest.mark.django_db(transaction=True)
def test_flush_pending_tool_results_replays_persisted_tail_when_redis_is_empty(monkeypatch):
    user = get_user_model().objects.create_user(username="replay-user")
    workspace = Workspace.objects.create(name="replay-ws")
    agent = Agent.objects.create(
        workspace=workspace, owner=user, name="Replay Agent", soul="Prompt"
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="prompt",
        previous_response_id="resp_1",
    )
    step = AgentStep.objects.create(
        run=run,
        step_index=1,
        kind=AgentStep.Kind.TOOL_CALL,
        payload={"tool_name": "repo_tree", "args": {"scope": "backend"}},
    )
    timestamp = timezone.now()
    tool_call = ToolCall.objects.create(
        run=run,
        step=step,
        tool_name="repo_tree",
        args={"scope": "backend"},
        status=ToolCall.Status.COMPLETED,
        exit_code=0,
        stdout="",
        stderr="",
        result={"ok": True},
        provider_call_id="call-123",
        correlation_id=step.correlation_id,
        started_at=timestamp,
        ended_at=timestamp,
    )

    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.run_id = str(run.id)
    consumer.provider = "openai"
    consumer.model_name = "gpt-5.4"
    consumer.session = SimpleNamespace(previous_response_id="resp_1")
    consumer._tool_result_event = asyncio.Event()
    consumer._tool_result_event.set()
    sent = []

    async def fake_send(payload):
        sent.append(payload)

    consumer.send_json = fake_send
    monkeypatch.setattr("agents.consumers.pop_pending_tool_results", lambda run_id, limit=20: [])

    asyncio.run(consumer._flush_pending_tool_results())

    assert sent == [
        {
            "type": "tool_result",
            "tool_call_id": str(tool_call.id),
            "status": "COMPLETED",
            "tool_name": "repo_tree",
            "stdout": "",
            "stderr": "",
            "result": {"ok": True},
            "run_id": str(run.id),
            "correlation_id": str(step.correlation_id),
            "provider_call_id": "call-123",
            "duration_ms": 0,
        }
    ]


@pytest.mark.asyncio
async def test_push_drops_stale_run_events():
    consumer = AgentChatConsumer(scope={"type": "websocket"})
    consumer.run_id = "current-run"
    sent = []

    async def fake_send(payload):
        sent.append(payload)

    consumer.send_json = fake_send

    event = {
        "type": "push",
        "payload": {
            "type": "push",
            "topic": "run.event",
            "event": "tool_denied",
            "run_id": "stale-run",
            "data": {
                "run_id": "stale-run",
                "tool_call_id": "call-1",
                "tool_name": "file_delete",
                "status": "DENIED",
                "error": "Tool call denied.",
            },
        },
    }

    await consumer.push(event)

    assert sent == []
