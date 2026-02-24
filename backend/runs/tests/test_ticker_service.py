# backend/runs/tests/test_ticker_service.py
import threading

import pytest
from django.contrib.auth import get_user_model
from django.db import connections, transaction

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep, RunEvent
from runs.services.ticker import RunTickLocked, run_tick


def _make_run(name_suffix: str) -> AgentRun:
    User = get_user_model()
    user = User.objects.create_user(username=f"ticker{name_suffix}", password="x")
    ws = Workspace.objects.create(name=f"Ticker WS {name_suffix}")
    WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)

    agent = Agent.objects.create(
        workspace=ws,
        name=f"Ticker Agent {name_suffix}",
        system_prompt="Ticker test agent.",
        created_by=user,
    )

    return AgentRun.objects.create(
        workspace=ws,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="tick",
    )


@pytest.mark.django_db(transaction=True)
def test_run_tick_progresses_status_and_records_steps_and_events():
    run = _make_run("progress")

    first = run_tick(run_id=str(run.id))
    run.refresh_from_db()

    assert first["action"] == "started_run"
    assert run.status == AgentRun.Status.RUNNING
    assert AgentStep.objects.filter(run=run).count() == 1
    assert RunEvent.objects.filter(run=run).count() == 2

    second = run_tick(run_id=str(run.id))
    run.refresh_from_db()

    assert second["action"] == "completed_run"
    assert run.status == AgentRun.Status.COMPLETED
    assert AgentStep.objects.filter(run=run).count() == 2
    assert RunEvent.objects.filter(run=run).count() == 4

    event_types = list(RunEvent.objects.filter(run=run).order_by("seq").values_list("event_type", flat=True))
    assert event_types == ["state_changed", "step_appended", "step_appended", "state_changed"]


@pytest.mark.django_db(transaction=True)
def test_run_tick_raises_when_run_locked():
    run = _make_run("lock")
    started = threading.Event()
    release = threading.Event()

    def hold_lock():
        connection = connections["default"]
        try:
            with transaction.atomic(using=connection.alias):
                AgentRun.objects.select_for_update().get(id=run.id)
                started.set()
                release.wait(timeout=5)
        finally:
            connection.close()

    thread = threading.Thread(target=hold_lock, daemon=True)
    thread.start()
    assert started.wait(timeout=5)

    with pytest.raises(RunTickLocked):
        run_tick(run_id=str(run.id))

    release.set()
    thread.join(timeout=5)
