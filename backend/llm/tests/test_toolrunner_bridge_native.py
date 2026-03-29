import asyncio
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
import pytest
import httpx
from django.contrib.auth import get_user_model
from django.test import override_settings

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from google_bridge.services.schema import build_google_bridge_args_schema
from llm.services.toolrunner_bridge import run_tool
from memory.models import MemoryRecord, ScheduledTask
from memory.scheduled_tasks import create_scheduled_task
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


@override_settings(TIME_ZONE="Europe/London")
def test_run_tool_executes_native_schedule_task_without_timezone_defaults_to_local(orchestration_run):
    result = asyncio.run(
        run_tool(
            "schedule_task",
            {
                "title": "daily repo backup summary",
                "task_type": "other_task",
                "execution_mode": "headless_run",
                "local_time": "05:00",
                "execution_payload": {
                    "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                    "repo_dir": "C:/Dev/AgentMaestro",
                },
            },
            orchestration_run_id=str(orchestration_run.id),
        )
    )

    assert result["ok"] is True
    payload = result["result"]
    assert payload["timezone"] == "Europe/London"
    assert ScheduledTask.objects.filter(
        id=payload["scheduled_task_id"],
        timezone="Europe/London",
    ).exists()


def test_run_tool_executes_native_current_datetime(orchestration_run, monkeypatch):
    ToolDefinition.objects.create(
        workspace=orchestration_run.workspace,
        name="get_current_datetime",
        enabled=True,
        args_schema={"type": "object", "additionalProperties": False, "properties": {}},
    )

    fixed_now = datetime(2026, 3, 21, 19, 32, 5, tzinfo=dt_timezone.utc)
    monkeypatch.setattr("core.services.timezones.timezone.now", lambda: fixed_now)

    result = asyncio.run(
        run_tool(
            "get_current_datetime",
            {},
            orchestration_run_id=str(orchestration_run.id),
        )
    )

    assert result["ok"] is True
    payload = result["result"]
    assert payload["timezone"] == "America/New_York"
    assert payload["datetime"] == "2026-03-21T15:32:05-04:00"



def test_run_tool_executes_native_generic_headless_schedule_task(orchestration_run):
    result = asyncio.run(run_tool(
        "schedule_task",
        {
            "title": "daily repo backup summary",
            "task_type": "other_task",
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
    assert payload["task_type"] == ScheduledTask.TaskType.OTHER_TASK
    assert payload["execution_mode"] == ScheduledTask.ExecutionMode.HEADLESS_RUN
    assert ScheduledTask.objects.filter(
        id=payload["scheduled_task_id"],
        task_type=ScheduledTask.TaskType.OTHER_TASK,
    ).exists()


def test_run_tool_executes_native_scheduled_task_management_tools(orchestration_run):
    scheduled_task = create_scheduled_task(
        agent=orchestration_run.agent,
        owner=orchestration_run.started_by or orchestration_run.agent.owner,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="05:00",
        timezone_name="America/New_York",
        title="daily repo backup summary",
        execution_payload={
            "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
            "repo_dir": "C:/Dev/AgentMaestro",
        },
    )

    for tool_name, payload, expected_enabled in [
        ("edit_scheduled_task", {"scheduled_task_id": str(scheduled_task.id), "title": "updated task", "enabled": False}, False),
        ("disable_scheduled_task", {"scheduled_task_id": str(scheduled_task.id)}, False),
        ("enable_scheduled_task", {"scheduled_task_id": str(scheduled_task.id)}, True),
    ]:
        ToolDefinition.objects.create(
            workspace=orchestration_run.workspace,
            name=tool_name,
            enabled=True,
        )
        result = asyncio.run(
            run_tool(
                tool_name,
                payload,
                orchestration_run_id=str(orchestration_run.id),
            )
        )

        assert result["ok"] is True
        assert result["result"]["scheduled_task_id"] == str(scheduled_task.id)
        assert result["result"]["enabled"] is expected_enabled
        scheduled_task.refresh_from_db()
        assert scheduled_task.enabled is expected_enabled

    ToolDefinition.objects.create(
        workspace=orchestration_run.workspace,
        name="get_scheduled_task",
        enabled=True,
    )
    get_result = asyncio.run(
        run_tool(
            "get_scheduled_task",
            {"scheduled_task_id": str(scheduled_task.id)},
            orchestration_run_id=str(orchestration_run.id),
        )
    )
    assert get_result["ok"] is True
    assert get_result["result"]["scheduled_task_id"] == str(scheduled_task.id)
    assert get_result["result"]["execution_payload"]["objective"].startswith("Create a backup commit")

    ToolDefinition.objects.create(
        workspace=orchestration_run.workspace,
        name="list_scheduled_tasks",
        enabled=True,
        args_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "include_execution_payload": {"type": "boolean", "default": False},
            },
        },
    )
    list_result = asyncio.run(
        run_tool(
            "list_scheduled_tasks",
            {"enabled_only": True, "limit": 5, "include_execution_payload": True},
            orchestration_run_id=str(orchestration_run.id),
        )
    )
    assert list_result["ok"] is True
    assert list_result["result"]["results"][0]["scheduled_task_id"] == str(scheduled_task.id)
    assert "execution_payload" in list_result["result"]["results"][0]


def test_run_tool_executes_native_google_bridge_without_http(monkeypatch, orchestration_run):
    ToolDefinition.objects.create(
        workspace=orchestration_run.workspace,
        name="google_bridge",
        enabled=True,
        args_schema=build_google_bridge_args_schema(),
    )

    def fake_execute_google_task(*, payload, workspace=None, owner=None, account=None):
        assert payload["integration_kind"] == "google"
        assert payload["resource_kind"] == "gmail"
        assert payload["operation"] == "list"
        assert workspace == orchestration_run.workspace
        return {
            "ok": True,
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "summary_text": "Found 5 Gmail messages.",
            "result": {"messages": []},
            "steps": [],
            "accounts": [],
        }

    monkeypatch.setattr("google_bridge.services.bridge.execute_google_task", fake_execute_google_task)

    result = asyncio.run(
        run_tool(
            "google_bridge",
            {
                "integration_kind": "google",
                "resource_kind": "gmail",
                "action_kind": "read",
                "operation": "list",
                "account_scope": "primary",
                "email": "dev.agent.maestro@gmail.com",
                "query": "in:inbox newer_than:1d",
                "max_results": 5,
            },
            orchestration_run_id=str(orchestration_run.id),
        )
    )

    assert result["ok"] is True
    payload = result["result"]
    assert payload["resource_kind"] == "gmail"
    assert payload["summary_text"] == "Found 5 Gmail messages."


def test_run_tool_queues_native_google_bridge_cleanup_subrun(monkeypatch, orchestration_run):
    ToolDefinition.objects.create(
        workspace=orchestration_run.workspace,
        name="google_bridge",
        enabled=True,
        args_schema=build_google_bridge_args_schema(),
    )

    def fail_execute_google_task(*args, **kwargs):
        raise AssertionError("cleanup should queue a background subrun instead of executing inline")

    spawned_calls = {}

    def fake_spawn_subrun(**kwargs):
        spawned_calls.update(kwargs)
        return SimpleNamespace(id="child-run-123", execution_mode=AgentRun.ExecutionMode.HEADLESS)

    monkeypatch.setattr("google_bridge.services.bridge.execute_google_task", fail_execute_google_task)
    monkeypatch.setattr("runs.services.subruns.spawn_subrun", fake_spawn_subrun)

    result = asyncio.run(
        run_tool(
            "google_bridge",
            {
                "integration_kind": "google",
                "resource_kind": "gmail",
                "action_kind": "delete",
                "operation": "delete",
                "account_scope": "all",
                "query": "from:(kayak.com OR ally.com)",
                "max_results": 50,
            },
            orchestration_run_id=str(orchestration_run.id),
        )
    )

    assert result["ok"] is True
    payload = result["result"]
    assert payload["queued"] is True
    assert payload["background"] is True
    assert payload["child_run_id"] == "child-run-123"
    assert spawned_calls["parent_run_id"] == str(orchestration_run.id)
    assert spawned_calls["block_parent"] is False
    assert spawned_calls["child_execution_mode"] == AgentRun.ExecutionMode.HEADLESS


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


def test_run_tool_executes_native_send_telegram(monkeypatch, orchestration_run):
    ToolDefinition.objects.create(
        workspace=orchestration_run.workspace,
        name="send_telegram",
        enabled=True,
        args_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["target", "text"],
            "properties": {
                "target": {"type": "string", "enum": ["paired"]},
                "name": {"type": "string"},
                "text": {"type": "string"},
                "parse_mode": {"type": "string"},
                "disable_web_page_preview": {"type": "boolean"},
            },
        },
    )

    captured: dict[str, object] = {}

    def fake_send(*, run_id: str, text: str, **kwargs):
        captured["run_id"] = run_id
        captured["text"] = text
        captured["kwargs"] = kwargs
        return {
            "target": "paired",
            "delivered": True,
            "conversation_id": "conversation-1",
            "chat_id": "chat-1",
            "telegram_message_id": "message-1",
            "comms_message_id": "comms-1",
            "control_message_id": None,
            "response": {"message_id": "message-1"},
        }

    monkeypatch.setattr("llm.services.toolrunner_bridge.send_paired_telegram_message", fake_send)

    result = asyncio.run(
        run_tool(
            "send_telegram",
            {
                "target": "paired",
                "name": "system - task complete",
                "text": "Hello from the agent.",
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            orchestration_run_id=str(orchestration_run.id),
        )
    )

    assert result["ok"] is True
    payload = result["result"]
    assert payload["delivered"] is True
    assert payload["chat_id"] == "chat-1"
    assert captured["run_id"] == str(orchestration_run.id)
    assert captured["kwargs"]["name"] == "system - task complete"
    assert captured["text"] == "Hello from the agent."
    assert captured["kwargs"]["parse_mode"] == "HTML"
    assert captured["kwargs"]["disable_web_page_preview"] is True


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
