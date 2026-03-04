from __future__ import annotations

import hmac
import json
import logging
import time
from hashlib import sha256
from pathlib import Path

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

_PATH_KEYWORDS = (
    "path",
    "paths",
    "cwd",
    "directory",
    "dir",
    "root",
    "target",
    "source",
    "dest",
    "destination",
    "file",
    "filename",
    "repo",
    "workspace",
)


def _collect_path_strings(payload: object | None) -> list[str]:
    matches: list[str] = []

    def _collect(value: object | None, key: str | None = None) -> None:
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                lower_key = (sub_key or "").lower()
                if any(token in lower_key for token in _PATH_KEYWORDS):
                    matches.extend(_extract_strings(sub_value))
                _collect(sub_value, sub_key)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                _collect(item, key)
        elif isinstance(value, str) and key:
            lower_key = key.lower()
            if any(token in lower_key for token in _PATH_KEYWORDS):
                matches.append(value)

    def _extract_strings(value: object | None) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            result: list[str] = []
            for item in value:
                result.extend(_extract_strings(item))
            return result
        if isinstance(value, dict):
            result: list[str] = []
            for sub_value in value.values():
                result.extend(_extract_strings(sub_value))
            return result
        return []

    _collect(payload)
    return matches


def _resolve_candidate_path(raw_path: str) -> Path | None:
    try:
        candidate = Path(raw_path)
    except Exception:
        return None
    if not candidate.is_absolute():
        candidate = Path(settings.BASE_DIR) / candidate
    try:
        return candidate.expanduser().resolve()
    except Exception:
        return None


def _ensure_paths_within_sandbox(tool_call: ToolCall) -> None:
    agent = getattr(tool_call.run, "agent", None)
    if not agent:
        return
    allowed_roots = agent.get_sandbox_roots()
    if not allowed_roots:
        return
    paths = _collect_path_strings(tool_call.args or {})
    if not paths:
        return
    for raw_path in paths:
        resolved = _resolve_candidate_path(raw_path)
        if not resolved:
            continue
        for root in allowed_roots:
            try:
                if resolved == root or resolved.is_relative_to(root):
                    break
            except Exception:
                continue
        else:
            raise ToolrunnerError(f"Path '{resolved}' is outside the agent sandbox")


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
    limits.setdefault("timeout_s", settings.TOOLRUNNER_TIMEOUT)
    limits.setdefault("max_output_bytes", settings.TOOLRUNNER_OUTPUT_LIMIT)
    payload["limits"] = limits
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return body, payload


def _sign_payload(body: bytes, timestamp: str) -> str:
    key = settings.TOOLRUNNER_SECRET.encode("utf-8")
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
    _ensure_paths_within_sandbox(tool_call)
    if tool_call.status not in {ToolCall.Status.QUEUED, ToolCall.Status.RUNNING}:
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
        with httpx.Client(timeout=settings.TOOLRUNNER_HTTP_TIMEOUT) as client:
            request = httpx.Request("POST", settings.TOOLRUNNER_URL)
            response = client.post(
                settings.TOOLRUNNER_URL,
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
        tool_call.status = ToolCall.Status.COMPLETED if succeeded else ToolCall.Status.FAILED
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
