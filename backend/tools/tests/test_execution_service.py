import httpx
import pytest
import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep
from core.services.limits import LimitExceeded
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
    blocker = "blocker"
    try:
        acquire_tool_call_slots(workspace_id, run_id, blocker)
        with pytest.raises(LimitExceeded):
            execute_tool_call(str(tool_call.id))
    finally:
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
