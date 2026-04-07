from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from logging_utils import get_app_logger

from memory.models import ScheduledTask, ScheduledTaskApproval
from tools.policy import get_effective_tools

logger = get_app_logger(__name__)

INTERNAL_HEADLESS_APPROVAL_TOOL_NAME = "scheduled_headless_run_gate"
HEADLESS_APPROVAL_FINGERPRINT_VERSION = 1
HEADLESS_APPROVAL_REASON_FIRST_RUN = "first_run"
HEADLESS_APPROVAL_REASON_EXPIRED = "approval_expired"
HEADLESS_APPROVAL_REASON_DRIFT = "fingerprint_drift"
HEADLESS_APPROVAL_REASON_INHERITED = "inherited"
HEADLESS_APPROVAL_REASON_PENDING = "approval_required"


@dataclass(frozen=True)
class ScheduledTaskApprovalContext:
    fingerprint: str
    fingerprint_version: int
    fingerprint_payload: dict[str, Any]
    normalized_execution_payload: dict[str, Any]
    tool_signature: list[dict[str, Any]]
    approval: ScheduledTaskApproval | None
    previous_approval: ScheduledTaskApproval | None
    reason: str


def build_scheduled_task_approval_context(scheduled_task: ScheduledTask) -> ScheduledTaskApprovalContext:
    normalized_execution_payload = _normalize_json_payload(dict(scheduled_task.execution_payload or {}))
    tool_signature = _build_tool_signature(scheduled_task)
    fingerprint_payload = _build_fingerprint_payload(
        scheduled_task=scheduled_task,
        normalized_execution_payload=normalized_execution_payload,
        tool_signature=tool_signature,
    )
    fingerprint = sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    now = timezone.now()
    approvals = list(
        ScheduledTaskApproval.objects.filter(
            scheduled_task=scheduled_task,
            fingerprint_version=HEADLESS_APPROVAL_FINGERPRINT_VERSION,
        )
        .order_by("-approved_at", "-created_at")
    )
    matching_active = next(
        (
            approval
            for approval in approvals
            if approval.revoked_at is None
            and approval.expires_at
            and approval.expires_at > now
            and _approval_matches_current_task(
                approval=approval,
                scheduled_task=scheduled_task,
                normalized_execution_payload=normalized_execution_payload,
                tool_signature=tool_signature,
            )
        ),
        None,
    )
    if matching_active is not None:
        return ScheduledTaskApprovalContext(
            fingerprint=fingerprint,
            fingerprint_version=HEADLESS_APPROVAL_FINGERPRINT_VERSION,
            fingerprint_payload=fingerprint_payload,
            normalized_execution_payload=normalized_execution_payload,
            tool_signature=tool_signature,
            approval=matching_active,
            previous_approval=matching_active,
            reason=HEADLESS_APPROVAL_REASON_INHERITED,
        )

    matching_expired = next((approval for approval in approvals if approval.fingerprint == fingerprint), None)
    if matching_expired is not None:
        reason = HEADLESS_APPROVAL_REASON_EXPIRED
        previous_approval = matching_expired
    elif approvals:
        reason = HEADLESS_APPROVAL_REASON_DRIFT
        previous_approval = approvals[0]
    else:
        reason = HEADLESS_APPROVAL_REASON_FIRST_RUN
        previous_approval = None

    return ScheduledTaskApprovalContext(
        fingerprint=fingerprint,
        fingerprint_version=HEADLESS_APPROVAL_FINGERPRINT_VERSION,
        fingerprint_payload=fingerprint_payload,
        normalized_execution_payload=normalized_execution_payload,
        tool_signature=tool_signature,
        approval=None,
        previous_approval=previous_approval,
        reason=reason,
    )


@transaction.atomic
def create_headless_approval_request(*, run, scheduled_task: ScheduledTask, context: ScheduledTaskApprovalContext):
    from tools.services.approvals import request_tool_call_approval

    title = scheduled_task.title or scheduled_task.task_type.replace("_", " ")
    tool_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name=INTERNAL_HEADLESS_APPROVAL_TOOL_NAME,
        args={
            "display_label": _approval_request_label(scheduled_task),
            "display_summary": f"Approve first headless execution for {title}.",
            "scheduled_task_id": str(scheduled_task.id),
            "scheduled_task_title": title,
            "task_type": scheduled_task.task_type,
            "execution_mode": scheduled_task.execution_mode,
            "delivery_target": scheduled_task.delivery_target,
            "approval_reason": context.reason,
            "approval_fingerprint": context.fingerprint,
            "approval_fingerprint_version": context.fingerprint_version,
            "execution_payload": context.normalized_execution_payload,
            "tool_signature": context.tool_signature,
            "previous_approval_id": str(context.previous_approval.id) if context.previous_approval else "",
            "previous_fingerprint": context.previous_approval.fingerprint if context.previous_approval else "",
        },
        requires_approval=True,
    )
    return tool_call


@transaction.atomic
def ensure_headless_task_approval(*, scheduled_task: ScheduledTask, approved_by=None) -> tuple[ScheduledTaskApproval, bool]:
    context = build_scheduled_task_approval_context(scheduled_task)
    approval = context.approval
    now = timezone.now()
    expires_at = now + timedelta(days=_approval_ttl_days())
    if approval is not None:
        approval.approved_by = approved_by or approval.approved_by
        approval.approved_at = now
        approval.expires_at = expires_at
        approval.last_used_at = now
        approval.use_count = int(approval.use_count or 0) + 1
        approval.save(
            update_fields=[
                "approved_by",
                "approved_at",
                "expires_at",
                "last_used_at",
                "use_count",
                "updated_at",
            ]
        )
        logger.info(
            "ensure_headless_task_approval refreshed scheduled_task=%s approval=%s approved_by=%s fingerprint=%s reason=%s",
            scheduled_task.id,
            approval.id,
            getattr(approved_by, "username", None) or getattr(approval.approved_by, "username", None),
            approval.fingerprint,
            context.reason,
        )
        return approval, False

    approval = ScheduledTaskApproval.objects.create(
        scheduled_task=scheduled_task,
        workspace=scheduled_task.workspace,
        agent=scheduled_task.agent,
        source_run=None,
        source_tool_call=None,
        fingerprint=context.fingerprint,
        fingerprint_version=context.fingerprint_version,
        execution_mode=scheduled_task.execution_mode,
        task_type=scheduled_task.task_type,
        delivery_target=scheduled_task.delivery_target,
        normalized_execution_payload=context.normalized_execution_payload,
        tool_signature=context.tool_signature,
        approved_by=approved_by,
        approved_at=now,
        expires_at=expires_at,
        last_used_at=now,
        use_count=1,
    )
    logger.info(
        "ensure_headless_task_approval created scheduled_task=%s approval=%s approved_by=%s fingerprint=%s reason=%s",
        scheduled_task.id,
        approval.id,
        getattr(approved_by, "username", None),
        approval.fingerprint,
        context.reason,
    )
    return approval, True


@transaction.atomic
def activate_headless_approval_from_tool_call(tool_call) -> tuple[ScheduledTaskApproval | None, ScheduledTaskApprovalContext, str | None]:
    scheduled_task = (
        ScheduledTask.objects.select_related("agent", "workspace", "owner")
        .get(id=tool_call.args.get("scheduled_task_id"))
    )
    context = build_scheduled_task_approval_context(scheduled_task)
    requested_fingerprint = str(tool_call.args.get("approval_fingerprint") or "").strip()
    if context.fingerprint != requested_fingerprint:
        return None, context, HEADLESS_APPROVAL_REASON_DRIFT

    approval = ScheduledTaskApproval.objects.create(
        scheduled_task=scheduled_task,
        workspace=scheduled_task.workspace,
        agent=scheduled_task.agent,
        source_run=tool_call.run,
        source_tool_call=tool_call,
        fingerprint=context.fingerprint,
        fingerprint_version=context.fingerprint_version,
        execution_mode=scheduled_task.execution_mode,
        task_type=scheduled_task.task_type,
        delivery_target=scheduled_task.delivery_target,
        normalized_execution_payload=context.normalized_execution_payload,
        tool_signature=context.tool_signature,
        approved_by=tool_call.approved_by,
        approved_at=tool_call.approved_at or timezone.now(),
        expires_at=timezone.now() + timedelta(days=_approval_ttl_days()),
        last_used_at=timezone.now(),
        use_count=1,
    )
    return approval, context, None


@transaction.atomic
def record_inherited_headless_approval_use(*, approval: ScheduledTaskApproval, run) -> ScheduledTaskApproval:
    approval.last_used_at = timezone.now()
    approval.use_count = int(approval.use_count or 0) + 1
    approval.save(update_fields=["last_used_at", "use_count", "updated_at"])
    return approval


@transaction.atomic
def revoke_scheduled_task_approvals(*, scheduled_task: ScheduledTask) -> int:
    now = timezone.now()
    return ScheduledTaskApproval.objects.filter(
        scheduled_task=scheduled_task,
        revoked_at__isnull=True,
    ).update(revoked_at=now, updated_at=now)


def _approval_ttl_days() -> int:
    return max(int(getattr(settings, "SCHEDULED_HEADLESS_APPROVAL_TTL_DAYS", 30)), 1)


def _build_tool_signature(scheduled_task: ScheduledTask) -> list[dict[str, Any]]:
    entries = get_effective_tools(scheduled_task.agent, scheduled_task.owner)
    signature = [
        {
            "tool_name": entry.tool.name,
            "risk": entry.risk,
            "requires_approval": bool(entry.requires_approval),
        }
        for entry in entries
    ]
    return sorted(signature, key=lambda item: item["tool_name"])


def _build_fingerprint_payload(
    *,
    scheduled_task: ScheduledTask,
    normalized_execution_payload: dict[str, Any],
    tool_signature: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": HEADLESS_APPROVAL_FINGERPRINT_VERSION,
        "scheduled_task_id": str(scheduled_task.id),
        "agent_id": str(scheduled_task.agent_id),
        "execution_mode": scheduled_task.execution_mode,
        "task_type": scheduled_task.task_type,
        "delivery_target": scheduled_task.delivery_target,
        "timezone": scheduled_task.timezone,
        "local_time": scheduled_task.local_time.isoformat(timespec="minutes"),
        "execution_payload": normalized_execution_payload,
        "tool_signature": tool_signature,
    }


def _approval_matches_current_task(
    *,
    approval: ScheduledTaskApproval,
    scheduled_task: ScheduledTask,
    normalized_execution_payload: dict[str, Any],
    tool_signature: list[dict[str, Any]],
) -> bool:
    current_payload = _build_fingerprint_payload(
        scheduled_task=scheduled_task,
        normalized_execution_payload=normalized_execution_payload,
        tool_signature=tool_signature,
    )
    current_fingerprint = _fingerprint_for_payload(current_payload)
    if approval.fingerprint == current_fingerprint:
        return True

    approved_signature = _normalize_tool_signature(list(approval.tool_signature or []))
    current_signature = _normalize_tool_signature(tool_signature)
    if not approved_signature:
        return approval.fingerprint == current_fingerprint
    if not _is_tool_signature_superset(current_signature, approved_signature):
        return False

    compatible_payload = _build_fingerprint_payload(
        scheduled_task=scheduled_task,
        normalized_execution_payload=normalized_execution_payload,
        tool_signature=list(approval.tool_signature or []),
    )
    compatible_fingerprint = _fingerprint_for_payload(compatible_payload)
    return approval.fingerprint == compatible_fingerprint


def _fingerprint_for_payload(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _normalize_tool_signature(signature: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for entry in signature:
        tool_name = str(entry.get("tool_name") or "").strip()
        if not tool_name:
            continue
        normalized[tool_name] = {
            "tool_name": tool_name,
            "risk": str(entry.get("risk") or "").strip(),
            "requires_approval": bool(entry.get("requires_approval")),
        }
    return normalized


def _is_tool_signature_superset(current: dict[str, dict[str, Any]], approved: dict[str, dict[str, Any]]) -> bool:
    for tool_name, approved_entry in approved.items():
        current_entry = current.get(tool_name)
        if current_entry is None:
            return False
        if current_entry.get("risk") != approved_entry.get("risk"):
            return False
        if bool(current_entry.get("requires_approval")) != bool(approved_entry.get("requires_approval")):
            return False
    return True


def _normalize_json_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_json_payload(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_json_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json_payload(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _approval_request_label(scheduled_task: ScheduledTask) -> str:
    return "Approve first execution of scheduled task"
