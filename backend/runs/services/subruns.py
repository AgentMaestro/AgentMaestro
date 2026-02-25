from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone

from runs.models import AgentRun, AgentStep, SubrunLink
from runs.services.events import append_event
from runs.services.state import transition_run
from runs.services.steps import append_step
from core.services.limits import LimitKey, QUOTA_MANAGER

STEP_CREATED_EVENT = "step_created"
SUBRUN_SPAWN_EVENT = "subrun_spawned"
SUBRUN_COMPLETED_EVENT = "subrun_completed"
SUBRUN_CANCELLED_EVENT = "subrun_cancelled"

FINAL_RUN_STATUSES = {
    AgentRun.Status.COMPLETED,
    AgentRun.Status.FAILED,
    AgentRun.Status.CANCELED,
}
FAILURE_RUN_STATUSES = {
    AgentRun.Status.FAILED,
    AgentRun.Status.CANCELED,
}
MAX_PENDING_SUBRUNS_PER_PARENT = 4


def _extract_link_metadata(link: Optional[SubrunLink]) -> Dict[str, Any]:
    if not link:
        return {}
    return {
        "group_id": str(link.group_id),
        "join_policy": link.join_policy,
        "quorum": link.quorum,
        "timeout_seconds": link.timeout_seconds,
        "failure_policy": link.failure_policy,
    }


def _build_subrun_event_payload(
    *,
    child: AgentRun,
    link: Optional[SubrunLink] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "child_run_id": str(child.id),
        "child_status": child.status,
        "ended_at": child.ended_at.isoformat() if child.ended_at else None,
    }
    if child.correlation_id:
        payload["correlation_id"] = str(child.correlation_id)
    payload.update(_extract_link_metadata(link or getattr(child, "subrun_link", None)))
    if reason:
        payload["reason"] = reason
    return payload


def _emit_subrun_event(
    *,
    child: AgentRun,
    event_type: str,
    link: Optional[SubrunLink] = None,
    reason: Optional[str] = None,
) -> None:
    parent_id = child.parent_run_id
    if not parent_id:
        return
    append_event(
        run_id=str(parent_id),
        event_type=event_type,
        payload=_build_subrun_event_payload(child=child, link=link, reason=reason),
        correlation_id=child.correlation_id,
    )


def _build_step_event_payload(step: AgentStep) -> Dict[str, Any]:
    return {
        "step_id": str(step.id),
        "step_index": step.step_index,
        "kind": step.kind,
        "payload": step.payload,
        "correlation_id": str(step.correlation_id),
    }


def _schedule_run_tick(run_id: str) -> None:
    from runs.tasks import run_tick as run_tick_task

    run_tick_task.delay(str(run_id))


@transaction.atomic
def cancel_subrun(
    *,
    child_run_id: str,
    reason: Optional[str] = None,
    notify_parent: bool = True,
) -> None:
    """
    Cancel a tracked child run, emit subrun_cancelled, and optionally advance the parent.
    """
    child = AgentRun.objects.select_for_update().get(id=child_run_id)
    link = SubrunLink.objects.filter(child_run_id=child_run_id).first()
    if child.status == AgentRun.Status.CANCELED:
        if notify_parent and child.parent_run_id:
            _emit_subrun_event(child=child, event_type=SUBRUN_CANCELLED_EVENT, link=link, reason=reason)
            complete_subrun(child_run_id=child_run_id)
        return

    child.cancel_requested = True
    child.save(update_fields=["cancel_requested", "updated_at"])
    transition_run(run_id=child_run_id, new_status=AgentRun.Status.CANCELED)
    if reason:
        AgentRun.objects.filter(id=child_run_id).update(error_summary=reason, updated_at=timezone.now())

    _emit_subrun_event(child=child, event_type=SUBRUN_CANCELLED_EVENT, link=link, reason=reason)

    if notify_parent:
        complete_subrun(child_run_id=child_run_id)


@transaction.atomic
def notify_parent_child_cancelled(*, child_run_id: str, reason: Optional[str] = None) -> None:
    """
    After a run is cancelled, inform its parent about the cancellation and let the policy run.
    """
    child = AgentRun.objects.get(id=child_run_id)
    link = SubrunLink.objects.filter(child_run_id=child_run_id).first()
    _emit_subrun_event(child=child, event_type=SUBRUN_CANCELLED_EVENT, link=link, reason=reason)
    complete_subrun(child_run_id=child_run_id)


@transaction.atomic
def spawn_subrun(
    *,
    parent_run_id: str,
    input_text: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    join_policy: str = SubrunLink.JoinPolicy.WAIT_ALL,
    quorum: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
    failure_policy: str = SubrunLink.FailurePolicy.FAIL_FAST,
    group_id: Optional[str] = None,
) -> AgentRun:
    """
    Spawn a child run with a join policy; parents wait or resume according to the SubrunLink.
    """
    parent = AgentRun.objects.select_for_update().get(id=parent_run_id)
    pending_children = (
        AgentRun.objects.filter(parent_run=parent)
        .exclude(status__in=FINAL_RUN_STATUSES)
        .count()
    )
    if pending_children >= MAX_PENDING_SUBRUNS_PER_PARENT:
        raise RuntimeError("Parent has too many pending subruns in flight.")
    QUOTA_MANAGER.record_request(str(parent.workspace_id), LimitKey.SPAWN_SUBRUN)
    if parent.status not in {
        AgentRun.Status.PENDING,
        AgentRun.Status.RUNNING,
        AgentRun.Status.WAITING_FOR_SUBRUN,
    }:
        raise RuntimeError(f"Cannot spawn a subrun from run {parent.status}")

    correlation_identifier = uuid.uuid4()
    child = AgentRun.objects.create(
        workspace=parent.workspace,
        agent=parent.agent,
        parent_run=parent,
        started_by=parent.started_by,
        status=AgentRun.Status.PENDING,
        channel=parent.channel,
        input_text=input_text or "",
        max_steps=parent.max_steps,
        max_tool_calls=parent.max_tool_calls,
        correlation_id=correlation_identifier,
    )

    QUOTA_MANAGER.acquire_run_slots(str(parent.workspace_id), str(child.id), include_parent=False)

    group_uuid = uuid.UUID(str(group_id)) if group_id else uuid.uuid4()
    SubrunLink.objects.create(
        parent_run=parent,
        child_run=child,
        group_id=group_uuid,
        join_policy=join_policy,
        quorum=quorum,
        timeout_seconds=timeout_seconds,
        failure_policy=failure_policy,
        metadata=metadata or {},
    )

    step_payload = {
        "child_run_id": str(child.id),
        "subrun_group_id": str(group_uuid),
        "join_policy": join_policy,
        "failure_policy": failure_policy,
    }
    if quorum is not None:
        step_payload["quorum"] = quorum
    if timeout_seconds is not None:
        step_payload["timeout_seconds"] = timeout_seconds
    if metadata:
        step_payload["metadata"] = metadata

    step = append_step(
        run_id=parent_run_id,
        kind=AgentStep.Kind.SUBRUN_SPAWN,
        payload=step_payload,
        correlation_id=correlation_identifier,
    )

    append_event(
        run_id=parent_run_id,
        event_type=STEP_CREATED_EVENT,
        payload=_build_step_event_payload(step),
        correlation_id=correlation_identifier,
    )

    append_event(
        run_id=parent_run_id,
        event_type=SUBRUN_SPAWN_EVENT,
        payload={
            "child_run_id": str(child.id),
            "input_text": child.input_text,
            "status": child.status,
            "group_id": str(group_uuid),
            "join_policy": join_policy,
            "quorum": quorum,
            "timeout_seconds": timeout_seconds,
            "failure_policy": failure_policy,
            "correlation_id": str(correlation_identifier),
        },
        correlation_id=correlation_identifier,
    )

    if parent.status != AgentRun.Status.WAITING_FOR_SUBRUN:
        transition_run(run_id=parent_run_id, new_status=AgentRun.Status.WAITING_FOR_SUBRUN)

    transaction.on_commit(lambda: _schedule_run_tick(str(child.id)))

    return child


@transaction.atomic
def complete_subrun(*, child_run_id: str) -> Optional[str]:
    """
    Resume the parent once its join condition is satisfied. Failure policies may short-circuit.
    """
    child = AgentRun.objects.select_for_update().get(id=child_run_id)
    parent = child.parent_run
    if not parent or parent.status != AgentRun.Status.WAITING_FOR_SUBRUN:
        return None

    try:
        link = SubrunLink.objects.select_for_update().select_related("child_run").get(child_run=child)
    except SubrunLink.DoesNotExist:
        return None

    group_links = list(
        SubrunLink.objects.select_for_update()
        .select_related("child_run")
        .filter(parent_run=parent, group_id=link.group_id)
    )

    if not group_links:
        return None

    active_links = [l for l in group_links if l.child_run.status not in FINAL_RUN_STATUSES]
    completed_count = len([l for l in group_links if l.child_run.status in FINAL_RUN_STATUSES])

    timeout_expired = False
    if link.timeout_seconds:
        earliest = min(l.created_at for l in group_links)
        elapsed = timezone.now() - earliest
        timeout_expired = elapsed.total_seconds() >= link.timeout_seconds

    reason = child.error_summary or None
    event_type = (
        SUBRUN_CANCELLED_EVENT
        if child.status == AgentRun.Status.CANCELED
        else SUBRUN_COMPLETED_EVENT
    )
    _emit_subrun_event(child=child, event_type=event_type, link=link, reason=reason)

    if child.status in FAILURE_RUN_STATUSES:
        if link.failure_policy == SubrunLink.FailurePolicy.FAIL_FAST:
            transition_run(run_id=str(parent.id), new_status=AgentRun.Status.FAILED)
            return None
        if link.failure_policy == SubrunLink.FailurePolicy.CANCEL_SIBLINGS:
            for sibling in active_links:
                transition_run(run_id=str(sibling.child_run.id), new_status=AgentRun.Status.CANCELED)
            transition_run(run_id=str(parent.id), new_status=AgentRun.Status.FAILED)
            return None

    should_resume = False
    if link.join_policy == SubrunLink.JoinPolicy.WAIT_ANY:
        should_resume = child.status in FINAL_RUN_STATUSES
    elif link.join_policy == SubrunLink.JoinPolicy.WAIT_ALL:
        should_resume = not active_links
    elif link.join_policy == SubrunLink.JoinPolicy.QUORUM:
        required = max(1, link.quorum or len(group_links))
        should_resume = completed_count >= required
    elif link.join_policy == SubrunLink.JoinPolicy.TIMEOUT:
        should_resume = not active_links or timeout_expired
    else:
        should_resume = not active_links

    if should_resume:
        transition_run(run_id=str(parent.id), new_status=AgentRun.Status.RUNNING)
        transaction.on_commit(lambda: _schedule_run_tick(str(parent.id)))
        return str(parent.id)

    return None
