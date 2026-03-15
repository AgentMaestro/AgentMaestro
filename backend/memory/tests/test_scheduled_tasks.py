from datetime import timedelta, time

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord, ScheduledTask
from memory.scheduled_tasks import (
    SCHEDULED_TASK_CREATED_SOURCE_KIND,
    SCHEDULED_TASK_EXECUTED_SOURCE_KIND,
    build_scheduled_task_execution_bucket_key,
    claim_due_scheduled_tasks,
    create_scheduled_task,
    execute_scheduled_task,
    list_scheduled_tasks,
)
from memory.tasks import run_due_scheduled_tasks

pytestmark = pytest.mark.django_db


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

    assert scheduled_task.source_memory is not None
    assert scheduled_task.source_memory.memory_kind == MemoryRecord.MemoryKind.EPISODIC
    assert scheduled_task.source_memory.scope_type == MemoryRecord.ScopeType.AGENT
    assert scheduled_task.source_memory.source_kind == SCHEDULED_TASK_CREATED_SOURCE_KIND
    assert scheduled_task.source_memory.source_ref == str(scheduled_task.id)
    assert scheduled_task.source_memory.dedupe_key == f"scheduled-task-created:{scheduled_task.id}"
    assert scheduled_task.enabled is True
    assert scheduled_task.next_run_at is not None

    listed = list_scheduled_tasks(agent=agent, enabled_only=True, limit=5)
    assert [task.id for task in listed] == [scheduled_task.id]


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

    assert result == {"processed": 1, "succeeded": 1, "failed": 0}
    scheduled_task.refresh_from_db()
    assert scheduled_task.failure_count == 0
    assert scheduled_task.last_result_summary == "scheduled report sent"
