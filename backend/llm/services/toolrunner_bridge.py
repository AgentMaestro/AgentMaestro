import hmac
import json
import time
import uuid
from hashlib import sha256
from typing import Any, Dict, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.services.timezones import get_current_datetime_iso8601, get_local_timezone_name, get_tango_timezone_name
from tools.models import ToolDefinition
from tools.services.tool_validation import ToolArgumentValidationError, validate_required_tool_arguments

_NATIVE_TOOL_NAMES = {
    "remember",
    "search_memory",
    "get_current_datetime",
    "schedule_task",
    "get_scheduled_task",
    "list_scheduled_tasks",
    "edit_scheduled_task",
    "disable_scheduled_task",
    "enable_scheduled_task",
    "spawn_subrun",
    "send_telegram",
    "google_bridge",
}


def _sign(body: bytes, secret: bytes) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    message = timestamp.encode("utf-8") + b"." + body
    signature = hmac.new(secret, message, sha256).hexdigest()
    return timestamp, signature


def _coerce_bool(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    raise RuntimeError("Invalid pinned value; expected a boolean.")


def _coerce_expires_at(value: object):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise RuntimeError("Invalid expires_at value; expected an ISO 8601 datetime string.")
    parsed = parse_datetime(value.strip())
    if parsed is None:
        raise RuntimeError("Invalid expires_at value; expected an ISO 8601 datetime string.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _run_native_tool(tool_name: str, args: Dict[str, Any], orchestration_run_id: Optional[str]) -> Dict[str, Any]:
    from google_bridge.services.bridge import build_google_task_objective, execute_google_task
    from comms.services.agent_chat_bridge import send_paired_telegram_message
    from memory.scheduled_tasks import (
        create_scheduled_task,
        disable_scheduled_task,
        enable_scheduled_task,
        get_scheduled_task,
        list_scheduled_tasks,
        serialize_scheduled_task,
        update_scheduled_task,
    )
    from memory.services import remember as remember_memory
    from memory.services import search_memory as search_memory_records
    from runs.models import AgentRun
    from runs.services.subruns import run_subrun_flow
    from runs.services.subruns import spawn_subrun

    if not orchestration_run_id:
        raise RuntimeError(f"Native tool '{tool_name}' requires orchestration_run_id.")
    run = AgentRun.objects.select_related("agent", "workspace", "started_by").get(id=orchestration_run_id)
    tool_definition = (
        ToolDefinition.objects.select_related("tool")
        .filter(workspace_id=run.workspace_id, name=tool_name, enabled=True)
        .first()
    )
    try:
        validate_required_tool_arguments(tool_name, args or {}, definition=tool_definition)
    except ToolArgumentValidationError as exc:
        return {
            "ok": False,
            "result": exc.to_result(),
            "meta": {"native": True, "validation_failed": True, "required_parameters": exc.required_parameters},
            "error": str(exc),
        }

    if tool_name == "remember":
        scope_type = str(args.get("scope_type") or "").strip()
        scope_id = str(args.get("scope_id") or "").strip()
        record = remember_memory(
            scope_type=scope_type or None,
            scope_id=scope_id or None,
            memory_kind=str(args.get("memory_kind") or "semantic"),
            content=str(args.get("content") or ""),
            tags=list(args.get("tags") or []),
            importance=args.get("importance") or 0.5,
            summary=str(args.get("summary") or ""),
            pinned=_coerce_bool(args.get("pinned", False)),
            expires_at=_coerce_expires_at(args.get("expires_at")),
            dedupe_key=str(args.get("dedupe_key") or ""),
            dedupe_mode=str(args.get("dedupe_mode") or "auto"),
            source_kind=str(args.get("source_kind") or ""),
            source_ref=str(args.get("source_ref") or ""),
            agent=run.agent if not scope_type and not scope_id else None,
        )
        result = {
            "scope_type": record.scope_type,
            "scope_id": record.scope_id,
            "memory_kind": record.memory_kind,
            "memory_id": str(record.id),
            "dedupe_key": record.dedupe_key,
            "source_kind": record.source_kind,
            "source_ref": record.source_ref,
            "summary": record.summary,
            "tags": record.tags,
            "importance": str(record.importance),
            "pinned": record.pinned,
            "expires_at": record.expires_at.isoformat() if record.expires_at else "",
            "access_count": record.access_count,
            "last_accessed_at": record.last_accessed_at.isoformat() if record.last_accessed_at else "",
        }
        return {"ok": True, "result": result, "meta": {"native": True}, "error": None}

    if tool_name == "search_memory":
        records = search_memory_records(
            query=str(args.get("query") or ""),
            scope_type=str(args.get("scope_type") or "") or None,
            scope_id=str(args.get("scope_id") or "") or None,
            memory_kind=str(args.get("memory_kind") or "") or None,
            limit=int(args.get("limit") or 5),
            agent=run.agent if not args.get("scope_type") and not args.get("scope_id") else None,
        )
        result = {
            "query": str(args.get("query") or ""),
            "count": len(records),
            "results": [
                {
                    "memory_id": str(record.id),
                    "scope_type": record.scope_type,
                    "scope_id": record.scope_id,
                    "memory_kind": record.memory_kind,
                    "dedupe_key": record.dedupe_key,
                    "source_kind": record.source_kind,
                    "source_ref": record.source_ref,
                    "summary": record.summary,
                    "content": record.content[:800],
                    "tags": record.tags,
                    "importance": str(record.importance),
                    "pinned": record.pinned,
                    "expires_at": record.expires_at.isoformat() if record.expires_at else "",
                    "access_count": record.access_count,
                    "last_accessed_at": record.last_accessed_at.isoformat() if record.last_accessed_at else "",
                    "updated_at": record.updated_at.isoformat(),
                }
                for record in records
            ],
        }
        return {"ok": True, "result": result, "meta": {"native": True}, "error": None}

    if tool_name == "get_current_datetime":
        result = {
            "datetime": get_current_datetime_iso8601(),
            "timezone": get_tango_timezone_name(),
        }
        return {"ok": True, "result": result, "meta": {"native": True}, "error": None}

    if tool_name == "schedule_task":
        scheduled_task = create_scheduled_task(
            agent=run.agent,
            owner=run.started_by or run.agent.owner,
            task_type="other_task",
            local_time_value=str(args.get("local_time") or "08:00"),
            timezone_name=str(args.get("timezone") or get_local_timezone_name()),
            title=str(args.get("title") or ""),
            execution_payload=dict(args.get("execution_payload") or {}),
            execution_mode=str(args.get("execution_mode") or "headless_run"),
            recurrence_config=dict(args.get("recurrence") or args.get("recurrence_config") or {}),
        )
        result = {
            "scheduled_task_id": str(scheduled_task.id),
            "recurrence_rule_id": str(scheduled_task.recurrence_rule_id),
            "title": scheduled_task.title,
            "task_type": scheduled_task.task_type,
            "schedule_kind": scheduled_task.schedule_kind,
            "execution_mode": scheduled_task.execution_mode,
            "timezone": scheduled_task.timezone,
            "local_time": scheduled_task.local_time.isoformat(timespec="minutes"),
            "recurrence_frequency": scheduled_task.recurrence_rule.frequency,
            "recurrence_summary": scheduled_task.recurrence_summary,
            "next_run_at": scheduled_task.next_run_at.isoformat(),
            "enabled": scheduled_task.enabled,
            "source_memory_id": str(scheduled_task.source_memory_id or ""),
        }
        return {"ok": True, "result": result, "meta": {"native": True}, "error": None}

    if tool_name in {"get_scheduled_task", "edit_scheduled_task", "disable_scheduled_task", "enable_scheduled_task"}:
        scheduled_task_id = str(args.get("scheduled_task_id") or "").strip()
        if not scheduled_task_id:
            raise RuntimeError(f"{tool_name} requires scheduled_task_id.")
        scheduled_task = get_scheduled_task(agent=run.agent, scheduled_task_id=scheduled_task_id)
        if scheduled_task is None:
            raise RuntimeError("Scheduled task not found for the current agent.")
        if tool_name == "get_scheduled_task":
            return {"ok": True, "result": serialize_scheduled_task(scheduled_task, include_execution_payload=True), "meta": {"native": True}, "error": None}
        if tool_name == "edit_scheduled_task":
            updated = update_scheduled_task(
                scheduled_task,
                title=str(args.get("title")) if args.get("title") is not None else None,
                enabled=args.get("enabled") if args.get("enabled") is not None else None,
                execution_payload=dict(args.get("execution_payload") or {}) if args.get("execution_payload") is not None else None,
                recurrence_config=dict(args.get("recurrence") or args.get("recurrence_config") or {}) if args.get("recurrence") is not None or args.get("recurrence_config") is not None else None,
                local_time_value=str(args.get("local_time")) if args.get("local_time") is not None else None,
                timezone_name=str(args.get("timezone")) if args.get("timezone") is not None else None,
                delivery_target=str(args.get("delivery_target")) if args.get("delivery_target") is not None else None,
            )
        elif tool_name == "disable_scheduled_task":
            updated = disable_scheduled_task(scheduled_task)
        else:
            updated = enable_scheduled_task(scheduled_task)
        return {"ok": True, "result": serialize_scheduled_task(updated), "meta": {"native": True}, "error": None}

    if tool_name == "list_scheduled_tasks":
        scheduled_tasks = list_scheduled_tasks(
            agent=run.agent,
            enabled_only=_coerce_bool(args.get("enabled_only", False)),
            limit=int(args.get("limit") or 10),
        )
        include_execution_payload = _coerce_bool(args.get("include_execution_payload", False))
        result = {
            "count": len(scheduled_tasks),
            "results": [
                serialize_scheduled_task(task, include_execution_payload=include_execution_payload)
                for task in scheduled_tasks
            ],
        }
        return {"ok": True, "result": result, "meta": {"native": True}, "error": None}

    if tool_name == "spawn_subrun":
        result = run_subrun_flow(
            parent_run_id=str(run.id),
            input_text=str(args.get("input_text") or ""),
            metadata=dict(args.get("metadata") or {}),
            join_policy=str(args.get("join_policy") or "WAIT_ALL"),
            quorum=int(args["quorum"]) if args.get("quorum") not in (None, "") else None,
            timeout_seconds=int(args["timeout_seconds"]) if args.get("timeout_seconds") not in (None, "") else None,
            failure_policy=str(args.get("failure_policy") or "IGNORE_FAILURE"),
            group_id=str(args.get("group_id") or "") or None,
        )
        return {"ok": True, "result": result, "meta": {"native": True}, "error": None}

    if tool_name == "send_telegram":
        target = str(args.get("target") or "paired").strip().lower()
        if target != "paired":
            raise RuntimeError("send_telegram currently only supports target='paired'.")
        result = send_paired_telegram_message(
            run_id=str(run.id),
            text=str(args.get("text") or ""),
            name=str(args.get("name") or "") or None,
            parse_mode=str(args.get("parse_mode") or "") or None,
            disable_web_page_preview=args.get("disable_web_page_preview"),
            disable_notification=args.get("disable_notification"),
            reply_to_message_id=args.get("reply_to_message_id"),
            allow_sending_without_reply=args.get("allow_sending_without_reply"),
            protect_content=args.get("protect_content"),
            message_thread_id=args.get("message_thread_id"),
            reply_markup=dict(args.get("reply_markup") or {}) if args.get("reply_markup") is not None else None,
        )
        return {"ok": True, "result": result, "meta": {"native": True}, "error": None}

    if tool_name == "google_bridge":
        if _should_queue_google_cleanup_subrun(args):
            objective, prompt = build_google_task_objective(args or {})
            child = spawn_subrun(
                parent_run_id=str(run.id),
                input_text=prompt,
                metadata={
                    "integration_kind": "google",
                    "resource_kind": "gmail",
                    "operation": str(args.get("operation") or "trash").strip().lower(),
                    "background": True,
                    "summary": objective,
                },
                join_policy="WAIT_ALL",
                failure_policy="IGNORE_FAILURE",
                schedule_child=True,
                child_execution_mode=AgentRun.ExecutionMode.HEADLESS,
                block_parent=False,
            )
            summary_text = (
                f"Queued Gmail cleanup in background subrun {child.id}. "
                "Results will arrive asynchronously in the run timeline."
            )
            return {
                "ok": True,
                "result": {
                    "queued": True,
                    "background": True,
                    "parent_run_id": str(run.id),
                    "child_run_id": str(child.id),
                    "execution_mode": child.execution_mode,
                    "summary_text": summary_text,
                    "objective": objective,
                },
                "meta": {"native": True, "queued": True, "background_subrun": True},
                "error": None,
            }
        try:
            result = execute_google_task(
                payload=args or {},
                workspace=run.workspace,
                owner=run.started_by or run.agent.owner,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "result": None, "meta": {"native": True}, "error": str(exc)}
        return {"ok": bool(result.get("ok", True)), "result": result, "meta": {"native": True}, "error": None}

    raise RuntimeError(f"Unsupported native tool: {tool_name}")


def _should_queue_google_cleanup_subrun(args: Dict[str, Any]) -> bool:
    resource_kind = str(args.get("resource_kind") or "").strip().lower()
    operation = str(args.get("operation") or "").strip().lower()
    query = str(args.get("query") or "").strip()
    message_id = str(args.get("message_id") or "").strip()
    if resource_kind != "gmail":
        return False
    if operation not in {"trash", "delete"}:
        return False
    if message_id:
        return False
    return bool(query)


async def run_tool(
    tool_name: str, args: Dict[str, Any], *, orchestration_run_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute a tool call in ToolRunner.

    Returns:
        {
          "ok": bool,
          "result": Any,
          "meta": dict,
          "error": Optional[str],
        }
    """
    if tool_name in _NATIVE_TOOL_NAMES:
        try:
            return await sync_to_async(_run_native_tool, thread_sensitive=True)(tool_name, args or {}, orchestration_run_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "result": None, "meta": {"native": True}, "error": str(exc)}

    base_url = getattr(settings, "TOOLRUNNER_URL", None) or getattr(settings, "TOOLRUNNER_URL")
    base_url = base_url.rstrip("/")
    secret_value = getattr(settings, "TOOLRUNNER_SECRET", None) or getattr(settings, "TOOLRUNNER_SECRET", "insecure-secret")
    secret = secret_value.encode("utf-8")
    timeout = getattr(settings, "TOOLRUNNER_HTTP_TIMEOUT", None) or getattr(settings, "TOOLRUNNER_HTTP_TIMEOUT", 45)
    request_id = str(uuid.uuid4())
    workspace_id = str(orchestration_run_id or "llm-workspace")
    run_folder = orchestration_run_id or request_id
    run = None
    tool_definition = None
    if orchestration_run_id:
        from runs.models import AgentRun

        run = await sync_to_async(AgentRun.objects.select_related("agent", "workspace", "started_by").get)(id=orchestration_run_id)
        tool_definition = await sync_to_async(
            lambda: ToolDefinition.objects.select_related("tool")
            .filter(workspace_id=run.workspace_id, name=tool_name, enabled=True)
            .first()
        )()
        try:
            validate_required_tool_arguments(tool_name, args or {}, definition=tool_definition)
        except ToolArgumentValidationError as exc:
            return {
                "ok": False,
                "result": exc.to_result(),
                "meta": {
                    "request_id": request_id,
                    "validation_failed": True,
                    "required_parameters": exc.required_parameters,
                },
                "error": str(exc),
            }

    payload = {
        "request_id": request_id,
        "workspace_id": workspace_id,
        "run_id": run_folder,
        "tool_name": tool_name,
        "args": args or {},
        "limits": {
            "timeout_s": getattr(settings, "TOOLRUNNER_TIMEOUT", None)
            or getattr(settings, "TOOLRUNNER_TIMEOUT", 30),
            "max_output_bytes": getattr(settings, "TOOLRUNNER_OUTPUT_LIMIT", None)
            or getattr(settings, "TOOLRUNNER_OUTPUT_LIMIT", 4096),
        },
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp, signature = _sign(body, secret)
    headers = {
        "Content-Type": "application/json",
        "X-AM-Timestamp": timestamp,
        "X-AM-Signature": signature,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(base_url, content=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            return {
                "ok": False,
                "result": None,
                "meta": {"timeout_source": "TOOLRUNNER_HTTP_TIMEOUT", "timeout_seconds": timeout},
                "error": f"toolrunner request timed out (source=TOOLRUNNER_HTTP_TIMEOUT timeout_seconds={timeout})",
            }
        except Exception as exc:
            return {"ok": False, "result": None, "meta": {}, "error": str(exc)}

    status = data.get("status")
    exit_code = data.get("exit_code")
    stderr = data.get("stderr") or ""
    result_field = data.get("result") or {}
    if isinstance(result_field, dict) and "tool_result" in result_field:
        effective_result = result_field.get("tool_result")
    else:
        effective_result = result_field

    ok = status == "COMPLETED" and (exit_code is None or exit_code == 0)
    error = None
    if not ok:
        if isinstance(result_field, dict):
            error = result_field.get("error") or stderr or f"ToolRunner status={status} exit_code={exit_code}"
        else:
            error = stderr or f"ToolRunner status={status} exit_code={exit_code}"

    return {
        "ok": ok,
        "result": effective_result,
        "meta": {
            "request_id": request_id,
            "status": status,
            "exit_code": exit_code,
            "stdout": data.get("stdout") or "",
            "stderr": data.get("stderr") or "",
            "duration_ms": data.get("duration_ms"),
        },
        "error": error,
    }
