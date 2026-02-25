import pytest

from asgiref.sync import sync_to_async
from agents.models import Agent
from channels.testing import WebsocketCommunicator
from core.models import Workspace, WorkspaceMembership
from django.contrib.auth import get_user_model
from django.test import Client
from runs.models import AgentRun, AgentStep
from tools.models import ToolCall
from unittest.mock import patch

from agentmaestro.asgi import application


def _session_cookie_for_user(user):
    client = Client()
    client.force_login(user)
    return client.cookies["sessionid"].value


def _create_workspace(name):
    return Workspace.objects.create(name=name)


def _create_workspace_member(name, username, role):
    workspace = Workspace.objects.create(name=name)
    user = get_user_model().objects.create_user(username=username, password="x")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=role)
    return workspace, user


def _create_run_with_membership(name, username, role):
    workspace = Workspace.objects.create(name=name)
    user = get_user_model().objects.create_user(username=username, password="x")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=role)
    agent = Agent.objects.create(workspace=workspace, name=f"RunAgent-{name}", system_prompt="x", created_by=user)
    run = AgentRun.objects.create(workspace=workspace, agent=agent, status=AgentRun.Status.PENDING, input_text="run", started_by=user)
    return workspace, user, run


def _create_run_without_membership(name, username):
    workspace = Workspace.objects.create(name=name)
    user = get_user_model().objects.create_user(username=username, password="x")
    agent = Agent.objects.create(workspace=workspace, name=f"RunAgent-{name}", system_prompt="x", created_by=user)
    run = AgentRun.objects.create(workspace=workspace, agent=agent, status=AgentRun.Status.PENDING, input_text="run", started_by=user)
    return workspace, user, run


async def _await_event(communicator: WebsocketCommunicator, event_name: str):
    while True:
        msg = await communicator.receive_json_from()
        if msg.get("event") == event_name:
            return msg


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_workspace_consumer_denies_unauthenticated():
    workspace = await sync_to_async(_create_workspace)("WSAuth")
    communicator = WebsocketCommunicator(application, f"/ws/ui/workspace/?workspace_id={workspace.id}")
    connected, _ = await communicator.connect()
    assert not connected
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_workspace_consumer_allows_member():
    workspace, user = await sync_to_async(_create_workspace_member)(
        "WSMember", "wsuser", WorkspaceMembership.Role.OPERATOR
    )

    sessionid = await sync_to_async(_session_cookie_for_user)(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ui/workspace/?workspace_id={workspace.id}",
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_run_consumer_denies_nonmember():
    _, user, run = await sync_to_async(_create_run_without_membership)("RunWS", "runuser")

    sessionid = await sync_to_async(_session_cookie_for_user)(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ui/run/{run.id}/",
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )
    connected, _ = await communicator.connect()
    assert not connected
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_run_consumer_denies_viewer_approvals():
    _, user, run = await sync_to_async(_create_run_with_membership)(
        "RunApproveWS", "viewer", WorkspaceMembership.Role.VIEWER
    )
    step = await sync_to_async(AgentStep.objects.create)(
        run=run, step_index=1, kind=AgentStep.Kind.TOOL_CALL, payload={}
    )
    tool_call = await sync_to_async(ToolCall.objects.create)(
        run=run,
        step=step,
        tool_name="demo",
        requires_approval=True,
        status=ToolCall.Status.PENDING,
    )

    sessionid = await sync_to_async(_session_cookie_for_user)(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ui/run/{run.id}/",
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()
    await communicator.send_json_to({"type": "cmd", "cmd": "approve_tool_call", "tool_call_id": str(tool_call.id)})
    response = await communicator.receive_json_from()
    assert response["event"] == "error"
    assert "Insufficient role" in response["data"]["message"]
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_run_consumer_controls_workflow():
    _, user, run = await sync_to_async(_create_run_with_membership)(
        "RunControlWS", "controller", WorkspaceMembership.Role.ADMIN
    )
    run.status = AgentRun.Status.RUNNING
    await sync_to_async(run.save)(update_fields=["status", "updated_at"])

    sessionid = await sync_to_async(_session_cookie_for_user)(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ui/run/{run.id}/",
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()

    with patch("runs.tasks.run_tick.delay") as mock_delay:
        await communicator.send_json_to({"type": "cmd", "cmd": "pause_run"})
        pause_ack = await _await_event(communicator, "pause_run_ack")
        assert pause_ack["event"] == "pause_run_ack"
        await sync_to_async(run.refresh_from_db)()
        assert run.status == AgentRun.Status.PAUSED

        await communicator.send_json_to({"type": "cmd", "cmd": "resume_run"})
        resume_ack = await _await_event(communicator, "resume_run_ack")
        assert resume_ack["event"] == "resume_run_ack"
        await sync_to_async(run.refresh_from_db)()
        assert run.status == AgentRun.Status.RUNNING
        mock_delay.assert_called_once_with(str(run.id))

        await communicator.send_json_to({"type": "cmd", "cmd": "cancel_run"})
        cancel_ack = await _await_event(communicator, "cancel_run_ack")
        assert cancel_ack["event"] == "cancel_run_ack"
        await sync_to_async(run.refresh_from_db)()
        assert run.status == AgentRun.Status.CANCELED

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_run_consumer_controls_require_role():
    _, user, run = await sync_to_async(_create_run_with_membership)(
        "RunControlDenied", "viewer", WorkspaceMembership.Role.VIEWER
    )
    run.status = AgentRun.Status.RUNNING
    await sync_to_async(run.save)(update_fields=["status", "updated_at"])

    sessionid = await sync_to_async(_session_cookie_for_user)(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ui/run/{run.id}/",
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()

    await communicator.send_json_to({"type": "cmd", "cmd": "pause_run"})
    error = await communicator.receive_json_from()
    assert error["event"] == "error"
    assert "run control" in error["data"]["message"].lower()
    await sync_to_async(run.refresh_from_db)()
    assert run.status == AgentRun.Status.RUNNING

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_run_consumer_spawns_subrun():
    _, user, run = await sync_to_async(_create_run_with_membership)(
        "RunSpawnWS", "controller", WorkspaceMembership.Role.ADMIN
    )
    run.status = AgentRun.Status.RUNNING
    await sync_to_async(run.save)(update_fields=["status", "updated_at"])

    sessionid = await sync_to_async(_session_cookie_for_user)(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ui/run/{run.id}/",
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()

    with patch("runs.tasks.run_tick.delay") as mock_delay:
        await communicator.send_json_to(
            {"type": "cmd", "cmd": "spawn_subrun", "input_text": "child prompt"}
        )
        ack = await communicator.receive_json_from()
        assert ack["event"] == "spawn_subrun_ack"
        await sync_to_async(run.refresh_from_db)()
        assert run.status == AgentRun.Status.WAITING_FOR_SUBRUN
        mock_delay.assert_called_once()

    await communicator.disconnect()
