from __future__ import annotations

from celery import shared_task
from django.conf import settings
from logging_utils import get_app_logger

from memory.models import ScheduledTask
from memory.retention import (
    DEFAULT_DISTILL_GROUP_LIMIT,
    DEFAULT_RETENTION_BATCH_SIZE,
    run_memory_retention,
)
from memory.scheduled_tasks import (
    DEFAULT_SCHEDULED_TASK_LIMIT,
    cleanup_scheduled_task_active_runs,
    claim_due_scheduled_tasks,
    mark_scheduled_task_failure,
)
from runs.models import AgentRun
from runs.services.headless import launch_scheduled_task_run

logger = get_app_logger(__name__)


@shared_task(name="memory.tasks.run_due_scheduled_tasks")
def run_due_scheduled_tasks(*, limit: int = DEFAULT_SCHEDULED_TASK_LIMIT) -> dict[str, int]:
    effective_limit = int(limit or getattr(settings, "SCHEDULED_TASK_BATCH_LIMIT", DEFAULT_SCHEDULED_TASK_LIMIT))
    cleanup_counts = cleanup_scheduled_task_active_runs(limit=effective_limit)
    claimed_tasks = claim_due_scheduled_tasks(limit=effective_limit)
    processed = 0
    succeeded = 0
    failed = 0
    launched = 0
    awaiting_approval = 0

    for scheduled_task in claimed_tasks:
        processed += 1
        try:
            _scheduled_task, run, did_launch = launch_scheduled_task_run(str(scheduled_task.id))
            if did_launch and run.status == AgentRun.Status.WAITING_FOR_APPROVAL:
                awaiting_approval += 1
                continue
            if did_launch:
                from runs.tasks import execute_headless_run_task

                execute_headless_run_task.delay(str(run.id))
                launched += 1
                succeeded += 1
            else:
                logger.info(
                    "Scheduled task %s already has active headless run %s",
                    scheduled_task.id,
                    run.id,
                )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.exception(
                "Scheduled task execution failed task=%s type=%s agent=%s",
                scheduled_task.id,
                scheduled_task.task_type,
                scheduled_task.agent.slug,
            )
            mark_scheduled_task_failure(scheduled_task, str(exc))

    return {
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "launched": launched,
        "awaiting_approval": awaiting_approval,
        "terminal_cleared": cleanup_counts.get("terminal_cleared", 0),
        "stale_cleared": cleanup_counts.get("stale_cleared", 0),
    }


@shared_task(name="memory.tasks.run_scheduled_task_once")
def run_scheduled_task_once(task_id: str) -> dict[str, str]:
    scheduled_task = ScheduledTask.objects.select_related("agent", "workspace", "owner").get(id=task_id)
    _scheduled_task, run, _did_launch = launch_scheduled_task_run(str(scheduled_task.id))
    if run.status == AgentRun.Status.WAITING_FOR_APPROVAL:
        return {"task_id": str(scheduled_task.id), "run_id": str(run.id), "status": "awaiting_approval"}
    from runs.tasks import execute_headless_run_task

    execute_headless_run_task.delay(str(run.id))
    return {"task_id": str(scheduled_task.id), "run_id": str(run.id), "status": "launched"}


@shared_task(name="memory.tasks.run_memory_retention_task")
def run_memory_retention_task() -> dict[str, object]:
    return run_memory_retention(
        dry_run=False,
        retention_days=getattr(settings, "MEMORY_RETENTION_DAYS", 30),
        batch_size=getattr(settings, "MEMORY_RETENTION_BATCH_SIZE", DEFAULT_RETENTION_BATCH_SIZE),
        group_limit=getattr(settings, "MEMORY_EPISODIC_DISTILL_GROUP_LIMIT", DEFAULT_DISTILL_GROUP_LIMIT),
    )
