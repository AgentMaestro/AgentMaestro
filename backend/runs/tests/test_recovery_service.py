# backend/runs/tests/test_recovery_service.py
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep, RunEvent
from runs.services.recovery import (
    LOCK_LEASE_SECONDS,
    PermanentRunError,
    TransientRunError,
    cancel_run,
    handle_run_failure,
    release_stale_lock,
    reconcile_waiting_parents_and_leases,
)
from runs.services.subruns import SUBRUN_CANCELLED_EVENT, spawn_subrun
from runs.services.ticker import run_tick


def _make_run(name_suffix: str) -> AgentRun:
    User = get_user_model()
    user = User.objects.create_user(username=f"recovery{name_suffix}", password="x")
    workspace = Workspace.objects.create(name=f"Recovery WS {name_suffix}")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        name=f"Recovery Agent {name_suffix}",
        system_prompt="Recovery test prompt.",
        created_by=user,
    )
    return AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="recovery test",
    )


@pytest.mark.django_db(transaction=True)
def test_release_stale_lock_clears_fields():
    run = _make_run("stale")
    now = timezone.now()
    run.locked_by = "other"
    run.locked_at = now - timedelta(seconds=LOCK_LEASE_SECONDS + 10)
    run.lock_expires_at = run.locked_at + timedelta(seconds=LOCK_LEASE_SECONDS)
    run.save(update_fields=["locked_by", "locked_at", "lock_expires_at", "updated_at"])

    assert release_stale_lock(run, now=now)
    run.refresh_from_db()
    assert run.locked_by == ""
    assert run.locked_at is None
    assert run.lock_expires_at is None


@pytest.mark.django_db(transaction=True)
def test_run_tick_noops_for_duplicate_cursor():
    run = _make_run("duplicate")
    run.current_step_index = 1
    run.save(update_fields=["current_step_index", "updated_at"])
    result = run_tick(run_id=str(run.id))

    assert result["action"] == "noop"
    assert AgentStep.objects.filter(run=run).count() == 0
    assert RunEvent.objects.filter(run=run).count() == 0


@pytest.mark.django_db(transaction=True)
def test_run_tick_reclaims_stale_lock():
    run = _make_run("stale_tick")
    stale = timezone.now() - timedelta(seconds=LOCK_LEASE_SECONDS + 5)
    run.locked_by = "other"
    run.locked_at = stale
    run.lock_expires_at = stale + timedelta(seconds=LOCK_LEASE_SECONDS)
    run.save(update_fields=["locked_by", "locked_at", "lock_expires_at", "updated_at"])

    result = run_tick(run_id=str(run.id))
    assert result["action"] == "started_run"


@pytest.mark.django_db(transaction=True)
def test_handle_failure_retry_and_failure_flow():
    run = _make_run("errors")
    transient = handle_run_failure(run_id=str(run.id), exc=TransientRunError("transient"))
    assert transient.retry
    run.refresh_from_db()
    assert run.status == AgentRun.Status.PENDING

    permanent = handle_run_failure(run_id=str(run.id), exc=PermanentRunError("permanent"))
    assert not permanent.retry
    run.refresh_from_db()
    assert run.status == AgentRun.Status.FAILED


@pytest.mark.django_db(transaction=True)
def test_cancel_parent_cancels_children():
    parent = _make_run("cancel_parent")
    parent.status = AgentRun.Status.RUNNING
    parent.save(update_fields=["status", "updated_at"])
    child = spawn_subrun(parent_run_id=str(parent.id), input_text="stop child")
    cancel_run(run_id=str(parent.id), reason="operator cancel")

    parent.refresh_from_db()
    child.refresh_from_db()

    assert parent.status == AgentRun.Status.CANCELED
    assert child.status == AgentRun.Status.CANCELED

    event_types = list(
        RunEvent.objects.filter(run=parent).order_by("seq").values_list("event_type", flat=True)
    )
    assert "run_cancelled" in event_types
    assert SUBRUN_CANCELLED_EVENT in event_types


@pytest.mark.django_db(transaction=True)
def test_cancel_child_triggers_parent_policy():
    parent = _make_run("cancel_child")
    parent.status = AgentRun.Status.RUNNING
    parent.save(update_fields=["status", "updated_at"])
    child = spawn_subrun(parent_run_id=str(parent.id), input_text="child to cancel")
    cancel_run(run_id=str(child.id), reason="abort child")

    parent.refresh_from_db()
    child.refresh_from_db()

    assert child.status == AgentRun.Status.CANCELED
    assert parent.status == AgentRun.Status.FAILED

    event_types = list(
        RunEvent.objects.filter(run=parent).order_by("seq").values_list("event_type", flat=True)
    )
    assert SUBRUN_CANCELLED_EVENT in event_types


@pytest.mark.django_db(transaction=True)
def test_reconcile_waiting_parents_resumes(monkeypatch):
    parent = _make_run("reconcile_parent")
    parent.status = AgentRun.Status.WAITING_FOR_SUBRUN
    parent.save(update_fields=["status", "updated_at"])

    child = AgentRun.objects.create(
        workspace=parent.workspace,
        agent=parent.agent,
        parent_run=parent,
        status=AgentRun.Status.COMPLETED,
        started_by=parent.started_by,
        input_text="done",
        ended_at=timezone.now(),
    )

    scheduled = []

    def fake_delay(run_id):
        scheduled.append(run_id)

    monkeypatch.setattr("runs.tasks.run_tick.delay", fake_delay)

    result = reconcile_waiting_parents_and_leases()

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.RUNNING
    assert str(parent.id) in scheduled
    assert result["resumed_waiting_parents"] == 1


@pytest.mark.django_db(transaction=True)
def test_reconcile_stale_leases(monkeypatch):
    run = _make_run("reconcile_stale")
    stale = timezone.now() - timedelta(seconds=LOCK_LEASE_SECONDS + 10)
    run.locked_by = "other"
    run.locked_at = stale
    run.lock_expires_at = stale + timedelta(seconds=LOCK_LEASE_SECONDS)
    run.save(update_fields=["locked_by", "locked_at", "lock_expires_at", "updated_at"])

    scheduled = []

    def fake_delay(run_id):
        scheduled.append(run_id)

    monkeypatch.setattr("runs.tasks.run_tick.delay", fake_delay)

    result = reconcile_waiting_parents_and_leases()

    run.refresh_from_db()
    assert run.locked_by == ""
    assert result["stale_leases_reclaimed"] == 1
    assert str(run.id) in scheduled
