import httpx
import pytest
import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep, RunEvent
from core.services.limits import LimitExceeded
from tools.models import ToolCall, ToolDefinition
from tools.services.execution import execute_tool_call, TOOL_CALL_COMPLETED_EVENT
from tools.services.quotas import release_tool_call_slots, acquire_tool_call_slots

pytestmark = pytest.mark.django_db(transaction=True)


def _build_test_run(suffix: str):
    User = get_user_model()
    user = User.objects.create_user(username=f"exec{suffix}", password="x")
    workspace = Workspace.objects.create(name=f"Exec WS {suffix}")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        name=f"Exec Agent {suffix}",
        system_prompt="Execute tests",
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
        status=ToolCall.Status.APPROVED,
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


@override_settings(
    AGENTMAESTRO_TOOLRUNNER_URL="http://example/v1/execute",
    AGENTMAESTRO_TOOLRUNNER_SECRET="test-secret",
    AGENTMAESTRO_TOOLRUNNER_TIMEOUT=5,
    AGENTMAESTRO_TOOLRUNNER_OUTPUT_LIMIT=128,
    AGENTMAESTRO_TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_tool_call_success(monkeypatch):
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
    assert tool_call.status == ToolCall.Status.SUCCEEDED
    assert tool_call.stdout == "done"
    assert tool_call.result == {"foo": "bar"}

    assert RunEvent.objects.filter(run=tool_call.run, event_type=TOOL_CALL_COMPLETED_EVENT).exists()


@override_settings(
    AGENTMAESTRO_TOOLRUNNER_URL="http://example/v1/execute",
    AGENTMAESTRO_TOOLRUNNER_SECRET="test-secret",
    AGENTMAESTRO_TOOLRUNNER_TIMEOUT=5,
    AGENTMAESTRO_TOOLRUNNER_OUTPUT_LIMIT=128,
    AGENTMAESTRO_TOOLRUNNER_HTTP_TIMEOUT=10,
)
def test_execute_tool_call_failure(monkeypatch):
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


@override_settings(
    AGENTMAESTRO_TOOLRUNNER_URL="http://example/v1/execute",
    AGENTMAESTRO_TOOLRUNNER_SECRET="test-secret",
    AGENTMAESTRO_TOOLRUNNER_TIMEOUT=5,
    AGENTMAESTRO_TOOLRUNNER_OUTPUT_LIMIT=128,
    AGENTMAESTRO_TOOLRUNNER_HTTP_TIMEOUT=10,
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
