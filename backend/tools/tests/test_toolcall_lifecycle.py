import pytest

from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep
from tools.models import Tool, ToolDefinition, ToolGroup, ToolRisk, ToolCall
from tools.services.approval_grants import GRANT_MODE_PATH_PREFIX
from tools.services.approvals import (
    approve_tool_call,
    clear_tool_approval_grants,
    deny_tool_call,
    request_tool_call_approval,
    revoke_tool_approval_grant,
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


@pytest.mark.django_db(transaction=True)
def test_approve_with_run_grant_auto_approves_future_matching_calls():
    run, tool, user = _build_tool_environment("grant-auto")
    tool.name = "file_write"
    tool.save(update_fields=["name", "slug", "updated_at"])
    ToolDefinition.objects.filter(workspace=run.workspace, tool=tool).update(name=tool.name)

    first_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"path": "notes/first.txt", "content": "one"},
        requires_approval=True,
    )
    approved = approve_tool_call(
        tool_call_id=str(first_call.id),
        user=user,
        grant_mode=GRANT_MODE_PATH_PREFIX,
    )

    assert approved.status == ToolCall.Status.QUEUED
    assert approved.approval_grant is not None
    assert approved.approval_metadata["mode"] == "grant_create"

    auto_approved = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"path": "notes/second.txt", "content": "two"},
        requires_approval=True,
    )

    assert auto_approved.status == ToolCall.Status.QUEUED
    assert auto_approved.approval_grant_id == approved.approval_grant_id
    assert auto_approved.approval_metadata["mode"] == "grant_match"


@pytest.mark.django_db(transaction=True)
def test_revoke_and_clear_run_grants_stop_auto_approval():
    run, tool, user = _build_tool_environment("grant-reset")
    tool.name = "file_write"
    tool.save(update_fields=["name", "slug", "updated_at"])
    ToolDefinition.objects.filter(workspace=run.workspace, tool=tool).update(name=tool.name)

    first_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"path": "notes/first.txt", "content": "one"},
        requires_approval=True,
    )
    approved = approve_tool_call(
        tool_call_id=str(first_call.id),
        user=user,
        grant_mode=GRANT_MODE_PATH_PREFIX,
    )
    grant = approved.approval_grant

    revoke_tool_approval_grant(grant_id=str(grant.id), user=user)
    pending_again = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"path": "notes/second.txt", "content": "two"},
        requires_approval=True,
    )
    assert pending_again.status == ToolCall.Status.PENDING_APPROVAL

    second_approved = approve_tool_call(
        tool_call_id=str(pending_again.id),
        user=user,
        grant_mode=GRANT_MODE_PATH_PREFIX,
    )
    assert second_approved.approval_grant is not None

    cleared = clear_tool_approval_grants(run_id=str(run.id), user=user)
    assert cleared >= 1

    final_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=tool.name,
        args={"path": "notes/third.txt", "content": "three"},
        requires_approval=True,
    )
    assert final_call.status == ToolCall.Status.PENDING_APPROVAL
