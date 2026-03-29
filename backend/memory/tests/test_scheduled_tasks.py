from datetime import timedelta, time

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord, RecurrenceRule, ScheduledTask
from memory.scheduled_approvals import INTERNAL_HEADLESS_APPROVAL_TOOL_NAME
from memory.scheduled_tasks import (
    SCHEDULED_TASK_CREATED_SOURCE_KIND,
    disable_scheduled_task,
    enable_scheduled_task,
    claim_due_scheduled_tasks,
    cleanup_scheduled_task_active_runs,
    create_scheduled_task,
    get_scheduled_task,
    list_scheduled_tasks,
    serialize_scheduled_task,
    update_scheduled_task,
)
from memory.tasks import run_due_scheduled_tasks
from runs.models import AgentRun
from runs.services.headless import HEADLESS_RUN_FAILED_SOURCE_KIND, launch_scheduled_task_run

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scheduled_task_agent():
    User = get_user_model()
    user = User.objects.create_user(username="memoryscheduler", password="x")
    workspace = Workspace.objects.create(name="Scheduled Memory Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Scheduled Memory Agent",
        soul="Handle recurring tasks",
    )
    return user, workspace, agent



def test_create_scheduled_task_creates_eposodic_memory_and_lists_it(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent

    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )

    assert scheduled_task.recurrence_rule is not None
    assert scheduled_task.recurrence_rule.frequency == RecurrenceRule.Frequency.DAILY
    assert scheduled_task.recurrence_rule.timezone == "America/New_York"
    assert scheduled_task.recurrence_rule.local_time.isoformat(timespec="minutes") == "08:00"
    assert scheduled_task.source_memory is not None
    assert scheduled_task.source_memory.memory_kind == MemoryRecord.MemoryKind.EPISODIC
    assert scheduled_task.source_memory.scope_type == MemoryRecord.ScopeType.AGENT
    assert scheduled_task.source_memory.source_kind == SCHEDULED_TASK_CREATED_SOURCE_KIND
    assert scheduled_task.source_memory.source_ref == str(scheduled_task.id)
    assert scheduled_task.source_memory.dedupe_key == f"scheduled-task-created:{scheduled_task.id}"
    assert scheduled_task.enabled is True
    assert scheduled_task.next_run_at is not None
    assert scheduled_task.execution_mode == ScheduledTask.ExecutionMode.HEADLESS_RUN

    listed = list_scheduled_tasks(agent=agent, enabled_only=True, limit=5)
    assert [task.id for task in listed] == [scheduled_task.id]
    serialized = serialize_scheduled_task(scheduled_task)
    assert "execution_payload" not in serialized
    detail = get_scheduled_task(agent=agent, scheduled_task_id=str(scheduled_task.id))
    assert detail is not None
    assert serialize_scheduled_task(detail, include_execution_payload=True)["execution_payload"] == {
        "location": "Richmond, VA",
        "source_domain": "weather.com",
    }


@override_settings(TIME_ZONE="Europe/London")
def test_create_scheduled_task_defaults_timezone_to_local_setting(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent

    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        execution_payload={"location": "London, UK", "source_domain": "weather.com"},
    )

    assert scheduled_task.recurrence_rule.timezone == "Europe/London"
    assert scheduled_task.timezone == "Europe/London"



def test_edit_disable_enable_scheduled_task_round_trip(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent

    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )

    edited = update_scheduled_task(
        scheduled_task,
        title="Updated scheduled task",
        enabled=False,
        local_time_value="09:30",
        timezone_name="America/New_York",
    )

    assert edited.title == "Updated scheduled task"
    assert edited.enabled is False
    assert edited.timezone == "America/New_York"
    assert edited.local_time.isoformat(timespec="minutes") == "09:30"

    disabled = disable_scheduled_task(edited)
    assert disabled.enabled is False

    enabled = enable_scheduled_task(disabled)
    assert enabled.enabled is True
    assert enabled.next_run_at is not None
    assert [task.id for task in list_scheduled_tasks(agent=agent, enabled_only=True, limit=5)] == [scheduled_task.id]
    listed_with_payload = serialize_scheduled_task(
        list_scheduled_tasks(agent=agent, enabled_only=True, limit=5)[0],
        include_execution_payload=True,
    )
    assert listed_with_payload["execution_payload"] == {"location": "Richmond, VA", "source_domain": "weather.com"}



def test_create_scheduled_task_promotes_focus_days_into_weekly_recurrence(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent

    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        title="Tennis weather report for Ocala, FL (34472)",
        local_time_value="07:00",
        timezone_name="America/New_York",
        execution_payload={
            "location": "Ocala, FL 34472",
            "focus_periods": ["09:00-12:00", "15:00-19:00"],
            "focus_days": ["Monday", "Wednesday", "Friday", "Saturday"],
            "interests": ["rain_chance", "temperature_range", "sky_conditions", "wind"],
        },
    )

    assert scheduled_task.recurrence_rule.frequency == RecurrenceRule.Frequency.WEEKLY
    assert scheduled_task.recurrence_rule.by_weekday == ["mon", "wed", "fri", "sat"]
    assert scheduled_task.schedule_kind == ScheduledTask.ScheduleKind.RECURRENCE_RULE



def test_create_scheduled_task_accepts_hourly_recurrence_config(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent

    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        recurrence_config={
            "timezone": "America/New_York",
            "frequency": "hourly",
            "interval": 1,
            "by_weekday": ["mon", "wed", "fri", "sat"],
            "run_minute": 0,
            "window_start_time": "09:00",
            "window_end_time": "19:00",
            "start_date": "2026-03-16",
        },
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )

    assert scheduled_task.schedule_kind == ScheduledTask.ScheduleKind.RECURRENCE_RULE
    assert scheduled_task.recurrence_rule.frequency == RecurrenceRule.Frequency.HOURLY
    assert scheduled_task.recurrence_rule.by_weekday == ["mon", "wed", "fri", "sat"]
    assert scheduled_task.recurrence_rule.run_minute == 0
    assert scheduled_task.recurrence_summary.startswith("Every 1 hour")



def test_claim_due_tasks_recomputes_next_run_from_recurrence_rule(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        recurrence_config={
            "timezone": "America/New_York",
            "frequency": "weekly",
            "interval": 1,
            "by_weekday": ["mon", "wed", "fri"],
            "local_time": "08:00",
            "start_date": "2026-03-16",
        },
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )
    scheduled_task.next_run_at = timezone.now() - timedelta(minutes=1)
    scheduled_task.save(update_fields=["next_run_at", "updated_at"])

    claimed = claim_due_scheduled_tasks(limit=5)

    assert claimed[0].id == scheduled_task.id
    scheduled_task.refresh_from_db()
    assert scheduled_task.next_run_at > timezone.now()



def test_run_due_scheduled_tasks_task_counts_success(monkeypatch, scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )
    scheduled_task.next_run_at = timezone.now() - timedelta(minutes=1)
    scheduled_task.save(update_fields=["next_run_at", "updated_at"])

    monkeypatch.setattr("memory.tasks.execute_scheduled_task", lambda task: "scheduled report sent")

    result = run_due_scheduled_tasks(limit=5)

    assert result == {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "launched": 0,
        "awaiting_approval": 0,
        "terminal_cleared": 0,
        "stale_cleared": 0,
    }
    scheduled_task.refresh_from_db()
    assert scheduled_task.failure_count == 0
    assert scheduled_task.last_result_summary == "scheduled report sent"



def test_run_due_scheduled_tasks_headless_first_run_waits_for_approval(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )
    scheduled_task.next_run_at = timezone.now() - timedelta(minutes=1)
    scheduled_task.save(update_fields=["next_run_at", "updated_at"])

    result = run_due_scheduled_tasks(limit=5)

    assert result == {
        "processed": 1,
        "succeeded": 0,
        "failed": 0,
        "launched": 0,
        "awaiting_approval": 1,
        "terminal_cleared": 0,
        "stale_cleared": 0,
    }
    scheduled_task.refresh_from_db()
    assert scheduled_task.active_run_id is not None
    assert scheduled_task.active_run.status == AgentRun.Status.WAITING_FOR_APPROVAL
    assert scheduled_task.active_run.tool_calls.filter(tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME).exists()



def test_cleanup_scheduled_task_active_runs_clears_terminal_run(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )
    _scheduled_task, run, launched = launch_scheduled_task_run(str(scheduled_task.id))

    assert launched is True
    AgentRun.objects.filter(id=run.id).update(status=AgentRun.Status.COMPLETED, ended_at=timezone.now())

    result = cleanup_scheduled_task_active_runs(limit=5)

    assert result == {"terminal_cleared": 1, "stale_cleared": 0}
    scheduled_task.refresh_from_db()
    assert scheduled_task.active_run is None
    assert scheduled_task.last_run_id == run.id



def test_cleanup_scheduled_task_active_runs_marks_stale_headless_run(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )
    _scheduled_task, run, launched = launch_scheduled_task_run(str(scheduled_task.id))

    assert launched is True
    stale_at = timezone.now() - timedelta(hours=2)
    AgentRun.objects.filter(id=run.id).update(
        status=AgentRun.Status.RUNNING,
        started_at=stale_at,
        updated_at=stale_at,
    )

    result = cleanup_scheduled_task_active_runs(limit=5)

    assert result == {"terminal_cleared": 0, "stale_cleared": 1}
    scheduled_task.refresh_from_db()
    run.refresh_from_db()
    assert scheduled_task.active_run is None
    assert scheduled_task.last_run_id == run.id
    assert scheduled_task.failure_count == 1
    assert scheduled_task.last_error == "stale_headless_run_timeout"
    assert run.status == AgentRun.Status.FAILED
    failure_memory = MemoryRecord.objects.filter(source_kind=HEADLESS_RUN_FAILED_SOURCE_KIND, source_ref=str(run.id)).first()
    assert failure_memory is not None



def test_cleanup_scheduled_task_active_runs_does_not_stale_waiting_for_approval_run(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_TASK,
        local_time_value="08:00",
        timezone_name="America/New_York",
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        execution_payload={"location": "Richmond, VA", "source_domain": "weather.com"},
    )
    _scheduled_task, run, launched = launch_scheduled_task_run(str(scheduled_task.id))

    assert launched is True
    stale_at = timezone.now() - timedelta(hours=2)
    AgentRun.objects.filter(id=run.id).update(updated_at=stale_at)

    result = cleanup_scheduled_task_active_runs(limit=5)

    assert result == {"terminal_cleared": 0, "stale_cleared": 0}
    scheduled_task.refresh_from_db()
    run.refresh_from_db()
    assert run.status == AgentRun.Status.WAITING_FOR_APPROVAL
    assert scheduled_task.active_run_id == run.id


