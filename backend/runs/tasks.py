# backend/runs/tasks.py
from __future__ import annotations

from agentmaestro.celery import app

from django.conf import settings
from runs.models import AgentRun
from runs.services.checkpoints import archive_completed_runs
from runs.services.recovery import handle_run_failure
from runs.services.ticker import run_tick as run_tick_service


@app.task(bind=True, name="runs.tasks.run_tick", max_retries=5)
def run_tick(self, run_id: str):
    """Celery entry point for advancing a run via the tick service."""
    AgentRun.objects.filter(id=run_id).update(current_task_id=self.request.id)
    try:
        return run_tick_service(run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        instruction = handle_run_failure(run_id=run_id, exc=exc)
        if instruction.retry:
            raise self.retry(exc=exc, countdown=instruction.delay_seconds)
        raise
    finally:
        AgentRun.objects.filter(id=run_id).update(current_task_id="")


@app.task(name="runs.tasks.archive_completed_runs")
def archive_completed_runs_task():
    return archive_completed_runs(
        older_than_days=getattr(settings, "AGENTMAESTRO_ARCHIVE_RETENTION_DAYS", 30),
        limit=getattr(settings, "AGENTMAESTRO_ARCHIVE_LIMIT", None),
        compact=getattr(settings, "AGENTMAESTRO_ARCHIVE_COMPACT_EVENTS", True),
        event_types=getattr(settings, "AGENTMAESTRO_VERBOSE_EVENT_TYPES", None),
    )


@app.task(name="runs.tasks.reconcile_waiting_subruns")
def reconcile_waiting_subruns():
    from runs.services.recovery import reconcile_waiting_parents_and_leases

    return reconcile_waiting_parents_and_leases()
