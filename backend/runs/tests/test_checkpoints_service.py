import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace
from runs.models import AgentRun, AgentStep, RunEvent, RunArchive
from runs.services.checkpoints import compact_events, create_checkpoint


@pytest.mark.django_db
def test_create_checkpoint_generates_bundle(tmp_path):
    workspace = Workspace.objects.create(name="Checkpoint WS")
    agent = Agent.objects.create(workspace=workspace, name="Checkpoint Agent", system_prompt="Prompt")
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        status=AgentRun.Status.COMPLETED,
        ended_at=timezone.now(),
    )
    AgentStep.objects.create(run=run, step_index=0, kind=AgentStep.Kind.PLAN, payload={"plan": "ok"})
    RunEvent.objects.create(run=run, seq=1, event_type="stream", payload={"foo": "bar"})

    with override_settings(AGENTMAESTRO_ARCHIVE_ROOT=str(tmp_path)):
        archive = create_checkpoint(str(run.id), compress=True)

    assert RunArchive.objects.filter(run=run).exists()
    archive_path = Path(archive.archive_path)
    assert archive_path.exists()
    with zipfile.ZipFile(archive_path, "r") as zf:
        assert "run_snapshot_" in zf.namelist()[0]
        raw = zf.read(zf.namelist()[0]).decode("utf-8")
        payload = json.loads(raw)
    assert payload["run"]["id"] == str(run.id)
    assert archive.summary["steps"] == 1
    assert archive.summary["events"] == 1
    archived_events = RunEvent.objects.filter(run=run, event_type="run_archived")
    assert archived_events.count() == 1
    assert archived_events.first().payload["archive_id"] == str(archive.id)


@pytest.mark.django_db
def test_compact_events_prunes_old_verbose_events():
    workspace = Workspace.objects.create(name="Compact WS")
    agent = Agent.objects.create(workspace=workspace, name="Compact Agent", system_prompt="Prompt")
    run = AgentRun.objects.create(workspace=workspace, agent=agent, status=AgentRun.Status.COMPLETED, ended_at=timezone.now())
    old = timezone.now() - timedelta(days=60)
    event = RunEvent.objects.create(
        run=run,
        seq=1,
        event_type="token_stream",
        payload={"token": "alpha"},
    )
    RunEvent.objects.filter(id=event.id).update(created_at=old)
    RunEvent.objects.create(run=run, seq=2, event_type="state_changed", payload={"from": "PENDING", "to": "RUNNING"})

    removed = compact_events(str(run.id), retention_days=30, event_types=["token_stream"])
    assert removed == 1
    assert RunEvent.objects.filter(run=run, event_type="token_stream").count() == 0
    assert RunEvent.objects.filter(run=run).count() == 1
