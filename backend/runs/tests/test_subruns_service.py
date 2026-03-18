from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from llm.services.providers.openai_ws import OpenAIResponsesWSNetworkError
from runs.models import AgentRun, RunEvent, SubrunLink
from runs.services.subruns import (
    HEADLESS_FAILURE_DIAGNOSTICS_EVENT,
    SUBRUN_CIRCUIT_OPEN_EVENT,
    SUBRUN_COMPLETED_EVENT,
    SUBRUN_SPAWN_EVENT,
    complete_subrun,
    run_subrun_flow,
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
        soul="Subrun prompt.",
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
    assert link.failure_policy == SubrunLink.FailurePolicy.IGNORE_FAILURE

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
    assert parent.status == AgentRun.Status.RUNNING
    assert result == str(parent.id)

    children[1].refresh_from_db()
    assert children[1].status == AgentRun.Status.CANCELED


@pytest.mark.django_db(transaction=True)
def test_default_failure_policy_resumes_parent_after_failed_child():
    parent = _make_run("ignore_failure_default")
    child = spawn_subrun(parent_run_id=str(parent.id), input_text="child prompt")

    child.status = AgentRun.Status.FAILED
    child.ended_at = timezone.now()
    child.error_summary = "transient network issue"
    child.save(update_fields=["status", "ended_at", "error_summary", "updated_at"])

    result = complete_subrun(child_run_id=str(child.id))

    parent.refresh_from_db()
    assert parent.status == AgentRun.Status.RUNNING
    assert result == str(parent.id)


@pytest.mark.django_db(transaction=True)
def test_interactive_subrun_flow_executes_child_inline_and_returns_child_text(monkeypatch):
    parent = _make_run("interactive_inline")

    async def fake_run(self, **kwargs):
        return {"run_id": "", "text": "Focused child summary", "status": "completed", "error": None}

    monkeypatch.setattr("runs.services.headless.LLMRunner.run", fake_run)
    monkeypatch.setattr("runs.services.headless.send_run_transport_message", lambda **kwargs: True)

    from runs.services.subruns import run_subrun_flow

    result = run_subrun_flow(
        parent_run_id=str(parent.id),
        input_text="Research the focused weather outlook for Ocala tennis conditions.",
        metadata={"purpose": "focused research"},
    )

    parent.refresh_from_db()
    child = AgentRun.objects.get(id=result["child_run_id"])

    assert result["completed_inline"] is True
    assert result["resumed_parent"] is True
    assert result["child_final_text"] == "Focused child summary"
    assert result["child_error_summary"] == ""
    assert child.parent_run_id == parent.id
    assert child.execution_mode == AgentRun.ExecutionMode.HEADLESS
    assert child.status == AgentRun.Status.COMPLETED
    assert child.final_text == "Focused child summary"
    assert parent.status == AgentRun.Status.RUNNING


@pytest.mark.django_db(transaction=True)
def test_headless_subrun_flow_executes_child_inline_and_resumes_parent(monkeypatch):
    parent = _make_run("headless_inline")
    parent.execution_mode = AgentRun.ExecutionMode.HEADLESS
    parent.trigger_kind = AgentRun.TriggerKind.SYSTEM
    parent.save(update_fields=["execution_mode", "trigger_kind", "updated_at"])

    async def fake_run(self, **kwargs):
        return {"run_id": "", "text": "Focused child summary", "status": "completed", "error": None}

    monkeypatch.setattr("runs.services.headless.LLMRunner.run", fake_run)
    monkeypatch.setattr("runs.services.headless.send_run_transport_message", lambda **kwargs: True)

    from runs.services.subruns import run_subrun_flow

    result = run_subrun_flow(
        parent_run_id=str(parent.id),
        input_text="Research the focused weather outlook for Ocala tennis conditions.",
        metadata={"purpose": "focused research"},
    )

    parent.refresh_from_db()
    child = AgentRun.objects.get(id=result["child_run_id"])

    assert result["completed_inline"] is True
    assert result["resumed_parent"] is True
    assert child.parent_run_id == parent.id
    assert child.execution_mode == AgentRun.ExecutionMode.HEADLESS
    assert child.status == AgentRun.Status.COMPLETED
    assert parent.status == AgentRun.Status.RUNNING

    event_types = list(
        RunEvent.objects.filter(run=parent).order_by("seq").values_list("event_type", flat=True)
    )
    assert SUBRUN_SPAWN_EVENT in event_types
    assert SUBRUN_COMPLETED_EVENT in event_types


@pytest.mark.django_db(transaction=True)
def test_inline_subrun_failure_returns_retryable_diagnostics(monkeypatch):
    parent = _make_run("retryable_failure")

    async def fake_run(self, **kwargs):
        raise OpenAIResponsesWSNetworkError(
            "OpenAI WS connection closed without a completed response",
            request_id="req-subrun-123",
            status=1006,
        )

    monkeypatch.setattr("runs.services.headless.LLMRunner.run", fake_run)
    monkeypatch.setattr("runs.services.headless.send_run_transport_message", lambda **kwargs: True)

    from runs.services.subruns import run_subrun_flow

    result = run_subrun_flow(
        parent_run_id=str(parent.id),
        input_text="Research the focused weather outlook for Ocala tennis conditions.",
        metadata={"purpose": "focused research"},
    )

    parent.refresh_from_db()

    assert result["parent_status"] == AgentRun.Status.RUNNING
    assert result["child_status"] == AgentRun.Status.FAILED
    assert result["resumed_parent"] is True
    assert result["child_retryable"] is True
    assert result["child_failure"]["classification"] == "network_error"
    assert result["child_failure"]["request_id"] == "req-subrun-123"


@pytest.mark.django_db(transaction=True)
def test_subrun_circuit_breaker_opens_after_two_network_failures(monkeypatch):
    parent = _make_run("circuit_open")

    for index in range(2):
        child = AgentRun.objects.create(
            workspace=parent.workspace,
            agent=parent.agent,
            parent_run=parent,
            started_by=parent.started_by,
            status=AgentRun.Status.FAILED,
            execution_mode=AgentRun.ExecutionMode.HEADLESS,
            trigger_kind=AgentRun.TriggerKind.SYSTEM,
            input_text=f"failed child {index}",
        )
        RunEvent.objects.create(
            run=child,
            seq=1,
            event_type=HEADLESS_FAILURE_DIAGNOSTICS_EVENT,
            payload={
                "summary": "OpenAI WS connection closed without a completed response",
                "classification": "network_error",
                "retryable": True,
            },
            correlation_id=child.correlation_id,
        )

    monkeypatch.setattr(
        "runs.services.subruns.spawn_subrun",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("spawn_subrun should not be called")),
    )

    result = run_subrun_flow(
        parent_run_id=str(parent.id),
        input_text="Research the next focus area sequentially.",
        metadata={"purpose": "focused research"},
    )

    parent.refresh_from_db()

    assert result["subrun_circuit_open"] is True
    assert result["subrun_circuit_reason"] == "repeated_network_error"
    assert result["subrun_failure_count"] == 2
    assert result["child_run_id"] == ""
    assert result["child_status"] == "SKIPPED"
    assert result["parent_status"] == AgentRun.Status.RUNNING
    assert parent.status == AgentRun.Status.RUNNING

    event_types = list(
        RunEvent.objects.filter(run=parent).order_by("seq").values_list("event_type", flat=True)
    )
    assert SUBRUN_CIRCUIT_OPEN_EVENT in event_types
