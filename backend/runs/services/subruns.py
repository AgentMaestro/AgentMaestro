from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone

from core.services.limits import LimitKey, QUOTA_MANAGER
from runs.models import AgentRun, AgentStep, RunEvent, SubrunLink
from runs.services.events import append_event
from runs.services.memory import append_run_note
from runs.services.state import transition_run
from runs.services.steps import append_step

STEP_CREATED_EVENT = "step_created"
SUBRUN_SPAWN_EVENT = "subrun_spawned"
SUBRUN_COMPLETED_EVENT = "subrun_completed"
SUBRUN_CANCELLED_EVENT = "subrun_cancelled"
SUBRUN_CIRCUIT_OPEN_EVENT = "subrun_circuit_open"
HEADLESS_FAILURE_DIAGNOSTICS_EVENT = "headless_run_failure_diagnostics"

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
NETWORK_ERROR_SUBRUN_FAILURE_THRESHOLD = 2
logger = logging.getLogger(__name__)


def _normalize_join_policy(value: str) -> str:
    normalized = str(value or SubrunLink.JoinPolicy.WAIT_ALL).strip().upper()
    mapping = {
        "WAITALL": SubrunLink.JoinPolicy.WAIT_ALL,
        "WAIT_ALL": SubrunLink.JoinPolicy.WAIT_ALL,
        "WAITANY": SubrunLink.JoinPolicy.WAIT_ANY,
        "WAIT_ANY": SubrunLink.JoinPolicy.WAIT_ANY,
        "QUORUM": SubrunLink.JoinPolicy.QUORUM,
        "TIMEOUT": SubrunLink.JoinPolicy.TIMEOUT,
    }
    if normalized not in mapping:
        raise RuntimeError(f"Unsupported join_policy: {value}")
    return mapping[normalized]


def _normalize_failure_policy(value: str) -> str:
    normalized = str(value or SubrunLink.FailurePolicy.IGNORE_FAILURE).strip().upper()
    mapping = {
        "FAILFAST": SubrunLink.FailurePolicy.FAIL_FAST,
        "FAIL_FAST": SubrunLink.FailurePolicy.FAIL_FAST,
        "CANCELSIBLINGS": SubrunLink.FailurePolicy.CANCEL_SIBLINGS,
        "CANCEL_SIBLINGS": SubrunLink.FailurePolicy.CANCEL_SIBLINGS,
        "IGNORE": SubrunLink.FailurePolicy.IGNORE_FAILURE,
        "IGNOREFAILURE": SubrunLink.FailurePolicy.IGNORE_FAILURE,
        "IGNORE_FAILURE": SubrunLink.FailurePolicy.IGNORE_FAILURE,
    }
    if normalized not in mapping:
        raise RuntimeError(f"Unsupported failure_policy: {value}")
    return mapping[normalized]


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


def _count_network_error_subrun_failures(parent_run_id: str) -> int:
    return (
        RunEvent.objects.filter(
            run__parent_run_id=parent_run_id,
            event_type=HEADLESS_FAILURE_DIAGNOSTICS_EVENT,
            payload__classification="network_error",
        )
        .values("run_id")
        .distinct()
        .count()
    )


def _schedule_run_execution(run_id: str) -> None:
    run = AgentRun.objects.only("id", "execution_mode").get(id=run_id)
    if run.execution_mode == AgentRun.ExecutionMode.HEADLESS:
        from runs.tasks import execute_headless_run_task

        execute_headless_run_task.delay(str(run.id))
        return

    from runs.tasks import run_tick as run_tick_task

    run_tick_task.delay(str(run.id))


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
    failure_policy: str = SubrunLink.FailurePolicy.IGNORE_FAILURE,
    group_id: Optional[str] = None,
    schedule_child: bool = True,
    child_execution_mode: Optional[str] = None,
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

    join_policy = _normalize_join_policy(join_policy)
    failure_policy = _normalize_failure_policy(failure_policy)

    correlation_identifier = uuid.uuid4()
    child = AgentRun.objects.create(
        workspace=parent.workspace,
        agent=parent.agent,
        parent_run=parent,
        started_by=parent.started_by,
        status=AgentRun.Status.PENDING,
        channel=parent.channel,
        execution_mode=child_execution_mode or parent.execution_mode,
        trigger_kind=AgentRun.TriggerKind.SYSTEM,
        trigger_ref=str(parent.id),
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
            "execution_mode": child.execution_mode,
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

    if schedule_child:
        transaction.on_commit(lambda: _schedule_run_execution(str(child.id)))

    return child


@transaction.atomic
def complete_subrun(*, child_run_id: str, schedule_parent: bool = True) -> Optional[str]:
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
                if sibling.child_run_id == child.id:
                    continue
                transition_run(run_id=str(sibling.child_run.id), new_status=AgentRun.Status.CANCELED)
                AgentRun.objects.filter(id=sibling.child_run.id).update(
                    error_summary=f"Canceled because sibling subrun {child.id} failed.",
                    updated_at=timezone.now(),
                )
            active_links = []
            completed_count = len(group_links)

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
        if schedule_parent:
            transaction.on_commit(lambda: _schedule_run_execution(str(parent.id)))
        return str(parent.id)

    return None


def run_subrun_flow(
    *,
    parent_run_id: str,
    input_text: str,
    metadata: Optional[Dict[str, Any]] = None,
    join_policy: str = SubrunLink.JoinPolicy.WAIT_ALL,
    quorum: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
    failure_policy: str = SubrunLink.FailurePolicy.IGNORE_FAILURE,
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Spawn a child run and, for headless parents, execute the child inline so the
    current planner/model round can continue with the child result.
    """
    if not str(input_text or "").strip():
        raise RuntimeError("spawn_subrun requires a non-empty input_text.")

    parent = AgentRun.objects.select_related("agent", "workspace", "started_by").get(id=parent_run_id)
    network_error_failures = _count_network_error_subrun_failures(parent_run_id)
    if network_error_failures >= NETWORK_ERROR_SUBRUN_FAILURE_THRESHOLD:
        summary = (
            "Subruns are unavailable for the rest of this run after repeated child failures "
            f"classified as network_error ({network_error_failures} total)."
        )
        recommended_action = (
            "Continue the task in the parent run without spawning more subruns until a new run "
            "or connection is established."
        )
        append_event(
            run_id=parent_run_id,
            event_type=SUBRUN_CIRCUIT_OPEN_EVENT,
            payload={
                "classification": "network_error",
                "failure_count": network_error_failures,
                "threshold": NETWORK_ERROR_SUBRUN_FAILURE_THRESHOLD,
                "summary": summary,
                "recommended_action": recommended_action,
            },
            correlation_id=parent.correlation_id,
        )
        append_run_note(parent, summary)
        return {
            "parent_run_id": str(parent.id),
            "parent_status": parent.status,
            "child_run_id": "",
            "child_status": "SKIPPED",
            "child_execution_mode": AgentRun.ExecutionMode.HEADLESS,
            "join_policy": join_policy,
            "failure_policy": failure_policy,
            "completed_inline": False,
            "resumed_parent": False,
            "child_final_text": "",
            "child_error_summary": summary,
            "child_failed": False,
            "child_failure": {
                "summary": summary,
                "classification": "subrun_circuit_open",
                "code": "",
                "param": "",
                "status": "",
                "request_id": "",
                "retryable": False,
                "recommended_action": recommended_action,
            },
            "child_retryable": False,
            "child_recommended_action": recommended_action,
            "fallback_notice": (
                "Subruns are unavailable for the rest of this run because repeated child runs failed "
                "with network_error. Continue the task in the parent run without asking the user for "
                "permission to proceed."
            ),
            "subrun_circuit_open": True,
            "subrun_circuit_reason": "repeated_network_error",
            "subrun_failure_count": network_error_failures,
        }

    inline_child_execution = AgentRun.ExecutionMode.HEADLESS
    child = spawn_subrun(
        parent_run_id=parent_run_id,
        input_text=input_text,
        metadata=metadata,
        join_policy=join_policy,
        quorum=quorum,
        timeout_seconds=timeout_seconds,
        failure_policy=failure_policy,
        group_id=group_id,
        schedule_child=False,
        child_execution_mode=inline_child_execution,
    )

    from runs.services.headless import execute_headless_run
    from runs.services.headless import get_headless_failure_details

    logger.info(
        "Executing subrun inline for tool flow parent_run=%s child_run=%s parent_execution_mode=%s child_execution_mode=%s",
        parent_run_id,
        child.id,
        parent.execution_mode,
        inline_child_execution,
    )
    execute_headless_run(str(child.id))
    completed_inline = True
    resumed_parent = complete_subrun(child_run_id=str(child.id), schedule_parent=False) == str(parent.id)

    child.refresh_from_db()
    parent.refresh_from_db()
    failure_details = get_headless_failure_details(child) if child.status in FAILURE_RUN_STATUSES else None
    if child.status in FAILURE_RUN_STATUSES and failure_details is None:
        failure_details = {
            "summary": child.error_summary or f"Child run finished with status {child.status}.",
            "classification": "child_run_failed",
            "code": "",
            "param": "",
            "status": "",
            "request_id": "",
            "retryable": False,
            "recommended_action": "Review the child error summary and decide whether to retry or continue with partial results.",
        }
    child_failed = child.status in FAILURE_RUN_STATUSES
    fallback_notice = ""
    if child_failed:
        fallback_notice = (
            "Child subrun failed. Briefly acknowledge the failure, continue the task in the parent run "
            "without asking the user for permission to proceed, and mention the child error summary when it "
            "helps the user understand what failed."
        )
    return {
        "parent_run_id": str(parent.id),
        "parent_status": parent.status,
        "child_run_id": str(child.id),
        "child_status": child.status,
        "child_execution_mode": child.execution_mode,
        "join_policy": join_policy,
        "failure_policy": failure_policy,
        "completed_inline": completed_inline,
        "resumed_parent": resumed_parent,
        "child_final_text": child.final_text,
        "child_error_summary": child.error_summary,
        "child_failed": child_failed,
        "child_failure": failure_details,
        "child_retryable": bool((failure_details or {}).get("retryable")),
        "child_recommended_action": str((failure_details or {}).get("recommended_action") or "").strip(),
        "fallback_notice": fallback_notice,
    }
