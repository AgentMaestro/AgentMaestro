from __future__ import annotations

from fastapi import HTTPException

from ..models import ExecuteResponse


def create_webhook(payload: dict) -> ExecuteResponse:
    if not payload.get("event"):
        raise HTTPException(status_code=400, detail="event required")
    return ExecuteResponse(status="ok", run_id=payload.get("run_id", "unknown"), tool="webhook", result=None)
