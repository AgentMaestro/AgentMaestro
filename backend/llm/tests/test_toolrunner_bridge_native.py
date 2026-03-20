import asyncio
import pytest
import httpx
from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from llm.services.toolrunner_bridge import run_tool
from memory.models import MemoryRecord, ScheduledTask
from runs.models import AgentRun
from runs.services.memory import get_or_create_run_memory
from tools.models import ToolDefinition

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def orchestration_run():
    User = get_user_model()
    user = User.objects.create_user(username="bridge_native", password="x")
    workspace = Workspace.objects.create(name="Bridge Native Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Bridge Native Agent",
        soul="Use native tools",
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.API,
        execution_mode=AgentRun.ExecutionMode.HEADLESS,
        trigger_kind=AgentRun.TriggerKind.SYSTEM,
        input_text="headless native bridge test",
    )
    get_or_create_run_memory(run)
    ToolDefinition.objects.create(
        workspace=workspace,
        name="file_read",
        enabled=True,
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    )
    return run



def test_run_tool_executes_native_remember(orchestration_run):
    result = asyncio.run(run_tool(
        "remember",
        {
            "scope_type": "agent",
            "scope_id": str(orchestration_run.agent_id),
            "memory_kind": "semantic",
            "content": "The backend lives in backend/.",
            "summary": "backend location",
            "dedupe_key": "fact:backend-location",
            "dedupe_mode": "key",
            "source_kind": "manual_remember",
            "source_ref": "bridge:test",
        },
        orchestration_run_id=str(orchestration_run.id),
    ))

    assert result["ok"] is True
    payload = result["result"]
    assert payload["memory_kind"] == MemoryRecord.MemoryKind.SEMANTIC
    assert payload["dedupe_key"] == "fact:backend-location"
    assert MemoryRecord.objects.filter(id=payload["memory_id"]).exists()



def test_run_tool_executes_native_schedule_task(orchestration_run):
    result = asyncio.run(run_tool(
        "schedule_task",
        {
            "title": "daily repo backup summary",
            "task_type": "other_task",
            "execution_mode": "headless_run",
            "timezone": "America/New_York",
            "local_time": "05:00",
            "execution_payload": {
                "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                "repo_dir": "C:/Dev/AgentMaestro",
            },
        },
        orchestration_run_id=str(orchestration_run.id),
    ))

    assert result["ok"] is True
    payload = result["result"]
    assert payload["execution_mode"] == ScheduledTask.ExecutionMode.HEADLESS_RUN
    assert ScheduledTask.objects.filter(id=payload["scheduled_task_id"]).exists()



def test_run_tool_executes_native_generic_headless_schedule_task(orchestration_run):
    result = asyncio.run(run_tool(
        "schedule_task",
        {
            "title": "daily repo backup summary",
            "task_type": "other_daily_task",
            "execution_mode": "headless_run",
            "recurrence": {
                "timezone": "America/New_York",
                "frequency": "daily",
                "interval": 1,
                "local_time": "05:00",
            },
            "execution_payload": {
                "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                "repo_dir": "C:/Dev/AgentMaestro",
            },
        },
        orchestration_run_id=str(orchestration_run.id),
    ))

    assert result["ok"] is True
    payload = result["result"]
    assert payload["task_type"] == ScheduledTask.TaskType.OTHER_DAILY_TASK
    assert payload["execution_mode"] == ScheduledTask.ExecutionMode.HEADLESS_RUN
    assert ScheduledTask.objects.filter(
        id=payload["scheduled_task_id"],
        task_type=ScheduledTask.TaskType.OTHER_DAILY_TASK,
    ).exists()


def test_run_tool_executes_native_spawn_subrun(monkeypatch, orchestration_run):
    async def fake_run(self, **kwargs):
        return {"run_id": "", "text": "Focused child summary", "status": "completed", "error": None}

    monkeypatch.setattr("runs.services.headless.LLMRunner.run", fake_run)
    monkeypatch.setattr("runs.services.headless.send_run_transport_message", lambda **kwargs: True)

    result = asyncio.run(run_tool(
        "spawn_subrun",
        {
            "input_text": "Research the current Ocala tennis weather outlook and return a concise summary.",
            "metadata": {"purpose": "focused research"},
        },
        orchestration_run_id=str(orchestration_run.id),
    ))

    assert result["ok"] is True
    payload = result["result"]
    orchestration_run.refresh_from_db()
    assert payload["completed_inline"] is True
    assert payload["resumed_parent"] is True
    assert payload["parent_status"] == AgentRun.Status.RUNNING
    assert AgentRun.objects.filter(id=payload["child_run_id"], parent_run=orchestration_run).exists()


def test_run_tool_rejects_missing_required_arguments_before_http(monkeypatch, orchestration_run):
    class FailIfCalledAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("httpx.AsyncClient should not be called when validation fails")

    monkeypatch.setattr("llm.services.toolrunner_bridge.httpx.AsyncClient", FailIfCalledAsyncClient)

    result = asyncio.run(
        run_tool(
            "file_read",
            {},
            orchestration_run_id=str(orchestration_run.id),
        )
    )

    assert result["ok"] is False
    assert result["error"]
    assert "Tool 'file_read' argument validation failed" in result["error"]
    assert "This tool requires parameters" in result["error"]
    assert result["result"]["error"]["code"] == "tool_runner.MISSING_REQUIRED_ARGUMENTS"
    assert result["result"]["error"]["details"]["missing_parameters"] == ["path"]
