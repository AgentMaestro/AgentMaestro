from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from memory.retention import (
    DEFAULT_DISTILL_GROUP_LIMIT,
    DEFAULT_RETENTION_BATCH_SIZE,
    run_memory_retention,
)
from memory.scheduled_tasks import (
    DEFAULT_SCHEDULED_TASK_LIMIT,
    claim_due_scheduled_tasks,
    execute_scheduled_task,
    mark_scheduled_task_failure,
    mark_scheduled_task_success,
)
from memory.models import ScheduledTask

logger = logging.getLogger(__name__)


@shared_task(name="memory.tasks.run_due_scheduled_tasks")
def run_due_scheduled_tasks(*, limit: int = DEFAULT_SCHEDULED_TASK_LIMIT) -> dict[str, int]:
    effective_limit = int(limit or getattr(settings, "SCHEDULED_TASK_BATCH_LIMIT", DEFAULT_SCHEDULED_TASK_LIMIT))
    claimed_tasks = claim_due_scheduled_tasks(limit=effective_limit)
    processed = 0
    succeeded = 0
    failed = 0

    for scheduled_task in claimed_tasks:
        processed += 1
        try:
            summary = execute_scheduled_task(scheduled_task)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.exception(
                "Scheduled task execution failed task=%s type=%s agent=%s",
                scheduled_task.id,
                scheduled_task.task_type,
                scheduled_task.agent.slug,
            )
            mark_scheduled_task_failure(scheduled_task, str(exc))
        else:
            succeeded += 1
            mark_scheduled_task_success(scheduled_task, summary)

    return {
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
    }


@shared_task(name="memory.tasks.run_scheduled_task_once")
def run_scheduled_task_once(task_id: str) -> dict[str, str]:
    scheduled_task = ScheduledTask.objects.select_related("agent", "workspace", "owner").get(id=task_id)
    try:
        summary = execute_scheduled_task(scheduled_task)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Manual scheduled task execution failed task=%s type=%s agent=%s",
            scheduled_task.id,
            scheduled_task.task_type,
            scheduled_task.agent.slug,
        )
        mark_scheduled_task_failure(scheduled_task, str(exc))
        raise
    mark_scheduled_task_success(scheduled_task, summary)
    return {"task_id": str(scheduled_task.id), "summary": summary}


@shared_task(name="memory.tasks.run_memory_retention_task")
def run_memory_retention_task() -> dict[str, object]:
    return run_memory_retention(
        dry_run=False,
        retention_days=getattr(settings, "MEMORY_RETENTION_DAYS", 30),
        batch_size=getattr(settings, "MEMORY_RETENTION_BATCH_SIZE", DEFAULT_RETENTION_BATCH_SIZE),
        group_limit=getattr(settings, "MEMORY_EPISODIC_DISTILL_GROUP_LIMIT", DEFAULT_DISTILL_GROUP_LIMIT),
    )
