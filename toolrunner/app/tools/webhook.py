from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse

def create_webhook(payload: dict) -> JSONResponse:
    event = str(payload.get("event") or "").strip()
    if not event:
        raise HTTPException(status_code=400, detail="event required")
    run_id = str(payload.get("run_id") or "unknown")
    webhook_payload = payload.get("payload")
    if webhook_payload is not None and not isinstance(webhook_payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "accepted": True,
                "tool": "webhook",
                "event": event,
                "run_id": run_id,
                "payload": webhook_payload or {},
            },
        },
    )
