# backend/runs/services/events.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple
from uuid import UUID, uuid4

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from logging_utils import get_app_logger
from logging_utils import scrub_sensitive_value
from runs.models import AgentRun, RunEvent
from runs.services.event_contracts import (
    make_approvals_push,
    make_run_push,
    make_workspace_push,
)

logger = get_app_logger(__name__)

TOOL_CALL_COMPLETED_EVENT = "tool_call_completed"
TOOL_CALL_DENIED_EVENT = "tool_call_denied"


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _normalize_tool_completed_payload(
    payload: Dict[str, Any], correlation_id: UUID
) -> Dict[str, Any]:
    """
    Canonical payload shape for tool_call_completed events sent to AgentChatConsumer.push().
    """
    raw = _as_dict(payload)

    tool_call_id = str(raw.get("tool_call_id") or raw.get("id") or raw.get("tool_id") or "").strip()

    status = str(raw.get("status") or "completed").strip()
    stdout = raw.get("stdout")
    stderr = raw.get("stderr")
    result = raw.get("result")

    if stdout is None:
        stdout = ""
    if stderr is None:
        stderr = ""

    normalized: Dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "status": status,
        "stdout": stdout if isinstance(stdout, str) else str(stdout),
        "stderr": stderr if isinstance(stderr, str) else str(stderr),
        "result": result,
        "correlation_id": str(correlation_id),
    }

    for optional_key in (
        "tool_name",
        "provider_call_id",
        "call_id",
        "duration_ms",
        "started_at",
        "completed_at",
    ):
        if optional_key in raw:
            normalized[optional_key] = raw[optional_key]

    if not tool_call_id:
        logger.error(
            "normalize tool_call_completed missing tool_call_id payload_keys=%s payload=%r",
            sorted(raw.keys()),
            raw,
        )

    return normalized


def _normalize_tool_denied_payload(payload: Dict[str, Any], correlation_id: UUID) -> Dict[str, Any]:
    raw = _as_dict(payload)

    tool_call_id = str(raw.get("tool_call_id") or raw.get("id") or raw.get("tool_id") or "").strip()

    normalized: Dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "error": raw.get("error") or raw.get("message") or "Tool call denied.",
        "correlation_id": str(correlation_id),
    }

    if "tool_name" in raw:
        normalized["tool_name"] = raw["tool_name"]

    if not tool_call_id:
        logger.error(
            "normalize tool_call_denied missing tool_call_id payload_keys=%s payload=%r",
            sorted(raw.keys()),
            raw,
        )

    return normalized


def _normalize_run_event_data(
    *,
    event_type: str,
    payload: Dict[str, Any],
    correlation_id: UUID,
) -> Dict[str, Any]:
    raw = _as_dict(payload)

    if event_type == TOOL_CALL_COMPLETED_EVENT:
        return _normalize_tool_completed_payload(raw, correlation_id)

    if event_type == TOOL_CALL_DENIED_EVENT:
        return _normalize_tool_denied_payload(raw, correlation_id)

    return {**raw, "correlation_id": str(correlation_id)}


def _layer_diag(channel_layer: object) -> Dict[str, Any]:
    if channel_layer is None:
        return {"layer": None}
    info: Dict[str, Any] = {"layer_class": channel_layer.__class__.__name__}
    for attr in ("hosts", "_hosts"):
        if hasattr(channel_layer, attr):
            try:
                info["hosts"] = getattr(channel_layer, attr)
            except Exception as exc:
                info["hosts_error"] = repr(exc)
            break
    for attr in ("prefix", "_prefix"):
        if hasattr(channel_layer, attr):
            try:
                info["prefix"] = getattr(channel_layer, attr)
            except Exception as exc:
                info["prefix_error"] = repr(exc)
            break
    return info


def _run_group(run_id: str) -> str:
    return f"run.{run_id}"


def _workspace_group(workspace_id: str) -> str:
    return f"ws.{workspace_id}"


def _approvals_group(workspace_id: str) -> str:
    return f"approvals.{workspace_id}"


@transaction.atomic
def append_event(
    *,
    run_id: str,
    event_type: str,
    payload: Dict[str, Any],
    broadcast_to_run: bool = True,
    broadcast_to_workspace: bool = False,
    workspace_summary_event: str = "run_event",
    correlation_id: Optional[UUID] = None,
) -> Tuple[RunEvent, int]:
    """
    Append a RunEvent with a DB-safe, per-run monotonically increasing seq.

    Guarantees:
      - seq is monotonic per run (1, 2, 3, ...)
      - safe under concurrency by locking the AgentRun row during sequence allocation

    Broadcasting (hardened):
      - Broadcast happens ONLY AFTER the DB transaction successfully commits,
        using transaction.on_commit(...). This prevents "ghost events" on rollback.

    Returns:
      (RunEvent instance, seq)
    """
    logger.debug(
        "append_event start run=%s event=%s broadcast_run=%s broadcast_workspace=%s seq=%s correlation=%s payload_keys=%s",
        run_id,
        event_type,
        broadcast_to_run,
        broadcast_to_workspace,
        None,
        correlation_id,
        sorted((payload or {}).keys()),
    )

    # Lock the run row so concurrent tickers cannot allocate the same seq.
    run = AgentRun.objects.select_for_update().select_related("workspace").get(id=run_id)

    # Compute next seq from existing events (safe under run row lock).
    agg = RunEvent.objects.filter(run_id=run_id).aggregate(m=Max("seq"))
    next_seq = int((agg["m"] or 0) + 1)

    resolved_correlation = correlation_id or uuid4()
    evt = RunEvent.objects.create(
        run_id=run_id,
        seq=next_seq,
        event_type=event_type,
        payload=payload or {},
        correlation_id=resolved_correlation,
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    logger.debug(
        "append_event created RunEvent id=%s run=%s seq=%s type=%s",
        evt.id,
        run_id,
        next_seq,
        event_type,
    )

    # Broadcast only after commit.
    def _after_commit():
        logger.debug(
            "append_event _after_commit start run=%s seq=%s",
            run_id,
            next_seq,
        )

        if broadcast_to_run:
            channel_layer = get_channel_layer()
            logger.debug(
                "append_event broadcasting_to_run event run=%s seq=%s channel_layer=%s group=%s",
                run_id,
                next_seq,
                channel_layer.__class__.__name__ if channel_layer else None,
                _run_group(run_id),
            )

            if not channel_layer:
                logger.warning(
                    "append_event skipping broadcast_run_event because no channel_layer run=%s",
                    run_id,
                )

            normalized_run_data = _normalize_run_event_data(
                event_type=event_type,
                payload=payload or {},
                correlation_id=resolved_correlation,
            )

            logger.info(
                "append_event normalized run event run=%s seq=%s event=%s tool_call_id=%s keys=%s",
                run_id,
                next_seq,
                event_type,
                normalized_run_data.get("tool_call_id"),
                sorted(normalized_run_data.keys()),
            )

            broadcast_run_event(
                run_id=str(run.id),
                workspace_id=str(run.workspace_id),
                seq=next_seq,
                event=event_type,
                data=normalized_run_data,
            )

            logger.debug(
                "append_event broadcast_run_event completed run=%s seq=%s",
                run_id,
                next_seq,
            )

        if broadcast_to_workspace:
            logger.debug(
                "append_event broadcasting workspace event workspace=%s seq=%s event=%s",
                run.workspace_id,
                next_seq,
                workspace_summary_event,
            )
            broadcast_workspace_event(
                workspace_id=str(run.workspace_id),
                event=workspace_summary_event,
                data={
                    "run_id": str(run.id),
                    "seq": next_seq,
                    "event_type": event_type,
                    "payload": payload or {},
                    "correlation_id": str(resolved_correlation),
                },
            )
            logger.debug(
                "append_event broadcast_workspace_event completed workspace=%s seq=%s",
                run.workspace_id,
                next_seq,
            )
        logger.debug(
            "append_event _after_commit end run=%s seq=%s",
            run_id,
            next_seq,
        )

    logger.debug("append_event registering on_commit for run=%s seq=%s", run_id, next_seq)

    try:
        transaction.on_commit(_after_commit)
        logger.debug("append_event on_commit registered run=%s seq=%s", run_id, next_seq)
    except Exception:
        logger.exception(
            "append_event failed to register on_commit for run=%s seq=%s", run_id, next_seq
        )

    return evt, next_seq


async def broadcast_run_event_async(
    *,
    run_id: str,
    event: str,
    data: Dict[str, Any],
    seq: Optional[int] = None,
    workspace_id: Optional[str] = None,
) -> None:
    """
    Async version of the run-scoped broadcast helper.
    """
    logger.debug(
        "broadcast_run_event_async entered run=%s seq=%s event=%s workspace=%s data_keys=%s",
        run_id,
        seq,
        event,
        workspace_id,
        sorted((data or {}).keys()),
    )
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning("broadcast_run_event no channel_layer configured run=%s", run_id)
        return

    if not isinstance(data, dict):
        logger.error(
            "broadcast_run_event_async invalid data type run=%s seq=%s event=%s data_type=%s data=%r",
            run_id,
            seq,
            event,
            type(data).__name__,
            data,
        )
        data = {"raw_data": data}
    safe_data = scrub_sensitive_value(data or {})

    push = make_run_push(
        run_id=run_id,
        event=event,
        data=safe_data,
        seq=seq,
        workspace_id=workspace_id,
    )

    logger.debug(
        "broadcast_run_event_async prepared push payload run=%s seq=%s push_keys=%s",
        run_id,
        seq,
        sorted((push.get("data") or {}).keys()) if isinstance(push.get("data"), dict) else None,
    )

    if event == TOOL_CALL_COMPLETED_EVENT:
        logger.info(
            "broadcast_run_event_async tool completion run=%s seq=%s tool_call_id=%s status=%s keys=%s",
            run_id,
            seq,
            safe_data.get("tool_call_id"),
            safe_data.get("status"),
            sorted(safe_data.keys()),
        )

    group = _run_group(run_id)
    layer_info = _layer_diag(channel_layer)

    logger.info(
        "broadcast_run_event group_send START group=%s layer=%s seq=%s event=%s event_keys=%s",
        group,
        channel_layer.__class__.__name__,
        str(seq),
        event,
        list(push.get("data", {}).keys()) if isinstance(push.get("data"), dict) else [],
    )
    logger.info(
        "broadcast_run_event layer DIAG group=%s layer_info=%s",
        group,
        json.dumps(layer_info, default=str),
    )
    logger.info("broadcast_run_event payload DUMP %s", json.dumps(push, default=str))

    try:
        await channel_layer.group_send(group, {"type": "push", "payload": push})
    except Exception:
        logger.exception(
            "broadcast_run_event group_send failed run=%s seq=%s group=%s",
            run_id,
            seq,
            group,
        )
        raise
    else:
        logger.info("broadcast_run_event group_send OK run=%s group=%s", run_id, group)


def broadcast_run_event(
    *,
    run_id: str,
    event: str,
    data: Dict[str, Any],
    seq: Optional[int] = None,
    workspace_id: Optional[str] = None,
) -> None:
    async_to_sync(broadcast_run_event_async)(
        run_id=run_id,
        event=event,
        data=data,
        seq=seq,
        workspace_id=workspace_id,
    )


def broadcast_workspace_event(
    *,
    workspace_id: str,
    event: str,
    data: Dict[str, Any],
    seq: Optional[int] = None,
) -> None:
    """
    Broadcast a workspace-scoped push message to Channels group ws.<workspace_id>.
    """

    logger.info("Execution made it here")

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    safe_data = scrub_sensitive_value(data or {})
    logger.info(
        "Prior to broadcast push...  Workspace id = %s  Event = %s  Data = %s  Seq = %s",
        workspace_id,
        event,
        safe_data,
        seq,
    )

    push = make_workspace_push(
        workspace_id=workspace_id,
        event=event,
        data=safe_data,
        seq=seq,
    )

    workspace_group = _workspace_group(workspace_id)
    logger.info("Workspace group:  %s", workspace_group)
    logger.info("Payload: %s", push)

    async_to_sync(channel_layer.group_send)(
        workspace_group,
        {"type": "push", "payload": push},
    )


def broadcast_approvals_event(
    *,
    workspace_id: str,
    event: str,
    data: Dict[str, Any],
) -> None:
    """
    Broadcast an approval-scoped push message to Channels group approvals.<workspace_id>.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    push = make_approvals_push(
        workspace_id=workspace_id,
        event=event,
        data=scrub_sensitive_value(data or {}),
    )

    async_to_sync(channel_layer.group_send)(
        _approvals_group(workspace_id),
        {"type": "push", "payload": push},
    )
