# backend/tools/tests/test_approvals_service.py
import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator

from agentmaestro.asgi import application
from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from django.contrib.auth import get_user_model
from runs.models import AgentRun, RunEvent
from tools.models import ToolCall
from tools.services.approvals import (
    approve_tool_call,
    request_tool_call_approval,
)


def _setup_run(user_suffix: str):
    User = get_user_model()
    user = User.objects.create_user(username=f"approval{user_suffix}", password="x")
    workspace = Workspace.objects.create(name=f"Approval WS {user_suffix}")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)

    agent = Agent.objects.create(
        workspace=workspace,
        name=f"Approval Agent {user_suffix}",
        system_prompt="Approval test agent.",
        created_by=user,
    )

    return AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="approval",
    ), user


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_request_tool_call_broadcasts_approval_event():
    run, _ = await sync_to_async(_setup_run, thread_sensitive=True)("request")
    communicator = WebsocketCommunicator(application, f"/ws/ui/workspace/?workspace_id={run.workspace_id}")
    connected, _ = await communicator.connect()
    assert connected

    # consume connected push
    await communicator.receive_json_from()

    await communicator.send_json_to({"type": "cmd", "cmd": "subscribe_approvals"})
    await communicator.receive_json_from()

    await sync_to_async(
        request_tool_call_approval,
        thread_sensitive=True,
    )(
        run_id=str(run.id),
        tool_name="search",
        args={"query": "test"},
        requires_approval=True,
    )

    msg = await communicator.receive_json_from()
    assert msg["topic"] == "approvals.event"
    assert msg["event"] == "tool_call_requested"
    assert msg["data"]["run_id"] == str(run.id)
    assert msg["data"]["status"] == ToolCall.Status.PENDING

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_approve_tool_call_transitions_run_and_notifies():
    run, user = await sync_to_async(_setup_run, thread_sensitive=True)("approve")
    tool_call = await sync_to_async(
        request_tool_call_approval,
        thread_sensitive=True,
    )(
        run_id=str(run.id),
        tool_name="search",
        args={"query": "approve"},
        requires_approval=True,
    )

    await sync_to_async(run.refresh_from_db, thread_sensitive=True)()
    assert run.status == AgentRun.Status.WAITING_FOR_APPROVAL
    assert tool_call.status == ToolCall.Status.PENDING

    communicator = WebsocketCommunicator(application, f"/ws/ui/workspace/?workspace_id={run.workspace_id}")
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()
    await communicator.send_json_to({"type": "cmd", "cmd": "subscribe_approvals"})
    await communicator.receive_json_from()

    approved = await sync_to_async(
        approve_tool_call,
        thread_sensitive=True,
    )(tool_call_id=str(tool_call.id), user=user)

    await sync_to_async(run.refresh_from_db, thread_sensitive=True)()
    assert run.status == AgentRun.Status.RUNNING
    assert approved.status == ToolCall.Status.APPROVED

    events = await sync_to_async(
        lambda: list(
            RunEvent.objects.filter(run=run).order_by("seq").values_list("event_type", flat=True)
        ),
        thread_sensitive=True,
    )()
    assert "tool_call_requested" in events
    assert "tool_call_approved" in events

    msg = await communicator.receive_json_from()
    assert msg["event"] == "tool_call_approved"
    assert msg["data"]["status"] == ToolCall.Status.APPROVED

    await communicator.disconnect()
