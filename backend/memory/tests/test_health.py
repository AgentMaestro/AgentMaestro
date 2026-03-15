import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.health import build_memory_health_report
from memory.models import MemoryHealthSnapshot, MemoryRecord, ScheduledTask
from memory.services import remember


pytestmark = pytest.mark.django_db


def _create_health_context():
    User = get_user_model()
    user = User.objects.create_user(username="memoryhealth", password="x")
    workspace = Workspace.objects.create(name="Memory Health Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Memory Health Agent",
        soul="Track memory health",
    )
    return user, workspace, agent


def test_build_memory_health_report_counts_and_trend_snapshot():
    user, workspace, agent = _create_health_context()
    remember(
        scope_type="sandbox",
        scope_id=str(workspace.id),
        memory_kind="semantic",
        content="The Django backend lives in backend/.",
        importance=0.85,
        pinned=True,
        dedupe_key="fact:backend-location",
        source_kind="manual_remember",
    )
    procedural = remember(
        agent=agent,
        memory_kind="procedural",
        content="Clear webhook before local polling tests.",
        importance=0.90,
        source_kind="manual_remember",
    )
    episodic = remember(
        agent=agent,
        memory_kind="episodic",
        content="Daily weather report executed successfully.",
        importance=Decimal("0.10"),
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
        expires_at=timezone.now() - timedelta(minutes=5),
    )
    ScheduledTask.objects.create(
        workspace=workspace,
        agent=agent,
        owner=user,
        title="daily weather report for Richmond, VA",
        task_type=ScheduledTask.TaskType.DAILY_WEATHER_REPORT,
        timezone="America/New_York",
        local_time=timezone.localtime().time().replace(second=0, microsecond=0),
        next_run_at=timezone.now() + timedelta(hours=1),
        last_run_at=timezone.now() - timedelta(hours=2),
        last_success_at=timezone.now() - timedelta(hours=2),
        enabled=True,
        failure_count=0,
        source_memory=episodic,
    )

    baseline = MemoryHealthSnapshot.objects.create(
        retention_days=30,
        compare_days=30,
        total_records=1,
        pinned_records=0,
        high_importance_records=0,
        retention_candidate_records=0,
        scheduled_tasks_total=0,
        report_json={"baseline": True},
    )
    old_time = timezone.now() - timedelta(days=31)
    MemoryHealthSnapshot.objects.filter(id=baseline.id).update(created_at=old_time, updated_at=old_time)

    report = build_memory_health_report(compare_days=30, save_snapshot=True)

    assert report["memory"]["total_records"] == 3
    assert report["memory"]["pinned_records"] == 1
    assert report["memory"]["high_importance_records"] == 2
    assert report["memory"]["by_kind"][MemoryRecord.MemoryKind.EPISODIC] == 1
    assert report["memory"]["by_kind"][MemoryRecord.MemoryKind.SEMANTIC] == 1
    assert report["memory"]["by_kind"][MemoryRecord.MemoryKind.PROCEDURAL] == 1
    assert report["scheduled_tasks"]["total"] == 1
    assert report["scheduled_tasks"]["executed_at_least_once"] == 1
    assert report["scheduled_tasks"]["memory_records"]["scheduled_task_executed"] == 1
    assert report["retention"]["eligible_records"] == 1
    assert report["trend"]["baseline_available"] is True
    assert report["trend"]["total_records_then"] == 1
    assert report["trend"]["delta_total_records"] == 2
    assert report["snapshot"] is not None
    assert MemoryHealthSnapshot.objects.count() == 2


def test_memory_health_report_command_outputs_json_and_saves_snapshot():
    _user, workspace, _agent = _create_health_context()
    remember(
        scope_type="sandbox",
        scope_id=str(workspace.id),
        memory_kind="semantic",
        content="Memory health command smoke.",
        source_kind="manual_remember",
    )

    stdout = StringIO()
    call_command("memory_health_report", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["memory"]["total_records"] == 1
    assert payload["snapshot"] is not None
    assert MemoryHealthSnapshot.objects.count() == 1


def test_memory_health_report_alias_supports_no_save():
    _user, workspace, _agent = _create_health_context()
    remember(
        scope_type="sandbox",
        scope_id=str(workspace.id),
        memory_kind="semantic",
        content="Memory health alias smoke.",
        source_kind="manual_remember",
    )

    stdout = StringIO()
    call_command("memory_health_report", "--no-save", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["memory"]["total_records"] == 1
    assert payload["snapshot"] is None
    assert MemoryHealthSnapshot.objects.count() == 0
