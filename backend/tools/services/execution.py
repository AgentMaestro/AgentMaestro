from __future__ import annotations

import hmac
import json
import logging
import time
from hashlib import sha256

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from runs.services.events import append_event
from tools.models import ToolCall, ToolDefinition
from tools.services.quotas import acquire_tool_call_slots, release_tool_call_slots

logger = logging.getLogger(__name__)

TOOL_CALL_COMPLETED_EVENT = "tool_call_completed"


class ToolrunnerError(RuntimeError):
    pass


def _build_toolrunner_payload(tool_call: ToolCall, definition: ToolDefinition) -> tuple[bytes, dict]:
    args = tool_call.args or {}
    payload = {
        "request_id": str(tool_call.id),
        "workspace_id": str(tool_call.run.workspace_id),
        "run_id": str(tool_call.run_id),
        "tool_name": tool_call.tool_name,
        "args": args,
        "policy": {
            "risk_level": tool_call.risk_level,
            "tool_definition_id": str(definition.id),
            "requires_approval": tool_call.requires_approval,
        },
    }
    limits = dict(args.get("limits") or {})
    limits.setdefault("timeout_s", settings.AGENTMAESTRO_TOOLRUNNER_TIMEOUT)
    limits.setdefault("max_output_bytes", settings.AGENTMAESTRO_TOOLRUNNER_OUTPUT_LIMIT)
    payload["limits"] = limits
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return body, payload


def _sign_payload(body: bytes, timestamp: str) -> str:
    key = settings.AGENTMAESTRO_TOOLRUNNER_SECRET.encode("utf-8")
    message = timestamp.encode("utf-8") + b"." + body
    return hmac.new(key, message, sha256).hexdigest()


def _emit_tool_call_completed(tool_call: ToolCall, duration_ms: int) -> None:
    payload = {
        "tool_call_id": str(tool_call.id),
        "status": tool_call.status,
        "exit_code": tool_call.exit_code,
        "stdout": tool_call.stdout,
        "stderr": tool_call.stderr,
        "result": tool_call.result,
        "duration_ms": duration_ms,
    }

    def _after_commit():
        append_event(
            run_id=str(tool_call.run_id),
            event_type=TOOL_CALL_COMPLETED_EVENT,
            payload=payload,
            correlation_id=tool_call.correlation_id,
        )

    transaction.on_commit(_after_commit)


def execute_tool_call(tool_call_id: str) -> ToolCall:
    tool_call = (
        ToolCall.objects
        .select_related("run__workspace")
        .get(id=tool_call_id)
    )
    if tool_call.status not in {ToolCall.Status.APPROVED, ToolCall.Status.RUNNING}:
        raise RuntimeError(f"Cannot execute tool call in status {tool_call.status}")

    definition = (
        ToolDefinition.objects
        .filter(workspace_id=tool_call.run.workspace_id, name=tool_call.tool_name, enabled=True)
        .first()
    )
    if not definition:
        raise RuntimeError(f"tool {tool_call.tool_name} not enabled for workspace")

    acquired_quota = False
    if not tool_call.requires_approval:
        acquire_tool_call_slots(
            str(tool_call.run.workspace_id),
            str(tool_call.run_id),
            str(tool_call.id),
        )
        acquired_quota = True

    tool_call.status = ToolCall.Status.RUNNING
    tool_call.started_at = timezone.now()
    tool_call.save(update_fields=["status", "started_at", "updated_at"])

    body, payload = _build_toolrunner_payload(tool_call, definition)
    timestamp = str(int(time.time()))
    signature = _sign_payload(body, timestamp)
    headers = {
        "X-AM-Timestamp": timestamp,
        "X-AM-Signature": signature,
        "Content-Type": "application/json",
    }

    start = time.monotonic()
    stdout = ""
    stderr = ""
    exit_code = None
    succeeded = False
    result_payload: dict[str, object] = {}
    try:
        with httpx.Client(timeout=settings.AGENTMAESTRO_TOOLRUNNER_HTTP_TIMEOUT) as client:
            request = httpx.Request("POST", settings.AGENTMAESTRO_TOOLRUNNER_URL)
            response = client.post(
                settings.AGENTMAESTRO_TOOLRUNNER_URL,
                content=body,
                headers=headers,
            )
        if response.is_error:
            raise httpx.HTTPStatusError("toolrunner error", request=request, response=response)
        data = response.json()
        status_str = data.get("status", "FAILED")
        succeeded = status_str == "COMPLETED"
        exit_code = data.get("exit_code")
        stdout = data.get("stdout") or ""
        stderr = data.get("stderr") or ""
        result_payload = data.get("result") or {}
    except httpx.HTTPStatusError as exc:
        stderr = f"toolrunner error: {exc.response.status_code}"
    except httpx.RequestError as exc:
        stderr = f"toolrunner request failed: {exc}"
    finally:
        duration_ms = int(round((time.monotonic() - start) * 1000))
        now = timezone.now()
        tool_call.status = ToolCall.Status.SUCCEEDED if succeeded else ToolCall.Status.FAILED
        tool_call.exit_code = exit_code
        tool_call.stdout = stdout
        tool_call.stderr = stderr
        tool_call.result = result_payload
        tool_call.ended_at = now
        tool_call.observed_at = now
        tool_call.save(update_fields=[
            "status",
            "exit_code",
            "stdout",
            "stderr",
            "result",
            "ended_at",
            "observed_at",
            "updated_at",
        ])
        if tool_call.requires_approval or acquired_quota:
            release_tool_call_slots(
                str(tool_call.run.workspace_id),
                str(tool_call.run_id),
                str(tool_call.id),
            )
        _emit_tool_call_completed(tool_call, duration_ms)

    return tool_call
