import httpx
import pytest
import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep
from core.services.limits import LimitExceeded
from memory.scheduled_tasks import create_scheduled_task
from tools.models import ToolCall, ToolDefinition
from tools.services.execution import ToolrunnerError, execute_tool_call
from tools.services.quotas import release_tool_call_slots, acquire_tool_call_slots

pytestmark = pytest.mark.django_db(transaction=True)


class DummyChannelLayer:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def group_send(self, group: str, message: dict) -> None:
        self.sent.append((group, message))


@pytest.fixture
def fake_result_bus(monkeypatch):
    calls: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "tools.services.execution.store_tool_result",
        lambda run_id, tool_call_id, payload, ttl_seconds=3600: calls.append(
            (run_id, tool_call_id, payload)
        ),
    )
    layer = DummyChannelLayer()
    monkeypatch.setattr("tools.services.execution.get_channel_layer", lambda: layer)
    return calls, layer


def _build_test_run(suffix: str):
    User = get_user_model()
    user = User.objects.create_user(username=f"exec{suffix}", password="x")
    workspace = Workspace.objects.create(name=f"Exec WS {suffix}")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        name=f"Exec Agent {suffix}",
        soul="Execute tests",
        created_by=user,
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="run",
    )
    step = AgentStep.objects.create(run=run, step_index=0, kind=AgentStep.Kind.TOOL_CALL, payload={})
    ToolDefinition.objects.create(workspace=workspace, name="shell_exec", enabled=True)
    tool_call = ToolCall.objects.create(
        run=run,
        step=step,
        tool_name="shell_exec",
        args={"cmd": ["ls"], "cwd": "."},
        requires_approval=False,
        status=ToolCall.Status.QUEUED,
        correlation_id=step.correlation_id,
    )
    return tool_call


def _build_validation_test_run(suffix: str):
    User = get_user_model()
    user = User.objects.create_user(username=f"execv{suffix}", password="x")
    workspace = Workspace.objects.create(name=f"Exec Validation WS {suffix}")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        name=f"Exec Validation Agent {suffix}",
        soul="Execute validation tests",
        created_by=user,
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="run",
    )
    step = AgentStep.objects.create(run=run, step_index=0, kind=AgentStep.Kind.TOOL_CALL, payload={})
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
    tool_call = ToolCall.objects.create(
        run=run,
        step=step,
        tool_name="file_read",
        args={},
        requires_approval=False,
        status=ToolCall.Status.QUEUED,
        correlation_id=step.correlation_id,
    )
    return tool_call


class DummyClient:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        return self.result


class TimeoutCapturingClient(DummyClient):
    last_timeout = None

    def __init__(self, result, timeout=None):
        super().__init__(result)
        TimeoutCapturingClient.last_timeout = timeout


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_tool_call_success(monkeypatch, fake_result_bus):
    tool_call = _build_test_run("success")
    response = httpx.Response(
        200,
        json={
            "request_id": str(uuid.uuid4()),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "done",
            "stderr": "",
            "result": {"foo": "bar"},
            "duration_ms": 10,
        },
    )
    monkeypatch.setattr("tools.services.execution.httpx.Client", lambda *args, **kwargs: DummyClient(response))

    execute_tool_call(str(tool_call.id))
    tool_call.refresh_from_db()
    assert tool_call.status == ToolCall.Status.COMPLETED
    assert tool_call.stdout == "done"
    assert tool_call.result == {"foo": "bar"}

    calls, layer = fake_result_bus
    assert len(calls) == 1
    stored_run_id, stored_tool_call_id, payload = calls[0]
    assert stored_run_id == str(tool_call.run_id)
    assert stored_tool_call_id == str(tool_call.id)
    assert payload["status"] == ToolCall.Status.COMPLETED
    assert payload["tool_call_id"] == str(tool_call.id)
    assert layer.sent
    assert layer.sent[0][1]["payload"]["event"] == "tool_result_ready"


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_tool_call_sanitizes_nul_bytes_before_save(monkeypatch, fake_result_bus):
    tool_call = _build_test_run("nul-bytes")
    response = httpx.Response(
        200,
        json={
            "request_id": str(uuid.uuid4()),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "done\x00now",
            "stderr": "warn\x00later",
            "result": {"digest": "abc\x00def", "nested": ["x\x00y"]},
            "duration_ms": 10,
        },
    )
    monkeypatch.setattr("tools.services.execution.httpx.Client", lambda *args, **kwargs: DummyClient(response))

    execute_tool_call(str(tool_call.id))
    tool_call.refresh_from_db()

    assert tool_call.status == ToolCall.Status.COMPLETED
    assert tool_call.stdout == "done\\u0000now"
    assert tool_call.stderr == "warn\\u0000later"
    assert tool_call.result == {"digest": "abc\\u0000def", "nested": ["x\\u0000y"]}

    calls, _layer = fake_result_bus
    payload = calls[0][2]
    assert payload["stdout"] == "done\\u0000now"
    assert payload["stderr"] == "warn\\u0000later"
    assert payload["result"] == {"digest": "abc\\u0000def", "nested": ["x\\u0000y"]}


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_tool_call_rejects_missing_required_arguments(monkeypatch, fake_result_bus):
    tool_call = _build_validation_test_run("missing-path")

    class FailIfCalledClient(DummyClient):
        def post(self, *args, **kwargs):
            raise AssertionError("httpx.Client should not be used when validation fails")

    monkeypatch.setattr("tools.services.execution.httpx.Client", lambda *args, **kwargs: FailIfCalledClient(None))

    execute_tool_call(str(tool_call.id))
    tool_call.refresh_from_db()

    assert tool_call.status == ToolCall.Status.FAILED
    assert "Tool 'file_read' argument validation failed" in tool_call.stderr
    assert tool_call.result["error"]["code"] == "tool_runner.MISSING_REQUIRED_ARGUMENTS"
    assert tool_call.result["error"]["details"]["missing_parameters"] == ["path"]

    calls, layer = fake_result_bus
    assert len(calls) == 1
    payload = calls[0][2]
    assert payload["status"] == ToolCall.Status.FAILED
    assert payload["stderr"].startswith("Tool 'file_read' argument validation failed")
    assert layer.sent[0][1]["payload"]["event"] == "tool_result_ready"


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_tool_call_failure(monkeypatch, fake_result_bus):
    tool_call = _build_test_run("failure")
    response = httpx.Response(500)
    class FailureClient(DummyClient):
        def post(self, *args, **kwargs):
            raise httpx.HTTPStatusError("fail", request=None, response=response)

    monkeypatch.setattr("tools.services.execution.httpx.Client", lambda *args, **kwargs: FailureClient(response))

    execute_tool_call(str(tool_call.id))
    tool_call.refresh_from_db()
    assert tool_call.status == ToolCall.Status.FAILED
    assert "toolrunner error" in tool_call.stderr
    calls, layer = fake_result_bus
    assert len(calls) == 1
    payload = calls[0][2]
    assert payload["status"] == ToolCall.Status.FAILED
    assert layer.sent[0][1]["payload"]["event"] == "tool_result_ready"


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_tool_call_respects_quota(monkeypatch):
    tool_call = _build_test_run("quota")
    workspace_id = str(tool_call.run.workspace_id)
    run_id = str(tool_call.run_id)
    blockers = [f"blocker-{index}" for index in range(6)]
    try:
        for blocker in blockers:
            acquire_tool_call_slots(workspace_id, run_id, blocker)
        with pytest.raises(LimitExceeded):
            execute_tool_call(str(tool_call.id))
    finally:
        for blocker in blockers:
            release_tool_call_slots(workspace_id, run_id, blocker)


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
    TOOLRUNNER_HTTP_TIMEOUT_BUFFER=30,
)
def test_execute_tool_call_uses_tool_timeout_for_http_client(monkeypatch, fake_result_bus):
    tool_call = _build_test_run("timeout")
    tool_call.tool_name = "test_runner"
    tool_call.args = {
        "kind": "powershell_script",
        "script_path": "backend/scripts/runtests.ps1",
        "cwd": ".",
        "timeout_ms": 120000,
    }
    tool_call.save(update_fields=["tool_name", "args", "updated_at"])
    ToolDefinition.objects.create(workspace=tool_call.run.workspace, name="test_runner", enabled=True)
    response = httpx.Response(
        200,
        json={
            "request_id": str(uuid.uuid4()),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "done",
            "stderr": "",
            "result": {"foo": "bar"},
            "duration_ms": 10,
        },
    )

    def client_factory(*args, **kwargs):
        return TimeoutCapturingClient(response, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("tools.services.execution.httpx.Client", client_factory)

    execute_tool_call(str(tool_call.id))

    assert TimeoutCapturingClient.last_timeout == 150.0


def test_execute_tool_call_enforces_agent_sandbox(monkeypatch, tmp_path):
    tool_call = _build_test_run("sandbox")
    agent = tool_call.run.agent
    allowed_dir = tmp_path / "allowed"
    blocked_dir = tmp_path / "blocked"
    allowed_dir.mkdir()
    blocked_dir.mkdir()
    agent.sandbox_paths = [str(allowed_dir)]
    agent.save()
    tool_call.args["cwd"] = str(blocked_dir)
    with pytest.raises(ToolrunnerError):
        execute_tool_call(str(tool_call.id))


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_native_memory_tool_call_skips_toolrunner_http(monkeypatch, fake_result_bus):
    tool_call = _build_test_run("native-memory")
    tool_call.tool_name = "remember"
    tool_call.args = {
        "scope_type": "sandbox",
        "scope_id": "C:/Dev/AgentMaestro",
        "memory_kind": "semantic",
        "content": "The backend lives in backend/.",
        "summary": "backend location",
        "dedupe_key": "fact:backend-location",
        "dedupe_mode": "key",
        "source_kind": "manual_remember",
        "source_ref": "operator:console",
        "pinned": True,
        "expires_at": "2026-03-31T23:59:59Z",
    }
    tool_call.save(update_fields=["tool_name", "args", "updated_at"])
    ToolDefinition.objects.create(workspace=tool_call.run.workspace, name="remember", enabled=True)

    def _fail_client(*args, **kwargs):
        raise AssertionError("http client should not be used for native memory tools")

    monkeypatch.setattr("tools.services.execution.httpx.Client", _fail_client)

    execute_tool_call(str(tool_call.id))

    tool_call.refresh_from_db()
    assert tool_call.status == ToolCall.Status.COMPLETED
    assert tool_call.result["scope_type"] == "sandbox"
    assert tool_call.result["dedupe_key"] == "fact:backend-location"
    assert tool_call.result["source_kind"] == "manual_remember"
    assert tool_call.result["source_ref"] == "operator:console"
    assert tool_call.result["pinned"] is True
    assert tool_call.result["expires_at"].startswith("2026-03-31T23:59:59")
    assert tool_call.result["access_count"] == 1
    assert tool_call.result["last_accessed_at"]
    calls, _layer = fake_result_bus
    assert calls[0][2]["status"] == ToolCall.Status.COMPLETED


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_native_schedule_task_skips_toolrunner_http(monkeypatch, fake_result_bus):
    tool_call = _build_test_run("native-schedule")
    tool_call.tool_name = "schedule_task"
    tool_call.args = {
        "title": "daily repo backup summary",
        "task_type": "other_task",
        "execution_mode": "headless_run",
        "timezone": "America/New_York",
        "local_time": "05:00",
        "execution_payload": {
            "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
            "repo_dir": "C:/Dev/AgentMaestro",
        },
    }
    tool_call.save(update_fields=["tool_name", "args", "updated_at"])
    ToolDefinition.objects.create(workspace=tool_call.run.workspace, name="schedule_task", enabled=True)

    def _fail_client(*args, **kwargs):
        raise AssertionError("http client should not be used for native scheduling tools")

    monkeypatch.setattr("tools.services.execution.httpx.Client", _fail_client)

    execute_tool_call(str(tool_call.id))

    tool_call.refresh_from_db()
    assert tool_call.status == ToolCall.Status.COMPLETED
    assert tool_call.result["task_type"] == "other_task"
    assert tool_call.result["execution_mode"] == "headless_run"
    assert tool_call.result["scheduled_task_id"]


@override_settings(
    TOOLRUNNER_URL="http://example/v1/execute",
    TOOLRUNNER_SECRET="test-secret",
    TOOLRUNNER_TIMEOUT=5,
    TOOLRUNNER_OUTPUT_LIMIT=128,
    TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_native_scheduled_task_management_tools_skip_toolrunner_http(monkeypatch, fake_result_bus):
    def _fail_client(*args, **kwargs):
        raise AssertionError("http client should not be used for native scheduling tools")

    monkeypatch.setattr("tools.services.execution.httpx.Client", _fail_client)

    for tool_name, tool_args, expected_enabled in [
        ("edit_scheduled_task", {"title": "updated task", "enabled": False}, False),
        ("disable_scheduled_task", {}, False),
        ("enable_scheduled_task", {}, True),
    ]:
        tool_call = _build_test_run(f"native-{tool_name}")
        scheduled_task = create_scheduled_task(
            agent=tool_call.run.agent,
            owner=tool_call.run.started_by or tool_call.run.agent.owner,
            task_type="other_task",
            local_time_value="05:00",
            timezone_name="America/New_York",
            title="daily repo backup summary",
            execution_payload={
                "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                "repo_dir": "C:/Dev/AgentMaestro",
            },
        )
        tool_call.tool_name = tool_name
        tool_call.args = {"scheduled_task_id": str(scheduled_task.id), **tool_args}
        tool_call.save(update_fields=["tool_name", "args", "updated_at"])
        ToolDefinition.objects.create(workspace=tool_call.run.workspace, name=tool_name, enabled=True)

        execute_tool_call(str(tool_call.id))

        tool_call.refresh_from_db()
        scheduled_task.refresh_from_db()
        assert tool_call.status == ToolCall.Status.COMPLETED
        assert tool_call.result["scheduled_task_id"] == str(scheduled_task.id)
        assert tool_call.result["enabled"] is expected_enabled
        assert scheduled_task.enabled is expected_enabled
