from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PushMessage:
    """Standard outbound WS payload (server -> client). Fields are intentionally simple JSON primitives."""

    type: str  # always "push"
    topic: str  # run.event | workspace.event | approvals.event | user.event
    ts: str  # ISO-8601 UTC timestamp
    event: str  # event name, e.g. "state_changed"
    data: Dict[str, Any]  # payload dict
    seq: Optional[int] = None
    run_id: Optional[str] = None
    workspace_id: Optional[str] = None
    user_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "topic": self.topic,
            "ts": self.ts,
            "event": self.event,
            "data": self.data,
        }
        if self.seq is not None:
            out["seq"] = self.seq
        if self.run_id:
            out["run_id"] = self.run_id
        if self.workspace_id:
            out["workspace_id"] = self.workspace_id
        if self.user_id:
            out["user_id"] = self.user_id
        return out


def make_run_push(*, run_id: str, event: str, data: Dict[str, Any], seq: Optional[int] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
    return PushMessage(
        type="push",
        topic="run.event",
        ts=iso_utc_now(),
        event=event,
        data=data,
        seq=seq,
        run_id=run_id,
        workspace_id=workspace_id,
    ).to_dict()


def make_workspace_push(*, workspace_id: str, event: str, data: Dict[str, Any], seq: Optional[int] = None) -> Dict[str, Any]:
    return PushMessage(
        type="push",
        topic="workspace.event",
        ts=iso_utc_now(),
        event=event,
        data=data,
        seq=seq,
        workspace_id=workspace_id,
    ).to_dict()


def make_approvals_push(*, workspace_id: str, event: str, data: Dict[str, Any], seq: Optional[int] = None) -> Dict[str, Any]:
    return PushMessage(
        type="push",
        topic="approvals.event",
        ts=iso_utc_now(),
        event=event,
        data=data,
        seq=seq,
        workspace_id=workspace_id,
    ).to_dict()


def make_user_push(*, user_id: str, event: str, data: Dict[str, Any], seq: Optional[int] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
    return PushMessage(
        type="push",
        topic="user.event",
        ts=iso_utc_now(),
        event=event,
        data=data,
        seq=seq,
        user_id=user_id,
        workspace_id=workspace_id,
    ).to_dict()
