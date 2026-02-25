from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace
from runs.models import AgentRun, RunEvent, RunArchive
from runs.services.checkpoints import archive_completed_runs


@pytest.mark.django_db
def test_archive_completed_runs_creates_bundle(tmp_path):
    workspace = Workspace.objects.create(name="Archive WS")
    agent = Agent.objects.create(workspace=workspace, name="Archive Agent", system_prompt="Plan")
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        status=AgentRun.Status.COMPLETED,
        ended_at=timezone.now(),
    )
    RunEvent.objects.create(run=run, seq=1, event_type="state_changed", payload={"from": "PENDING", "to": "COMPLETED"})

    with override_settings(AGENTMAESTRO_ARCHIVE_ROOT=str(tmp_path)):
        results = archive_completed_runs(older_than_days=0, limit=1, compact=False, retention_days=1)

    assert len(results) == 1
    result = results[0]
    run.refresh_from_db()
    assert run.archived_at is not None
    archive_path = Path(result["archive_path"])
    assert archive_path.exists()
    assert RunArchive.objects.filter(run=run).exists()


@pytest.mark.django_db
def test_archive_runs_command_compacts_verbose_events(tmp_path, capsys):
    workspace = Workspace.objects.create(name="Command WS")
    agent = Agent.objects.create(workspace=workspace, name="Command Agent", system_prompt="Plan")
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        status=AgentRun.Status.COMPLETED,
        ended_at=timezone.now(),
    )
    token_event = RunEvent.objects.create(run=run, seq=1, event_type="token_stream", payload={"token": "keep"})
    RunEvent.objects.filter(id=token_event.id).update(created_at=timezone.now() - timedelta(days=60))
    RunEvent.objects.create(run=run, seq=2, event_type="state_changed", payload={"status": "COMPLETED"})

    with override_settings(AGENTMAESTRO_ARCHIVE_ROOT=str(tmp_path)):
        call_command(
            "archive_runs",
            older_than=0,
            limit=1,
            compact=True,
            verbose_events=["token_stream"],
        )

    run.refresh_from_db()
    assert run.archived_at is not None
    assert RunEvent.objects.filter(run=run, event_type="token_stream").count() == 0
    assert RunEvent.objects.filter(run=run).exclude(event_type="run_archived").count() == 1
    captured = capsys.readouterr()
    assert "Archived run" in captured.out
