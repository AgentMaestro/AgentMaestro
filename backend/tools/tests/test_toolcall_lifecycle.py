import pytest

from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep
from tools.models import Tool, ToolDefinition, ToolGroup, ToolRisk, ToolCall
from tools.services.approvals import (
    approve_tool_call,
    deny_tool_call,
    request_tool_call_approval,
)


def _build_tool_environment(suffix: str):
    User = get_user_model()
    user = User.objects.create_user(username=f"toolcall{suffix}", password="x")
    workspace = Workspace.objects.create(name=f"ToolCall WS {suffix}")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name=f"ToolCall Agent {suffix}",
        soul="Tool lifecycle tests",
        created_by=user,
    )
    group = ToolGroup.objects.create(name=f"ToolGroup {suffix}")
    tool = Tool.objects.create(
        name=f"tool_{suffix}",
        tool_group=group,
        risk=ToolRisk.SAFE,
        requires_approval=True,
        released=True,
    )
    ToolDefinition.objects.create(workspace=workspace, tool=tool, enabled=True)
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="tool",
    )
    AgentStep.objects.create(run=run, step_index=0, kind=AgentStep.Kind.TOOL_CALL, payload={})
    return run, tool, user


@pytest.mark.django_db(transaction=True)
def test_request_tool_call_requires_approval():
    run, tool, user = _build_tool_environment("approval")
    tool_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"value": 1},
        requires_approval=True,
    )

    run.refresh_from_db()
    assert run.status == AgentRun.Status.WAITING_FOR_APPROVAL
    assert tool_call.status == ToolCall.Status.PENDING_APPROVAL


@pytest.mark.django_db(transaction=True)
def test_approve_transitions_to_queued():
    run, tool, user = _build_tool_environment("queue")
    tool_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"value": 2},
        requires_approval=True,
    )
    approved = approve_tool_call(tool_call_id=str(tool_call.id), user=user)
    run.refresh_from_db()

    assert run.status == AgentRun.Status.RUNNING
    assert approved.status == ToolCall.Status.QUEUED


@pytest.mark.django_db(transaction=True)
def test_deny_tool_call_marks_denied():
    run, tool, user = _build_tool_environment("deny")
    tool_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"value": 3},
        requires_approval=True,
    )
    denied = deny_tool_call(tool_call_id=str(tool_call.id), user=user, reason="not allowed")

    assert denied.status == ToolCall.Status.DENIED
    assert denied.error == "not allowed"
