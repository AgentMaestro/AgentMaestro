from __future__ import annotations

import hmac
import json
import logging
import time
from hashlib import sha256
from pathlib import Path

import httpx
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

from agents.models import Agent
from runs.services.memory import append_tool_result_summary
from tools.models import ToolCall, ToolDefinition
from tools.services.native_tools import execute_native_tool_call
from tools.services.quotas import acquire_tool_call_slots, release_tool_call_slots
from tools.services.result_bus import store_tool_result
from tools.services.tool_validation import ToolArgumentValidationError, validate_required_tool_arguments

logger = logging.getLogger(__name__)
_DEFAULT_TOOLRUNNER_SANDBOX_ROOT = Path("C:/tmp/agentmaestro/sandbox")
_DEFAULT_TOOLRUNNER_HTTP_TIMEOUT_BUFFER_S = 30.0
_NATIVE_TOOL_NAMES = {"remember", "search_memory", "schedule_task", "list_scheduled_tasks", "spawn_subrun", "scheduled_headless_run_gate"}


def _run_group(run_id: str) -> str:
    return f"run.{run_id}"


def _publish_tool_result_ready(
    run_id: str, tool_call_id: str, provider_call_id: str | None = None
) -> None:
    start_ts = timezone.now().isoformat()
    logger.info(
        "_publish_tool_result_ready START run=%s tool_call_id=%s ts=%s",
        run_id,
        tool_call_id,
        start_ts,
    )
    channel_layer = get_channel_layer()
    logger.debug(
        "_publish_tool_result_ready channel_layer=%s group=%s",
        channel_layer,
        _run_group(run_id),
    )
    if channel_layer is None:
        logger.warning(
            "_publish_tool_result_ready no channel layer for run=%s tool_call_id=%s",
            run_id,
            tool_call_id,
        )
        return
    payload_ts = timezone.now().isoformat()
    payload = {
        "type": "push",
        "topic": "run.event",
        "event": "tool_result_ready",
        "ts": payload_ts,
        "data": {"run_id": run_id, "tool_call_id": tool_call_id},
    }
    logger.info(
        "_publish_tool_result_ready payload ready run=%s tool_call_id=%s provider_call_id=%s ts=%s data=%s",
        run_id,
        tool_call_id,
        provider_call_id,
        payload_ts,
        payload.get("data"),
    )
    logger.debug(
        "_publish_tool_result_ready payload run=%s tool_call_id=%s ts=%s payload=%s",
        run_id,
        tool_call_id,
        payload_ts,
        payload,
    )
    try:
        async_to_sync(channel_layer.group_send)(
            _run_group(run_id),
            {"type": "push", "payload": payload},
        )
    except Exception as exc:
        logger.exception(
            "_publish_tool_result_ready failed to group_send for run=%s tool_call_id=%s ts=%s",
            run_id,
            tool_call_id,
            timezone.now().isoformat(),
            exc_info=exc,
        )
        raise
    logger.info(
        "_publish_tool_result_ready COMPLETE sent async_to_sync to channel layer run=%s tool_call_id=%s ts=%s",
        run_id,
        tool_call_id,
        timezone.now().isoformat(),
    )


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


def _resolve_sandbox_roots(agent: Agent) -> list[Path]:
    raw_paths = agent._normalize_sandbox_paths(agent.sandbox_paths)
    resolved_roots: list[Path] = []
    seen: set[Path] = set()
    for raw in raw_paths:
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = Path(settings.BASE_DIR) / candidate
        try:
            normalized = candidate.resolve()
        except Exception:
            logger.debug("skipping sandbox candidate %s because it could not be resolved", raw)
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved_roots.append(normalized)
    return resolved_roots


def _ensure_paths_within_sandbox(tool_call: ToolCall) -> None:
    agent = getattr(tool_call.run, "agent", None)
    if not agent:
        return
    allowed_roots = _resolve_sandbox_roots(agent)
    raw_roots = agent._normalize_sandbox_paths(agent.sandbox_paths)
    logger.debug(
        "sandbox roots for agent=%s resolved=%s raw=%s",
        getattr(agent, "slug", "unknown"),
        [str(root) for root in allowed_roots],
        raw_roots,
    )

    if not allowed_roots:
        return
    paths = _collect_path_strings(tool_call.args or {})
    if not paths:
        return
    for raw_path in paths:
        resolved = _resolve_candidate_path(raw_path)
        if not resolved:
            continue
        matched = False
        for root in allowed_roots:
            try:
                is_equal = resolved == root
                is_relative = resolved.is_relative_to(root)
                logger.debug(
                    "comparing tool path %s to sandbox root %s (equal=%s, relative=%s)",
                    resolved,
                    root,
                    is_equal,
                    is_relative,
                )
                if is_equal or is_relative:
                    matched = True
                    break
                logger.debug(
                    "tool path %s not equal to sandbox root %s (equal=%s, relative=%s)",
                    resolved,
                    root,
                    is_equal,
                    is_relative,
                )
            except Exception:
                logger.debug("skipping sandbox root %s because of %s", root, raw_path, exc_info=True)
                continue
        if not matched:
            logger.warning(
                "Path %s not covered by sandbox roots for agent=%s resolved_roots=%s raw=%s",
                resolved,
                getattr(agent, "slug", "unknown"),
                [str(root) for root in allowed_roots],
                raw_roots,
            )
            raise ToolrunnerError(f"Path '{resolved}' is outside the agent sandbox")


def _build_toolrunner_payload(tool_call: ToolCall, definition: ToolDefinition) -> tuple[bytes, dict]:
    args = tool_call.args or {}
    allowed_roots = []
    agent = getattr(tool_call.run, "agent", None)
    if agent is not None:
        allowed_roots = [str(root) for root in _resolve_sandbox_roots(agent)]
    repo_root = str(Path(settings.BASE_DIR).resolve().parent)
    sandbox_root = Path(getattr(settings, "TOOLRUNNER_SANDBOX_ROOT", _DEFAULT_TOOLRUNNER_SANDBOX_ROOT)).resolve()
    tmp_root = str(sandbox_root.parent)
    allow_write = bool(
        tool_call.tool_name in {"file_write", "file_patch"}
        and tool_call.approved_at
    )
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
            "allow_write": allow_write,
            "allowed_roots": allowed_roots,
            "repo_root": repo_root,
            "tmp_root": tmp_root,
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


def _toolrunner_error_payload(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    try:
        body_text = response.text
    except Exception:
        body_text = ""
    return {
        "ok": False,
        "error": {
            "code": "tool_runner.HTTP_ERROR",
            "message": f"toolrunner returned HTTP {response.status_code}",
            "details": {"body": body_text[:4000]},
        },
    }


def _coerce_positive_seconds(raw_value: object | None, *, divisor: float = 1.0) -> float | None:
    if raw_value in (None, "", False):
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value / divisor


def _tool_timeout_seconds(tool_call: ToolCall, payload: dict[str, object]) -> float | None:
    args = tool_call.args or {}
    timeout_s = _coerce_positive_seconds(args.get("timeout_ms"), divisor=1000.0)
    if timeout_s is not None:
        return timeout_s
    timeout_s = _coerce_positive_seconds(args.get("timeout_seconds"))
    if timeout_s is not None:
        return timeout_s
    limits = payload.get("limits")
    if isinstance(limits, dict):
        timeout_s = _coerce_positive_seconds(limits.get("timeout_s"))
        if timeout_s is not None:
            return timeout_s
    return None


def _sanitize_postgres_text(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("\x00", "\\u0000")


def _sanitize_postgres_payload(value: object) -> object:
    if isinstance(value, str):
        return _sanitize_postgres_text(value)
    if isinstance(value, dict):
        return {
            _sanitize_postgres_text(key) if isinstance(key, str) else key: _sanitize_postgres_payload(sub_value)
            for key, sub_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_postgres_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_postgres_payload(item) for item in value)
    return value


def _toolrunner_http_timeout_seconds(tool_call: ToolCall, payload: dict[str, object]) -> float:
    default_timeout = float(getattr(settings, "TOOLRUNNER_HTTP_TIMEOUT", 45))
    tool_timeout = _tool_timeout_seconds(tool_call, payload)
    if tool_timeout is None:
        return default_timeout
    buffer_s = float(
        getattr(
            settings,
            "TOOLRUNNER_HTTP_TIMEOUT_BUFFER",
            _DEFAULT_TOOLRUNNER_HTTP_TIMEOUT_BUFFER_S,
        )
    )
    return max(default_timeout, tool_timeout + max(buffer_s, 1.0))


def _emit_tool_call_completed(tool_call: ToolCall, duration_ms: int) -> None:
    tool_call.refresh_from_db(fields=["provider_call_id"])
    provider_call_id = str(tool_call.provider_call_id or "").strip() or None
    payload = {
        "tool_call_id": str(tool_call.id),
        "status": tool_call.status,
        "tool_name": tool_call.tool_name,
        "exit_code": tool_call.exit_code,
        "stdout": tool_call.stdout or "",
        "stderr": tool_call.stderr or "",
        "result": tool_call.result or {},
        "duration_ms": duration_ms,
        "run_id": str(tool_call.run_id),
        "correlation_id": str(tool_call.correlation_id),
        "provider_call_id": provider_call_id,
    }
    logger.info(
        "execution._emit_tool_call_completed --> Tool name=%s run=%s result=%s status=%s",
        tool_call.tool_name,
        tool_call.run_id,
        tool_call.result,
        tool_call.status,
    )
    logger.info(
        "ToolRunner emitting completion run=%s tool_call=%s status=%s exit=%s args=%s",
        tool_call.run_id,
        tool_call.id,
        tool_call.status,
        tool_call.exit_code,
        list((tool_call.args or {}).keys()),
    )

    try:
        store_tool_result(
            run_id=str(tool_call.run_id),
            tool_call_id=str(tool_call.id),
            payload=payload,
        )
        logger.info(
            "execution._emit_tool_call_completed stored tool_result run=%s tool_call=%s provider=%s",
            tool_call.run_id,
            tool_call.id,
            provider_call_id,
        )
        _publish_tool_result_ready(
            str(tool_call.run_id),
            str(tool_call.id),
            provider_call_id=provider_call_id,
        )
        logger.info(
            "execution._emit_tool_call_completed tool_result_ready published run=%s tool_call=%s provider=%s",
            tool_call.run_id,
            tool_call.id,
            provider_call_id,
        )
    except Exception:
        logger.exception(
            "Failed to store or publish tool_result for run=%s tool_call=%s",
            tool_call.run_id,
            tool_call.id,
        )




def _summarize_tool_result_for_memory(tool_call: ToolCall) -> str:
    parts = [f"status={tool_call.status.lower()}"]
    if tool_call.exit_code is not None:
        parts.append(f"exit_code={tool_call.exit_code}")
    if tool_call.error:
        parts.append(f"error={tool_call.error}")
    elif tool_call.stderr:
        parts.append(f"stderr={tool_call.stderr}")
    elif tool_call.stdout:
        parts.append(f"stdout={tool_call.stdout}")
    elif tool_call.result:
        parts.append(f"result={tool_call.result}")
    return " | ".join(str(part).strip() for part in parts if str(part).strip())

def execute_tool_call(tool_call_id: str) -> ToolCall:
    tool_call = (
        ToolCall.objects
        .select_related("run__workspace")
        .get(id=tool_call_id)
    )
    logger.info("execute_tool_call start tool_call=%s run=%s status=%s", tool_call.id, tool_call.run_id, tool_call.status)
    if tool_call.tool_name not in _NATIVE_TOOL_NAMES:
        _ensure_paths_within_sandbox(tool_call)
        logger.info("execute_tool_call sandbox check passed tool_call=%s", tool_call.id)
    if tool_call.status not in {ToolCall.Status.QUEUED, ToolCall.Status.RUNNING}:
        raise RuntimeError(f"Cannot execute tool call in status {tool_call.status}")

    internal_gate_tool = tool_call.tool_name == "scheduled_headless_run_gate"
    definition = None
    if not internal_gate_tool:
        definition = (
            ToolDefinition.objects
            .filter(workspace_id=tool_call.run.workspace_id, name=tool_call.tool_name, enabled=True)
            .first()
        )
    logger.info("execute_tool_call tool_definition lookup tool_call=%s definition=%s", tool_call.id, definition.id if definition else None)
    if not internal_gate_tool and not definition:
        raise RuntimeError(f"tool {tool_call.tool_name} not enabled for workspace")

    if not internal_gate_tool:
        try:
            validate_required_tool_arguments(
                tool_call.tool_name,
                tool_call.args or {},
                definition=definition,
            )
        except ToolArgumentValidationError as exc:
            logger.warning(
                "execute_tool_call validation failed tool_call=%s tool=%s missing=%s submitted=%s",
                tool_call.id,
                tool_call.tool_name,
                exc.missing_parameters,
                list((tool_call.args or {}).keys()),
            )
            now = timezone.now()
            tool_call.status = ToolCall.Status.FAILED
            tool_call.started_at = now
            tool_call.exit_code = 2
            tool_call.stdout = ""
            tool_call.stderr = str(exc)
            tool_call.result = exc.to_result()
            tool_call.ended_at = now
            tool_call.observed_at = now
            tool_call.save(update_fields=[
                "status",
                "started_at",
                "exit_code",
                "stdout",
                "stderr",
                "result",
                "ended_at",
                "observed_at",
                "updated_at",
            ])
            append_tool_result_summary(
                tool_call.run_id,
                str(tool_call.id),
                _summarize_tool_result_for_memory(tool_call),
            )
            _emit_tool_call_completed(tool_call, 0)
            return tool_call

    acquired_quota = False
    acquire_tool_call_slots(
        str(tool_call.run.workspace_id),
        str(tool_call.run_id),
        str(tool_call.id),
    )
    acquired_quota = True

    tool_call.status = ToolCall.Status.RUNNING
    tool_call.started_at = timezone.now()
    tool_call.save(update_fields=["status", "started_at", "updated_at"])

    if internal_gate_tool:
        body = b""
        payload = {"limits": {}}
    else:
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
    tool_timeout_s = _tool_timeout_seconds(tool_call, payload)
    http_timeout_s = _toolrunner_http_timeout_seconds(tool_call, payload)
    logger.info("execute_tool_call sending http tool_call=%s tool=%s", tool_call.id, tool_call.tool_name)
    logger.info(
        "execute_tool_call timeout plan tool_call=%s tool=%s tool_timeout_s=%s http_timeout_s=%s default_http_timeout_s=%s",
        tool_call.id,
        tool_call.tool_name,
        tool_timeout_s,
        http_timeout_s,
        getattr(settings, "TOOLRUNNER_HTTP_TIMEOUT", 45),
    )
    try:
        if tool_call.tool_name in _NATIVE_TOOL_NAMES:
            data = execute_native_tool_call(tool_call)
        else:
            with httpx.Client(timeout=http_timeout_s) as client:
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
        error_payload = _toolrunner_error_payload(exc.response)
        result_payload = {
            "http_status": exc.response.status_code,
            "toolrunner_response": error_payload,
        }
        error = error_payload.get("error") if isinstance(error_payload, dict) else {}
        error = error if isinstance(error, dict) else {}
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
        if code and message:
            stderr = f"toolrunner error: {code}: {message}"
        elif message:
            stderr = f"toolrunner error: {message}"
        else:
            stderr = f"toolrunner error: {exc.response.status_code}"
    except httpx.RequestError as exc:
        result_payload = {
            "http_timeout_s": http_timeout_s,
            "tool_timeout_s": tool_timeout_s,
            "timeout_source": "TOOLRUNNER_HTTP_TIMEOUT"
            if isinstance(exc, httpx.TimeoutException)
            else None,
            "tool_timeout_source": "args.timeout_ms_or_limits.timeout_s"
            if tool_timeout_s is not None
            else None,
        }
        if isinstance(exc, httpx.TimeoutException):
            stderr = (
                "toolrunner request failed: timed out "
                f"(source=TOOLRUNNER_HTTP_TIMEOUT effective_timeout_s={http_timeout_s}"
                f" tool_timeout_s={tool_timeout_s})"
            )
        else:
            stderr = f"toolrunner request failed: {exc}"
    except Exception as exc:  # pragma: no cover
        logger.exception("execute_tool_call exception tool_call=%s", tool_call.id)
        raise
    finally:
        logger.info("execute_tool_call completed tool_call=%s status=%s result=%s", tool_call.id, tool_call.status, tool_call.result)
        duration_ms = int(round((time.monotonic() - start) * 1000))
        now = timezone.now()
        stdout = _sanitize_postgres_text(stdout)
        stderr = _sanitize_postgres_text(stderr)
        result_payload = _sanitize_postgres_payload(result_payload)
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
        append_tool_result_summary(
            tool_call.run,
            tool_call.tool_name,
            _summarize_tool_result_for_memory(tool_call),
        )
        if tool_call.requires_approval or acquired_quota:
            release_tool_call_slots(
                str(tool_call.run.workspace_id),
                str(tool_call.run_id),
                str(tool_call.id),
            )
        _emit_tool_call_completed(tool_call, duration_ms)

    return tool_call
