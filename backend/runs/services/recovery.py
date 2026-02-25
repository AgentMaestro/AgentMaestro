from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from agentmaestro.celery import app
from django.db import DatabaseError, transaction
from django.utils import timezone

from runs.models import AgentRun
from runs.services.events import append_event
from runs.services.state import transition_run
from runs.services.subruns import FINAL_RUN_STATUSES
from runs.services.toolrunner import signal_toolrunner_cancel

LOCK_LEASE_SECONDS = int(os.getenv("AGENTMAESTRO_LOCK_LEASE_SECONDS", "20"))
RETRY_BACKOFF_SECONDS = int(os.getenv("AGENTMAESTRO_RETRY_BACKOFF_SECONDS", "5"))
WORKER_ID = os.getenv("AGENTMAESTRO_TICKER_ID") or f"{socket.gethostname()}:{os.getpid()}"

logger = logging.getLogger(__name__)

EXPECTED_STEP_INDEX = {
    AgentRun.Status.PENDING: 0,
    AgentRun.Status.RUNNING: 1,
}


@dataclass(frozen=True)
class RetryInstruction:
    retry: bool
    delay_seconds: int


class RunRecoveryError(RuntimeError):
    """Base class for run recovery issues."""


class TransientRunError(RunRecoveryError):
    """Temporary failure; the run can be retried."""


class PermanentRunError(RunRecoveryError):
    """Non-recoverable failure; transition the run to FAILED."""


class RunTickLocked(TransientRunError):
    """Raised when another worker owns the lease for the run."""


def _is_lock_expired(run: AgentRun, *, now: Optional[datetime] = None) -> bool:
    now = now or timezone.now()
    if run.locked_at is None:
        return False
    if run.lock_expires_at is not None:
        return run.lock_expires_at <= now
    age = now - run.locked_at
    return age.total_seconds() >= LOCK_LEASE_SECONDS


def _clear_lock(run: AgentRun) -> None:
    run.locked_by = ""
    run.locked_at = None
    run.lock_expires_at = None
    run.save(update_fields=["locked_by", "locked_at", "lock_expires_at", "updated_at"])


def _acquire_lock(run: AgentRun, *, now: Optional[datetime] = None) -> None:
    now = now or timezone.now()
    run.locked_by = WORKER_ID
    run.locked_at = now
    run.lock_expires_at = now + timedelta(seconds=LOCK_LEASE_SECONDS)
    run.save(update_fields=["locked_by", "locked_at", "lock_expires_at", "updated_at"])


def release_stale_lock(run: AgentRun, *, now: Optional[datetime] = None) -> bool:
    """Return True if stale lock metadata was cleared."""
    if _is_lock_expired(run, now=now):
        _clear_lock(run)
        return True
    return False


def claim_run(run_id: str) -> AgentRun:
    """Lock the run row and refresh the lease, releasing stale holders."""
    try:
        run = AgentRun.objects.select_for_update(nowait=True).get(id=run_id)
    except DatabaseError as exc:
        raise RunTickLocked(f"Run {run_id} is locked") from exc

    now = timezone.now()
    release_stale_lock(run, now=now)
    if run.locked_by and run.locked_by != WORKER_ID and not _is_lock_expired(run, now=now):
        raise RunTickLocked(f"Run {run_id} is locked by {run.locked_by}")

    _acquire_lock(run, now=now)
    return run


def release_run_lock(run: AgentRun) -> None:
    """Release the lease if it is still held by this worker."""
    if run.locked_by != WORKER_ID:
        return
    _clear_lock(run)


def is_cursor_at_expected(run: AgentRun) -> bool:
    """Ensure the run cursor matches the expectations for the current status."""
    expected = EXPECTED_STEP_INDEX.get(run.status)
    if expected is None:
        return False
    return run.current_step_index == expected


def plan_retry(exc: Exception) -> RetryInstruction:
    """Return retry guidance for the given exception."""
    if isinstance(exc, TransientRunError):
        return RetryInstruction(retry=True, delay_seconds=RETRY_BACKOFF_SECONDS)
    return RetryInstruction(retry=False, delay_seconds=0)


def handle_run_failure(run_id: str, exc: Exception) -> RetryInstruction:
    """Mark the run failed for permanent errors and return retry guidance."""
    instruction = plan_retry(exc)
    if instruction.retry:
        return instruction

    try:
        transition_run(run_id=run_id, new_status=AgentRun.Status.FAILED)
    except AgentRun.DoesNotExist:
        logger.warning("Attempted to mark missing run %s as FAILED", run_id)
        return instruction
    AgentRun.objects.filter(id=run_id).update(
        error_summary=str(exc),
        updated_at=timezone.now(),
    )
    return instruction


@transaction.atomic
def cancel_run(run_id: str, *, reason: str | None = None) -> AgentRun:
    run = AgentRun.objects.select_for_update().get(id=run_id)
    task_id = run.current_task_id
    run.cancel_requested = True
    run.current_task_id = ""
    run.save(update_fields=["cancel_requested", "current_task_id", "updated_at"])
    cancelled = transition_run(run_id=run_id, new_status=AgentRun.Status.CANCELED)
    if reason:
        AgentRun.objects.filter(id=run_id).update(error_summary=reason)
    append_event(
        run_id=run_id,
        event_type="run_cancelled",
        payload={"reason": reason or ""},
        correlation_id=run.correlation_id,
    )
    _revoke_celery_task(task_id)
    signal_toolrunner_cancel(run_id)

    from runs.services.subruns import cancel_subrun, notify_parent_child_cancelled

    pending_children = (
        AgentRun.objects.filter(parent_run_id=run_id)
        .exclude(status__in=FINAL_RUN_STATUSES)
        .values_list("id", flat=True)
    )
    for child_id in pending_children:
        cancel_subrun(child_run_id=str(child_id), reason=reason, notify_parent=False)

    if run.parent_run_id:
        notify_parent_child_cancelled(child_run_id=run_id, reason=reason)

    return cancelled


def _revoke_celery_task(task_id: Optional[str]) -> None:
    if not task_id:
        return
    try:
        app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to revoke Celery task %s: %s", task_id, exc)


def reconcile_waiting_parents_and_leases() -> Dict[str, int]:
    from runs.tasks import run_tick as run_tick_task

    resumed = 0
    waiting_parents = AgentRun.objects.filter(status=AgentRun.Status.WAITING_FOR_SUBRUN)
    for parent in waiting_parents:
        has_pending = AgentRun.objects.filter(parent_run_id=parent.id).exclude(
            status__in=FINAL_RUN_STATUSES
        ).exists()
        if not has_pending:
            try:
                transition_run(run_id=str(parent.id), new_status=AgentRun.Status.RUNNING)
            except ValueError:
                continue
            run_tick_task.delay(str(parent.id))
            resumed += 1

    scheduled = 0
    stale_candidates = AgentRun.objects.filter(locked_at__isnull=False)
    for run in stale_candidates:
        if release_stale_lock(run):
            run_tick_task.delay(str(run.id))
            scheduled += 1

    return {"resumed_waiting_parents": resumed, "stale_leases_reclaimed": scheduled}


@transaction.atomic
def pause_run(run_id: str) -> AgentRun:
    return transition_run(run_id=run_id, new_status=AgentRun.Status.PAUSED)


@transaction.atomic
def resume_run(run_id: str) -> AgentRun:
    return transition_run(run_id=run_id, new_status=AgentRun.Status.RUNNING)
