from __future__ import annotations

from datetime import timedelta
from typing import Optional

from django.utils import timezone

from control.models import ApprovalGrant, ApprovalRequest, Operator

_DURATION_UNITS = {
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def _parse_duration(value: Optional[str]) -> timedelta:
    if not value:
        raise ValueError("Duration string required for timed approvals.")
    value = value.strip().lower()
    unit = value[-1]
    if unit not in _DURATION_UNITS:
        raise ValueError("Unsupported duration unit, use m/h/d")
    try:
        quantity = int(value[:-1])
    except ValueError as exc:
        raise ValueError("Duration must be digits followed by unit (e.g., 15m)") from exc
    return timedelta(seconds=quantity * _DURATION_UNITS[unit])


def decide_approval(
    approval_uuid: str,
    decision: str,
    duration: Optional[str] = None,
    persistent: bool = False,
    performed_by: Optional[Operator] = None,
) -> tuple[ApprovalRequest, Optional[ApprovalGrant]]:
    decision = decision.lower()
    request = ApprovalRequest.objects.get(uuid=approval_uuid)
    if request.status != ApprovalRequest.STATUS_PENDING:
        raise ValueError("Approval request has already been resolved.")

    grant_kwargs = {
        "scope": request.tool_name,
        "constraints": request.constraints or {},
        "granted_by": performed_by,
        "approval_request": request,
    }

    expires_at = None
    grant_persistent = persistent
    if decision == "approve_once":
        pass
    elif decision == "approve_for":
        expires_at = timezone.now() + _parse_duration(duration)
    elif decision == "approve_future":
        grant_persistent = True
    elif decision == "deny":
        request.status = ApprovalRequest.STATUS_DENIED
        request.save(update_fields=["status", "updated_at"])
        return request, None
    else:
        raise ValueError(f"Unknown approval decision '{decision}'.")

    grant = ApprovalGrant.objects.create(
        expires_at=expires_at,
        is_persistent=grant_persistent,
        **grant_kwargs,
    )
    request.status = ApprovalRequest.STATUS_APPROVED
    request.save(update_fields=["status", "updated_at"])
    return request, grant
