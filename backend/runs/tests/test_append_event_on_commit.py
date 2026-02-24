# backend/runs/tests/test_append_event_on_commit.py
import asyncio
import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.db import transaction

from agentmaestro.asgi import application
from core.models import Workspace, WorkspaceMembership
from agents.models import Agent
from runs.models import AgentRun
from runs.services.events import append_event


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_append_event_does_not_broadcast_on_rollback():
    User = get_user_model()

    @database_sync_to_async
    def setup_db() -> str:
        user = User.objects.create_user(username="rbuser", password="x")
        ws = Workspace.objects.create(name="Rollback WS")
        WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)
        agent = Agent.objects.create(workspace=ws, name="A", system_prompt="x", created_by=user)
        run = AgentRun.objects.create(workspace=ws, agent=agent, started_by=user, input_text="x")
        return str(run.id)

    @database_sync_to_async
    def do_rollback(run_id: str) -> None:
        try:
            with transaction.atomic():
                append_event(
                    run_id=run_id,
                    event_type="should_not_broadcast",
                    payload={"x": 1},
                    broadcast_to_run=True,
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass

    run_id = await setup_db()

    communicator = WebsocketCommunicator(application, f"/ws/ui/run/{run_id}/")
    ok, _ = await communicator.connect()
    assert ok is True

    try:
        _ = await communicator.receive_json_from()  # initial "connected"

        await do_rollback(run_id)

        # IMPORTANT: on_commit should NOT run, so nothing should be received.
        got_anything = await communicator.receive_nothing(timeout=0.25)
        assert got_anything is True  # True means: received nothing within timeout

    finally:
        # Avoid teardown flakiness when pytest is shutting down the loop/DB.
        try:
            await communicator.disconnect()
        except asyncio.CancelledError:
            pass