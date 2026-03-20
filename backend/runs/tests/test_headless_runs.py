from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord, ScheduledTask, ScheduledTaskApproval
from memory.scheduled_approvals import HEADLESS_APPROVAL_REASON_DRIFT, INTERNAL_HEADLESS_APPROVAL_TOOL_NAME
from memory.scheduled_tasks import create_scheduled_task
from runs.models import AgentRun
from runs.services.headless import (
    HEADLESS_RUN_COMPLETED_SOURCE_KIND,
    HEADLESS_RUN_FAILED_SOURCE_KIND,
    execute_headless_run,
    launch_scheduled_task_run,
)
from tools.models import ToolCall
from tools.services.approvals import approve_tool_call, deny_tool_call
from tools.services.execution import execute_tool_call

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def headless_task_agent():
    User = get_user_model()
    user = User.objects.create_user(username="headlessrunner", password="x")
    workspace = Workspace.objects.create(name="Headless Run Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Headless Agent",
        soul="Handle background work",
        default_model="gpt-5-codex",
    )
    return user, workspace, agent



def _create_headless_task(user, agent):
    return create_scheduled_task(
        agent=agent,
        owner=user,
        title="daily weather report for Richmond, VA",
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )



def _approve_gate_and_execute(tool_call: ToolCall, user, monkeypatch):
    enqueued_runs: list[str] = []
    monkeypatch.setattr("tools.services.approvals._enqueue_and_schedule", lambda tool_call_id: None)

    class _DelayProxy:
        @staticmethod
        def delay(run_id: str):
            enqueued_runs.append(run_id)

    monkeypatch.setattr("runs.tasks.execute_headless_run_task", _DelayProxy)
    approve_tool_call(tool_call_id=str(tool_call.id), user=user)
    execute_tool_call(str(tool_call.id))
    tool_call.refresh_from_db()
    return enqueued_runs



def test_launch_scheduled_task_run_requests_first_run_approval(headless_task_agent):
    user, workspace, agent = headless_task_agent
    scheduled_task = _create_headless_task(user, agent)

    _scheduled_task, run, launched = launch_scheduled_task_run(str(scheduled_task.id))

    assert launched is True
    assert run.workspace == workspace
    assert run.agent == agent
    assert run.execution_mode == AgentRun.ExecutionMode.HEADLESS
    assert run.trigger_kind == AgentRun.TriggerKind.SCHEDULED_TASK
    assert run.trigger_ref == str(scheduled_task.id)
    run.refresh_from_db()
    assert run.status == AgentRun.Status.WAITING_FOR_APPROVAL
    assert run.approval_mode == AgentRun.ApprovalMode.REQUESTED
    assert run.approval_fingerprint
    gate_call = ToolCall.objects.get(run=run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)
    assert gate_call.status == ToolCall.Status.PENDING_APPROVAL
    assert ScheduledTaskApproval.objects.count() == 0
    scheduled_task.refresh_from_db()
    assert scheduled_task.active_run_id == run.id
    assert scheduled_task.last_run_id == run.id



def test_approved_gate_creates_reusable_approval_and_queues_run(monkeypatch, headless_task_agent):
    user, _workspace, agent = headless_task_agent
    scheduled_task = _create_headless_task(user, agent)
    _scheduled_task, run, _launched = launch_scheduled_task_run(str(scheduled_task.id))
    gate_call = ToolCall.objects.get(run=run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)

    enqueued_runs = _approve_gate_and_execute(gate_call, user, monkeypatch)

    run.refresh_from_db()
    approval = ScheduledTaskApproval.objects.get(scheduled_task=scheduled_task)
    assert gate_call.status == ToolCall.Status.COMPLETED
    assert enqueued_runs == [str(run.id)]
    assert run.approval_mode == AgentRun.ApprovalMode.MANUAL
    assert run.approval_source_ref == str(approval.id)
    assert approval.fingerprint == run.approval_fingerprint
    assert approval.use_count == 1



def test_future_identical_execution_inherits_existing_approval(monkeypatch, headless_task_agent):
    user, _workspace, agent = headless_task_agent
    scheduled_task = _create_headless_task(user, agent)
    _scheduled_task, first_run, _launched = launch_scheduled_task_run(str(scheduled_task.id))
    gate_call = ToolCall.objects.get(run=first_run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)
    _approve_gate_and_execute(gate_call, user, monkeypatch)

    AgentRun.objects.filter(id=first_run.id).update(status=AgentRun.Status.COMPLETED, ended_at=timezone.now())
    ScheduledTask.objects.filter(id=scheduled_task.id).update(active_run=None, last_run_id=first_run.id)

    _scheduled_task, second_run, launched_again = launch_scheduled_task_run(str(scheduled_task.id))

    assert launched_again is True
    second_run.refresh_from_db()
    approval = ScheduledTaskApproval.objects.get(scheduled_task=scheduled_task)
    assert second_run.status == AgentRun.Status.PENDING
    assert second_run.approval_mode == AgentRun.ApprovalMode.INHERITED
    assert second_run.approval_source_ref == str(approval.id)
    assert second_run.tool_calls.count() == 0
    approval.refresh_from_db()
    assert approval.use_count == 2



def test_changed_payload_requires_fresh_approval(monkeypatch, headless_task_agent):
    user, _workspace, agent = headless_task_agent
    scheduled_task = _create_headless_task(user, agent)
    _scheduled_task, first_run, _launched = launch_scheduled_task_run(str(scheduled_task.id))
    gate_call = ToolCall.objects.get(run=first_run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)
    _approve_gate_and_execute(gate_call, user, monkeypatch)

    AgentRun.objects.filter(id=first_run.id).update(status=AgentRun.Status.COMPLETED, ended_at=timezone.now())
    ScheduledTask.objects.filter(id=scheduled_task.id).update(
        active_run=None,
        last_run_id=first_run.id,
        execution_payload={"location": "Norfolk, VA", "source_domain": "weather.com"},
    )

    _scheduled_task, second_run, launched_again = launch_scheduled_task_run(str(scheduled_task.id))

    assert launched_again is True
    second_run.refresh_from_db()
    assert second_run.status == AgentRun.Status.WAITING_FOR_APPROVAL
    assert second_run.approval_mode == AgentRun.ApprovalMode.REQUESTED
    gate_call = ToolCall.objects.get(run=second_run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)
    assert gate_call.args["approval_reason"] == HEADLESS_APPROVAL_REASON_DRIFT
    assert gate_call.args["previous_approval_id"]



def test_denied_gate_fails_run_and_clears_task(headless_task_agent):
    user, _workspace, agent = headless_task_agent
    scheduled_task = _create_headless_task(user, agent)
    _scheduled_task, run, _launched = launch_scheduled_task_run(str(scheduled_task.id))
    gate_call = ToolCall.objects.get(run=run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)

    deny_tool_call(tool_call_id=str(gate_call.id), user=user, reason="operator denied")

    run.refresh_from_db()
    scheduled_task.refresh_from_db()
    assert run.status == AgentRun.Status.FAILED
    assert run.error_summary == "operator denied"
    assert scheduled_task.active_run is None
    assert scheduled_task.failure_count == 1
    assert scheduled_task.last_error == "operator denied"



def test_execute_headless_run_success_updates_task_memory_and_delivery(monkeypatch, headless_task_agent):
    user, _workspace, agent = headless_task_agent
    scheduled_task = _create_headless_task(user, agent)
    _scheduled_task, run, _launched = launch_scheduled_task_run(str(scheduled_task.id))
    gate_call = ToolCall.objects.get(run=run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)
    _approve_gate_and_execute(gate_call, user, monkeypatch)

    async def fake_run(self, **kwargs):
        return {"run_id": "", "text": "Today is sunny. This week warms up.", "status": "completed", "error": None}

    deliveries: list[tuple[str, str]] = []
    monkeypatch.setattr("runs.services.headless.LLMRunner.run", fake_run)
    monkeypatch.setattr(
        "runs.services.headless.send_run_transport_message",
        lambda **kwargs: deliveries.append((kwargs["run_id"], kwargs["text"])) or True,
    )

    completed_run = execute_headless_run(str(run.id))

    assert completed_run.status == AgentRun.Status.COMPLETED
    assert completed_run.final_text == "Today is sunny. This week warms up."
    scheduled_task.refresh_from_db()
    assert scheduled_task.active_run is None
    assert scheduled_task.last_run_id == run.id
    assert scheduled_task.failure_count == 0
    assert scheduled_task.last_result_summary == "Today is sunny. This week warms up."
    assert deliveries == [(str(run.id), "Today is sunny. This week warms up.")]
    outcome_memory = MemoryRecord.objects.filter(source_kind=HEADLESS_RUN_COMPLETED_SOURCE_KIND, source_ref=str(run.id)).first()
    assert outcome_memory is not None
    assert outcome_memory.memory_kind == MemoryRecord.MemoryKind.EPISODIC



def test_execute_headless_run_failure_marks_task_and_writes_failure_memory(monkeypatch, headless_task_agent):
    user, _workspace, agent = headless_task_agent
    scheduled_task = _create_headless_task(user, agent)
    _scheduled_task, run, _launched = launch_scheduled_task_run(str(scheduled_task.id))
    gate_call = ToolCall.objects.get(run=run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)
    _approve_gate_and_execute(gate_call, user, monkeypatch)

    async def failing_run(self, **kwargs):
        raise RuntimeError("headless boom")

    monkeypatch.setattr("runs.services.headless.LLMRunner.run", failing_run)
    monkeypatch.setattr("runs.services.headless.send_run_transport_message", lambda **kwargs: True)

    failed_run = execute_headless_run(str(run.id))

    assert failed_run.status == AgentRun.Status.FAILED
    assert "headless boom" in failed_run.error_summary
    assert "retryable=no" in failed_run.error_summary
    scheduled_task.refresh_from_db()
    assert scheduled_task.active_run is None
    assert scheduled_task.failure_count == 1
    assert "headless boom" in scheduled_task.last_error
    failure_memory = MemoryRecord.objects.filter(source_kind=HEADLESS_RUN_FAILED_SOURCE_KIND, source_ref=str(run.id)).first()
    assert failure_memory is not None


def test_execute_headless_run_google_bridge_task_uses_bridge_not_llm(monkeypatch, headless_task_agent):
    user, _workspace, agent = headless_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        title="gmail read task",
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "query": "inbox newer_than:1d",
            "max_results": 5,
        },
    )
    _scheduled_task, run, _launched = launch_scheduled_task_run(str(scheduled_task.id))
    gate_call = ToolCall.objects.get(run=run, tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME)
    _approve_gate_and_execute(gate_call, user, monkeypatch)

    def fake_google_bridge_task(**kwargs):
        return {
            "ok": True,
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "summary_text": "Found 1 Gmail messages. Message IDs: msg-1.",
            "result": {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1},
            "account": {
                "google_subject": "sub-123",
                "email": "user@example.com",
                "workspace_id": str(kwargs["workspace"].id),
                "owner_id": str(kwargs["owner"].id),
            },
        }

    deliveries: list[tuple[str, str]] = []
    monkeypatch.setattr("google_bridge.services.bridge.execute_google_task", fake_google_bridge_task)

    async def fail_run(self, **kwargs):
        raise AssertionError("LLMRunner should not be used for Google bridge tasks")

    monkeypatch.setattr("runs.services.headless.LLMRunner.run", fail_run)
    monkeypatch.setattr(
        "runs.services.headless.send_run_transport_message",
        lambda **kwargs: deliveries.append((kwargs["run_id"], kwargs["text"])) or True,
    )

    completed_run = execute_headless_run(str(run.id))

    assert completed_run.status == AgentRun.Status.COMPLETED
    assert completed_run.final_text == "Found 1 Gmail messages. Message IDs: msg-1."
    assert deliveries == [(str(run.id), "Found 1 Gmail messages. Message IDs: msg-1.")]
    outcome_memory = MemoryRecord.objects.filter(source_kind=HEADLESS_RUN_COMPLETED_SOURCE_KIND, source_ref=str(run.id)).first()
    assert outcome_memory is not None
