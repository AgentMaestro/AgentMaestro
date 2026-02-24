# backend/runs/tests/test_append_event_db_and_ws.py
import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model

from agentmaestro.asgi import application
from core.models import Workspace, WorkspaceMembership
from agents.models import Agent
from runs.models import AgentRun, RunEvent
from runs.services.events import append_event


@pytest.mark.django_db(transaction=True)
def test_append_event_seq_increments_and_persists():
    """
    DB-safe sequencing: seq should be 1 then 2 for the same run.
    This is a synchronous test because it uses Django ORM heavily.
    """
    User = get_user_model()
    user = User.objects.create_user(username="testuser", password="x")

    ws = Workspace.objects.create(name="Test Workspace")
    WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)

    agent = Agent.objects.create(
        workspace=ws,
        name="TestAgent",
        system_prompt="You are a test agent.",
        created_by=user,
    )

    run = AgentRun.objects.create(
        workspace=ws,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="Hello",
    )

    evt1, seq1 = append_event(run_id=str(run.id), event_type="state_changed", payload={"to": "RUNNING"})
    evt2, seq2 = append_event(run_id=str(run.id), event_type="message", payload={"text": "hi"})

    assert seq1 == 1
    assert seq2 == 2

    assert RunEvent.objects.filter(run=run).count() == 2
    assert RunEvent.objects.get(run=run, seq=1).event_type == "state_changed"
    assert RunEvent.objects.get(run=run, seq=2).event_type == "message"

    assert evt1.seq == 1
    assert evt2.seq == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_append_event_broadcasts_to_run_group():
    """
    Integration: a WS client connected to /ws/ui/run/<run_id>/ should receive
    the broadcast emitted by append_event().

    IMPORTANT:
    - This is an async test, so all ORM work must be done via sync_to_async().
    """

    User = get_user_model()

    # MUST be sync, because sync_to_async only wraps sync functions.
    def setup_db_sync() -> str:
        user = User.objects.create_user(username="wsuser", password="x")
        ws = Workspace.objects.create(name="WS Workspace")
        WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)

        agent = Agent.objects.create(
            workspace=ws,
            name="WSAgent",
            system_prompt="You are a test agent.",
            created_by=user,
        )

        run = AgentRun.objects.create(
            workspace=ws,
            agent=agent,
            started_by=user,
            status=AgentRun.Status.PENDING,
            input_text="Hello",
        )
        return str(run.id)

    run_id = await sync_to_async(setup_db_sync, thread_sensitive=True)()

    communicator = WebsocketCommunicator(application, f"/ws/ui/run/{run_id}/")
    connected, _ = await communicator.connect()
    assert connected is True

    # Drain initial connected push
    _ = await communicator.receive_json_from()

    # append_event is sync -> call via sync_to_async
    _evt, seq = await sync_to_async(append_event, thread_sensitive=True)(
        run_id=run_id,
        event_type="db_broadcast_test",
        payload={"ok": True},
        broadcast_to_run=True,
    )
    assert seq == 1

    msg = await communicator.receive_json_from()
    assert msg["type"] == "push"
    assert msg["topic"] == "run.event"
    assert msg["event"] == "db_broadcast_test"
    assert msg["data"]["ok"] is True
    assert msg["run_id"] == run_id
    assert msg["seq"] == 1

    await communicator.disconnect()