# backend/runs/services/events.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from runs.models import AgentRun, RunEvent
from runs.services.event_contracts import (
    make_approvals_push,
    make_run_push,
    make_workspace_push,
)


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
    # Lock the run row so concurrent tickers cannot allocate the same seq.
    run = (
        AgentRun.objects
        .select_for_update()
        .select_related("workspace")
        .get(id=run_id)
    )

    # Compute next seq from existing events (safe under run row lock).
    agg = RunEvent.objects.filter(run_id=run_id).aggregate(m=Max("seq"))
    next_seq = int((agg["m"] or 0) + 1)

    evt = RunEvent.objects.create(
        run_id=run_id,
        seq=next_seq,
        event_type=event_type,
        payload=payload or {},
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )

    # Broadcast only after commit.
    def _after_commit():
        if broadcast_to_run:
            broadcast_run_event(
                run_id=str(run.id),
                workspace_id=str(run.workspace_id),
                seq=next_seq,
                event=event_type,
                data=payload or {},
            )

        if broadcast_to_workspace:
            broadcast_workspace_event(
                workspace_id=str(run.workspace_id),
                event=workspace_summary_event,
                data={
                    "run_id": str(run.id),
                    "seq": next_seq,
                    "event_type": event_type,
                    "payload": payload or {},
                },
            )

    transaction.on_commit(_after_commit)

    return evt, next_seq


def broadcast_run_event(
    *,
    run_id: str,
    event: str,
    data: Dict[str, Any],
    seq: Optional[int] = None,
    workspace_id: Optional[str] = None,
) -> None:
    """
    Broadcast a run-scoped push message to Channels group run.<run_id>.
    Works with either InMemoryChannelLayer or RedisChannelLayer.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    push = make_run_push(
        run_id=run_id,
        event=event,
        data=data or {},
        seq=seq,
        workspace_id=workspace_id,
    )

    async_to_sync(channel_layer.group_send)(
        _run_group(run_id),
        {"type": "push", "payload": push},
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
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    push = make_workspace_push(
        workspace_id=workspace_id,
        event=event,
        data=data or {},
        seq=seq,
    )

    async_to_sync(channel_layer.group_send)(
        _workspace_group(workspace_id),
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
        data=data or {},
    )

    async_to_sync(channel_layer.group_send)(
        _approvals_group(workspace_id),
        {"type": "push", "payload": push},
    )
