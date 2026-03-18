import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model

from agents.consumers import AgentChatConsumer
from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord
from memory.remember_requests import (
    EXPLICIT_USER_REMEMBER_SOURCE_KIND,
    LOCAL_TIME_PREFERENCE_DEDUPE_KEY,
)
from runs.models import AgentRun

pytestmark = pytest.mark.django_db(transaction=True)


def test_accept_user_message_persists_explicit_remember_request(monkeypatch):
    User = get_user_model()
    user = User.objects.create_user(username="consumer-remember", password="x")
    workspace = Workspace.objects.create(name="Consumer Remember Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Consumer Remember Agent",
        soul="Remember durable user preferences.",
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        input_text="",
    )

    consumer = AgentChatConsumer(scope={"type": "websocket", "user": user})
    consumer.agent = agent
    consumer.run = run
    consumer.run_id = str(run.id)
    consumer.history = []
    consumer._queued_user_message = False

    async def fake_bootstrap(*args, **kwargs):
        return None

    async def fake_get_run_status(*args, **kwargs):
        return AgentRun.Status.RUNNING

    called = {"dispatch": False}

    async def fake_dispatch(self):
        called["dispatch"] = True

    monkeypatch.setattr("agents.consumers._bootstrap_memory_for_first_turn", fake_bootstrap)
    monkeypatch.setattr("agents.consumers._get_run_status", fake_get_run_status)
    monkeypatch.setattr(AgentChatConsumer, "_dispatch_to_provider", fake_dispatch)

    async_to_sync(consumer._accept_user_message)(
        "Remember that I'm in Ocala Florida, so please report time as of local time Ocala, FL",
        persist=False,
        emit_message=False,
    )

    record = MemoryRecord.objects.get(source_kind=EXPLICIT_USER_REMEMBER_SOURCE_KIND, source_ref=f"chat:{run.id}")
    assert record.scope_type == MemoryRecord.ScopeType.USER
    assert record.scope_id == str(user.id)
    assert record.dedupe_key == LOCAL_TIME_PREFERENCE_DEDUPE_KEY
    assert "Ocala" in record.content
    assert called["dispatch"] is True
