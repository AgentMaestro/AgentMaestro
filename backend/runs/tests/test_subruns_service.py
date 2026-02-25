from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, RunEvent, SubrunLink
from runs.services.subruns import (
    SUBRUN_COMPLETED_EVENT,
    SUBRUN_SPAWN_EVENT,
    complete_subrun,
    spawn_subrun,
)


def _make_run(name_suffix: str) -> AgentRun:
    User = get_user_model()
    user = User.objects.create_user(username=f"subrun{name_suffix}", password="x")
    workspace = Workspace.objects.create(name=f"Subrun WS {name_suffix}")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)

    agent = Agent.objects.create(
        workspace=workspace,
        name=f"Subrun Agent {name_suffix}",
        system_prompt="Subrun prompt.",
        created_by=user,
    )

    return AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="parent run",
    )


@pytest.mark.django_db(transaction=True)
def test_spawn_subrun_creates_child_and_waits():
    parent = _make_run("spawn")
    child = spawn_subrun(parent_run_id=str(parent.id), input_text="child prompt")

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.WAITING_FOR_SUBRUN
    assert child.parent_run_id == parent.id
    assert child.status == AgentRun.Status.PENDING

    link = SubrunLink.objects.get(child_run=child)
    assert link.join_policy == SubrunLink.JoinPolicy.WAIT_ALL
    assert link.failure_policy == SubrunLink.FailurePolicy.FAIL_FAST

    event_types = list(
        RunEvent.objects.filter(run=parent).order_by("seq").values_list("event_type", flat=True)
    )
    assert SUBRUN_SPAWN_EVENT in event_types


@pytest.mark.django_db(transaction=True)
def test_complete_subrun_resumes_parent():
    parent = _make_run("complete")
    child = spawn_subrun(parent_run_id=str(parent.id), input_text="child prompt")
    child.status = AgentRun.Status.COMPLETED
    child.ended_at = timezone.now()
    child.save(update_fields=["status", "ended_at", "updated_at"])

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.WAITING_FOR_SUBRUN

    result = complete_subrun(child_run_id=str(child.id))

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.RUNNING
    assert result == str(parent.id)

    event_types = list(
        RunEvent.objects.filter(run=parent).order_by("seq").values_list("event_type", flat=True)
    )
    assert SUBRUN_COMPLETED_EVENT in event_types


@pytest.mark.django_db(transaction=True)
def test_complete_subrun_skips_when_not_waiting():
    parent = _make_run("skip")
    child = spawn_subrun(parent_run_id=str(parent.id), input_text="child prompt")
    parent.status = AgentRun.Status.RUNNING
    parent.save(update_fields=["status", "updated_at"])

    result = complete_subrun(child_run_id=str(child.id))

    assert result is None


@pytest.mark.django_db(transaction=True)
def test_wait_any_policy_resumes_after_first_child():
    parent = _make_run("wait_any")
    group_id = uuid.uuid4()
    child1 = spawn_subrun(
        parent_run_id=str(parent.id),
        input_text="first",
        join_policy=SubrunLink.JoinPolicy.WAIT_ANY,
        group_id=str(group_id),
    )
    child2 = spawn_subrun(
        parent_run_id=str(parent.id),
        input_text="second",
        join_policy=SubrunLink.JoinPolicy.WAIT_ANY,
        group_id=str(group_id),
    )

    child1.status = AgentRun.Status.COMPLETED
    child1.ended_at = timezone.now()
    child1.save(update_fields=["status", "ended_at", "updated_at"])

    result = complete_subrun(child_run_id=str(child1.id))

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.RUNNING
    assert result == str(parent.id)
    child2.refresh_from_db()
    assert child2.status == AgentRun.Status.PENDING


@pytest.mark.django_db(transaction=True)
def test_quorum_policy_requires_threshold():
    parent = _make_run("quorum")
    group_id = uuid.uuid4()
    children = [
        spawn_subrun(
            parent_run_id=str(parent.id),
            input_text=f"child {idx}",
            join_policy=SubrunLink.JoinPolicy.QUORUM,
            quorum=2,
            group_id=str(group_id),
        )
        for idx in range(3)
    ]

    children[0].status = AgentRun.Status.COMPLETED
    children[0].ended_at = timezone.now()
    children[0].save(update_fields=["status", "ended_at", "updated_at"])
    assert complete_subrun(child_run_id=str(children[0].id)) is None
    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.WAITING_FOR_SUBRUN

    children[1].status = AgentRun.Status.COMPLETED
    children[1].ended_at = timezone.now()
    children[1].save(update_fields=["status", "ended_at", "updated_at"])
    assert complete_subrun(child_run_id=str(children[1].id)) == str(parent.id)

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.RUNNING


@pytest.mark.django_db(transaction=True)
def test_failure_policy_cancel_siblings():
    parent = _make_run("fail_policy")
    group_id = uuid.uuid4()
    children = [
        spawn_subrun(
            parent_run_id=str(parent.id),
            input_text=f"child {idx}",
            failure_policy=SubrunLink.FailurePolicy.CANCEL_SIBLINGS,
            group_id=str(group_id),
        )
        for idx in range(2)
    ]

    failing = children[0]
    failing.status = AgentRun.Status.FAILED
    failing.ended_at = timezone.now()
    failing.save(update_fields=["status", "ended_at", "updated_at"])

    result = complete_subrun(child_run_id=str(failing.id))

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.FAILED
    assert result is None

    children[1].refresh_from_db()
    assert children[1].status == AgentRun.Status.CANCELED
