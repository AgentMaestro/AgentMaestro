from datetime import timedelta, time
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord, RecurrenceRule, ScheduledTask
from memory.scheduled_approvals import INTERNAL_HEADLESS_APPROVAL_TOOL_NAME
from memory.scheduled_tasks import (
    SCHEDULED_TASK_CREATED_SOURCE_KIND,
    SCHEDULED_TASK_EXECUTED_SOURCE_KIND,
    build_scheduled_task_execution_bucket_key,
    claim_due_scheduled_tasks,
    cleanup_scheduled_task_active_runs,
    create_scheduled_task,
    execute_scheduled_task,
    list_scheduled_tasks,
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
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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
    assert scheduled_task.execution_mode == ScheduledTask.ExecutionMode.DETERMINISTIC

    listed = list_scheduled_tasks(agent=agent, enabled_only=True, limit=5)
    assert [task.id for task in listed] == [scheduled_task.id]



def test_create_scheduled_task_promotes_focus_days_into_weekly_recurrence(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent

    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
        execution_mode=ScheduledTask.ExecutionMode.DETERMINISTIC,
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



def test_claim_and_execute_due_weather_report(monkeypatch, scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
        local_time_value=time(8, 0),
        timezone_name="America/New_York",
        execution_payload={
            "location": "Richmond, VA",
            "query": "site:weather.com Richmond VA daily and weekly weather forecast",
            "source_domain": "weather.com",
        },
    )
    scheduled_task.next_run_at = timezone.now() - timedelta(minutes=1)
    scheduled_task.save(update_fields=["next_run_at", "updated_at"])

    def fake_toolrunner(tool_name: str, args: dict, workspace_id: str):
        if tool_name == "web_search":
            return {
                "results": [
                    {
                        "title": "Richmond, VA Weather Forecast",
                        "url": "https://weather.com/weather/tenday/l/richmond",
                        "snippet": "A weather.com Richmond forecast.",
                    }
                ]
            }
        if tool_name == "fetch_url":
            return {
                "final_url": "https://weather.com/weather/tenday/l/richmond",
                "title": "Richmond 10 Day Weather",
                "content": "Today: Mostly sunny. This week: Warming trend with light rain on Thursday.",
                "truncated": False,
            }
        raise AssertionError(f"unexpected tool: {tool_name}")

    delivered: list[str] = []
    monkeypatch.setattr("memory.scheduled_tasks._call_toolrunner_tool", fake_toolrunner)
    monkeypatch.setattr("memory.scheduled_tasks._deliver_scheduled_task_message", lambda task, text: delivered.append(text))

    claimed = claim_due_scheduled_tasks(limit=5)
    assert [task.id for task in claimed] == [scheduled_task.id]

    summary = execute_scheduled_task(claimed[0])
    assert "daily weather report" in summary.lower()
    assert delivered
    assert "weather.com" in delivered[0]

    execution_memory = (
        MemoryRecord.objects.filter(source_kind=SCHEDULED_TASK_EXECUTED_SOURCE_KIND, source_ref=str(scheduled_task.id))
        .order_by("-created_at")
        .first()
    )
    assert execution_memory is not None
    assert execution_memory.dedupe_key == build_scheduled_task_execution_bucket_key(scheduled_task)



def test_claim_due_tasks_recomputes_next_run_from_recurrence_rule(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
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


def test_create_scheduled_task_coerces_generic_task_to_headless(scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent

    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_DAILY_TASK,
        local_time_value="05:00",
        timezone_name="America/New_York",
        execution_mode=ScheduledTask.ExecutionMode.DETERMINISTIC,
        execution_payload={"objective": "Create a backup summary."},
    )

    assert scheduled_task.execution_mode == ScheduledTask.ExecutionMode.HEADLESS_RUN



def test_run_due_scheduled_tasks_corrects_generic_task_to_headless(monkeypatch, scheduled_task_agent):
    user, _workspace, agent = scheduled_task_agent
    scheduled_task = create_scheduled_task(
        agent=agent,
        owner=user,
        task_type=ScheduledTask.TaskType.OTHER_DAILY_TASK,
        local_time_value="05:00",
        timezone_name="America/New_York",
        execution_mode=ScheduledTask.ExecutionMode.HEADLESS_RUN,
        execution_payload={"objective": "Create a backup summary."},
    )
    scheduled_task.next_run_at = timezone.now() - timedelta(minutes=1)
    scheduled_task.execution_mode = ScheduledTask.ExecutionMode.DETERMINISTIC
    scheduled_task.save(update_fields=["next_run_at", "execution_mode", "updated_at"])

    fake_run = SimpleNamespace(id="run-123", status=AgentRun.Status.WAITING_FOR_APPROVAL)
    monkeypatch.setattr("memory.tasks.launch_scheduled_task_run", lambda task_id: (scheduled_task, fake_run, True))

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
    assert scheduled_task.execution_mode == ScheduledTask.ExecutionMode.HEADLESS_RUN
