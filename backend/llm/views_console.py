import json
import time
from datetime import datetime, timezone as dt_timezone
from typing import Any, Callable, Dict, Iterator, List, Tuple

from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from logging_utils import scrub_sensitive_value
from .models import LLMMessage, LLMRun, LLMToolCall, MessageRole, RunStatus

CONSOLE_ORIGIN = "http://127.0.0.1:8001"
CONSOLE_DETAIL_URL = "http://127.0.0.1:8000/llm/console/detail"
POLL_INTERVAL = 0.25
DEFAULT_LIMIT = 50


def _apply_console_cors(response):
    response["Access-Control-Allow-Origin"] = CONSOLE_ORIGIN
    response["Access-Control-Allow-Methods"] = "GET"
    response["Vary"] = "Origin"
    return response


def _format_cursor(cursor: Dict[str, Any]) -> str:
    return f"m:{cursor['message_id']}|t:{cursor['tool_id']}|r:{int(cursor['run_ts'] * 1000)}"


def _parse_cursor(value: str | None) -> Dict[str, Any]:
    cursor = {"message_id": 0, "tool_id": 0, "run_ts": 0.0}
    if not value:
        return cursor
    for part in value.split("|"):
        if ":" not in part:
            continue
        kind, raw = part.split(":", 1)
        if kind == "m":
            try:
                cursor["message_id"] = max(cursor["message_id"], int(raw))
            except ValueError:
                pass
        elif kind == "t":
            try:
                cursor["tool_id"] = max(cursor["tool_id"], int(raw))
            except ValueError:
                pass
        elif kind == "r":
            try:
                cursor["run_ts"] = max(cursor["run_ts"], int(raw) / 1000.0)
            except ValueError:
                pass
    return cursor


def _cursor_from_dt(dt: datetime) -> float:
    return dt.timestamp() if dt else 0.0


def _iso_timestamp(dt: datetime) -> str:
    return dt.astimezone(dt_timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _render_agent(run: LLMRun) -> str:
    if run.profile and run.profile.name:
        return run.profile.name
    if run.agent_name:
        return run.agent_name
    return "LLM"


def _shorten(text: str, limit: int = 120) -> str:
    trimmed = " ".join(text.split())
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: limit - 1] + "…"


def _merge_events(
    cursor: Dict[str, Any],
    events: List[Tuple[datetime, Dict[str, Any], Callable[[Dict[str, Any]], None]]],
    limit: int,
) -> List[Dict[str, Any]]:
    events.sort(key=lambda item: item[0])
    output: List[Dict[str, Any]] = []
    for ts, payload, updater in events:
        if len(output) >= limit:
            break
        updater(cursor)
        payload["cursor"] = _format_cursor(cursor)
        payload["ts"] = _iso_timestamp(ts)
        output.append(payload)
    return output


def _run_events(
    run: LLMRun, cursor_ts: float
) -> List[Tuple[datetime, Dict[str, Any], Callable[[Dict[str, Any]], None]]]:
    events: List[Tuple[datetime, Dict[str, Any], Callable[[Dict[str, Any]], None]]] = []
    agent = _render_agent(run)
    updated_ts = run.updated_at or timezone.now()
    created_ts = run.created_at or updated_ts

    def update_run_cursor(c: Dict[str, Any]):
        c["run_ts"] = max(c["run_ts"], _cursor_from_dt(updated_ts))

    if _cursor_from_dt(created_ts) > cursor_ts:
        summary = f"RUN started (profile={agent} model={run.model})"
        payload = {
            "level": "INFO",
            "run_id": str(run.id),
            "agent": agent,
            "kind": "run_started",
            "summary": summary,
            "details_url": f"{CONSOLE_DETAIL_URL}?type=run&id={run.id}",
        }
        events.append((created_ts, scrub_sensitive_value(payload), update_run_cursor))

    if (
        run.status in (RunStatus.COMPLETED, RunStatus.FAILED)
        and _cursor_from_dt(updated_ts) > cursor_ts
    ):
        level = "ERROR" if run.status == RunStatus.FAILED else "INFO"
        summary = (
            "RUN failed: "
            + (_shorten(run.error or "unknown error") if run.error else "unknown error")
            if run.status == RunStatus.FAILED
            else "RUN completed"
        )
        payload = {
            "level": level,
            "run_id": str(run.id),
            "agent": agent,
            "kind": "run_completed" if run.status == RunStatus.COMPLETED else "run_failed",
            "summary": summary,
            "details_url": f"{CONSOLE_DETAIL_URL}?type=run&id={run.id}",
        }
        events.append((updated_ts, scrub_sensitive_value(payload), update_run_cursor))
    return events


def _message_event(
    message: LLMMessage,
    cursor: Dict[str, Any],
) -> Tuple[datetime, Dict[str, Any], Callable[[Dict[str, Any]], None]]:
    run = message.run
    ts = message.created_at or timezone.now()
    agent = _render_agent(run)
    content = _shorten(message.content or "")
    kind = "llm_message"
    payload = {
        "level": "INFO",
        "run_id": str(run.id),
        "agent": agent,
        "kind": kind,
        "summary": f"[{agent}] {content}",
        "details_url": f"{CONSOLE_DETAIL_URL}?type=message&id={message.id}",
    }
    payload = scrub_sensitive_value(payload)

    def updater(cursor_state: Dict[str, Any]):
        cursor_state["message_id"] = max(cursor_state["message_id"], message.id)
        cursor_state["run_ts"] = max(cursor_state["run_ts"], _cursor_from_dt(ts))

    return ts, payload, updater


def _tool_call_events(
    call: LLMToolCall,
) -> List[Tuple[datetime, Dict[str, Any], Callable[[Dict[str, Any]], None]]]:
    run = call.run
    agent = _render_agent(run)
    ts = call.created_at or timezone.now()
    args = call.arguments or {}
    args_parts = []
    for key, value in list(args.items())[:4]:
        try:
            snippet = json.dumps(value, ensure_ascii=False)
        except Exception:
            snippet = str(value)
        args_parts.append(f"{key}={_shorten(snippet, 40)}")
    args_text = " ".join(args_parts)
    events: List[Tuple[datetime, Dict[str, Any], Callable[[Dict[str, Any]], None]]] = []

    def update_tool_cursor(cursor_state: Dict[str, Any]):
        cursor_state["tool_id"] = max(cursor_state["tool_id"], call.id)
        cursor_state["run_ts"] = max(cursor_state["run_ts"], _cursor_from_dt(ts))

    tool_summary = f"TOOLCALL {call.tool_name} {args_text}".strip()
    tool_payload = {
        "level": "TOOL",
        "run_id": str(run.id),
        "agent": agent,
        "kind": "tool_call",
        "summary": tool_summary,
        "details_url": f"{CONSOLE_DETAIL_URL}?type=tool_call&id={call.id}",
    }
    events.append((ts, scrub_sensitive_value(tool_payload), update_tool_cursor))

    result_summary = f"TOOLRESULT {call.tool_name} ok={call.success}"
    if call.error:
        result_summary += f" error={_shorten(call.error, 80)}"
        level = "ERROR"
    else:
        level = "TOOL"
    result_payload = {
        "level": level,
        "run_id": str(run.id),
        "agent": agent,
        "kind": "tool_result",
        "summary": result_summary,
        "details_url": f"{CONSOLE_DETAIL_URL}?type=tool_call&id={call.id}",
    }
    events.append((ts, scrub_sensitive_value(result_payload), update_tool_cursor))
    return events


def _collect_events(cursor: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    events: List[Tuple[datetime, Dict[str, Any], Callable[[Dict[str, Any]], None]]] = []
    message_q = (
        LLMMessage.objects.filter(id__gt=cursor["message_id"])
        .select_related("run__profile")
        .order_by("created_at")[:limit]
    )
    events.extend(
        [
            _message_event(message, cursor)
            for message in message_q
            if message.role == MessageRole.ASSISTANT
        ]
    )
    tool_calls = (
        LLMToolCall.objects.filter(id__gt=cursor["tool_id"])
        .select_related("run__profile")
        .order_by("created_at")[:limit]
    )
    for call in tool_calls:
        events.extend(_tool_call_events(call))
    run_since = datetime.fromtimestamp(cursor["run_ts"], tz=dt_timezone.utc)
    run_events = (
        LLMRun.objects.filter(updated_at__gt=run_since)
        .select_related("profile")
        .order_by("updated_at")[:limit]
    )
    for run in run_events:
        events.extend(_run_events(run, cursor["run_ts"]))
    return _merge_events(cursor, events, limit)


def _event_stream(cursor: Dict[str, Any], limit: int) -> Iterator[str]:
    try:
        last_cursor = cursor.copy()
        while True:
            payloads = _collect_events(last_cursor, limit)
            if payloads:
                for payload in payloads:
                    yield f"data: {json.dumps(payload)}\n\n"
            else:
                time.sleep(POLL_INTERVAL)
    except GeneratorExit:
        return


@require_GET
def console_stream(request):
    since = request.GET.get("since")
    try:
        limit = min(int(request.GET.get("limit") or DEFAULT_LIMIT), 200)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    cursor = _parse_cursor(since)
    response = StreamingHttpResponse(_event_stream(cursor, limit), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return _apply_console_cors(response)


def _record_as_dict(record):
    if hasattr(record, "model_dump"):
        return record.model_dump()
    if hasattr(record, "__dict__"):
        data = {k: v for k, v in record.__dict__.items() if not k.startswith("_")}
        data["id"] = str(data.get("id"))
        return data
    return {}


@require_GET
def console_detail(request):
    record_type = request.GET.get("type")
    object_id = request.GET.get("id")
    if not record_type or not object_id:
        raise Http404
    if record_type == "run":
        try:
            entity = LLMRun.objects.get(id=object_id)
        except LLMRun.DoesNotExist:
            raise Http404
        data = {
            "id": str(entity.id),
            "status": entity.status,
            "error": entity.error,
            "provider_meta": entity.provider_meta,
            "model": entity.model,
            "profile": entity.profile.name if entity.profile else None,
            "created_at": _iso_timestamp(entity.created_at),
            "updated_at": _iso_timestamp(entity.updated_at),
            "purpose": entity.purpose,
        }
    elif record_type == "message":
        try:
            entity = LLMMessage.objects.get(id=object_id)
        except LLMMessage.DoesNotExist:
            raise Http404
        data = {
            "id": entity.id,
            "role": entity.role,
            "content": entity.content,
            "meta": entity.meta,
            "created_at": _iso_timestamp(entity.created_at),
            "run_id": str(entity.run_id),
        }
    elif record_type == "tool_call":
        try:
            entity = LLMToolCall.objects.get(id=object_id)
        except LLMToolCall.DoesNotExist:
            raise Http404
        data = {
            "id": entity.id,
            "tool_name": entity.tool_name,
            "arguments": entity.arguments,
            "result": entity.result,
            "success": entity.success,
            "error": entity.error,
            "created_at": _iso_timestamp(entity.created_at),
            "run_id": str(entity.run_id),
        }
    else:
        raise Http404
    response = JsonResponse({"type": record_type, "data": scrub_sensitive_value(data)})
    return _apply_console_cors(response)
