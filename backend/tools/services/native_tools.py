from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.services.timezones import get_current_datetime_iso8601, get_tango_timezone_name
from memory.scheduled_approvals import INTERNAL_HEADLESS_APPROVAL_TOOL_NAME
from memory.models import ScheduledTask
from memory.scheduled_tasks import (
    create_scheduled_task,
    disable_scheduled_task,
    enable_scheduled_task,
    list_scheduled_tasks,
    serialize_scheduled_task,
    update_scheduled_task,
)
from memory.services import remember as remember_memory
from memory.services import search_memory as search_memory_records
from runs.services.headless import continue_headless_run_after_approval_gate
from runs.services.subruns import run_subrun_flow
from tools.models import ToolCall


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


def execute_native_tool_call(tool_call: ToolCall) -> dict[str, object]:
    args = dict(tool_call.args or {})
    if tool_call.tool_name == "remember":
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
            agent=tool_call.run.agent if not scope_type and not scope_id else None,
        )
        return {
            "request_id": str(tool_call.id),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "result": {
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
            },
        }
    if tool_call.tool_name == "search_memory":
        records = search_memory_records(
            query=str(args.get("query") or ""),
            scope_type=str(args.get("scope_type") or "") or None,
            scope_id=str(args.get("scope_id") or "") or None,
            memory_kind=str(args.get("memory_kind") or "") or None,
            limit=int(args.get("limit") or 5),
        )
        return {
            "request_id": str(tool_call.id),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "result": {
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
            },
        }
    if tool_call.tool_name == "get_current_datetime":
        return {
            "request_id": str(tool_call.id),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "result": {
                "datetime": get_current_datetime_iso8601(),
                "timezone": get_tango_timezone_name(),
            },
        }
    if tool_call.tool_name == "schedule_task":
        scheduled_task = create_scheduled_task(
            agent=tool_call.run.agent,
            owner=tool_call.run.started_by or tool_call.run.agent.owner,
            task_type="other_task",
            local_time_value=str(args.get("local_time") or "08:00"),
            timezone_name=str(args.get("timezone") or "UTC"),
            title=str(args.get("title") or ""),
            execution_payload=dict(args.get("execution_payload") or {}),
            execution_mode=str(args.get("execution_mode") or "headless_run"),
            recurrence_config=dict(args.get("recurrence") or args.get("recurrence_config") or {}),
        )
        return {
            "request_id": str(tool_call.id),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "result": {**serialize_scheduled_task(scheduled_task), "source_memory_id": str(scheduled_task.source_memory_id or "")},
        }
    if tool_call.tool_name in {"edit_scheduled_task", "disable_scheduled_task", "enable_scheduled_task"}:
        scheduled_task_id = str(args.get("scheduled_task_id") or "").strip()
        if not scheduled_task_id:
            raise RuntimeError(f"{tool_call.tool_name} requires scheduled_task_id.")
        scheduled_task = (
            ScheduledTask.objects.select_related("recurrence_rule")
            .filter(id=scheduled_task_id, agent=tool_call.run.agent)
            .first()
        )
        if scheduled_task is None:
            raise RuntimeError("Scheduled task not found for the current agent.")
        if tool_call.tool_name == "edit_scheduled_task":
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
        elif tool_call.tool_name == "disable_scheduled_task":
            updated = disable_scheduled_task(scheduled_task)
        else:
            updated = enable_scheduled_task(scheduled_task)
        return {
            "request_id": str(tool_call.id),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "result": serialize_scheduled_task(updated),
        }
    if tool_call.tool_name == "list_scheduled_tasks":
        scheduled_tasks = list_scheduled_tasks(
            agent=tool_call.run.agent,
            enabled_only=bool(args.get("enabled_only", False)),
            limit=int(args.get("limit") or 10),
        )
        return {
            "request_id": str(tool_call.id),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "result": {
                "count": len(scheduled_tasks),
                "results": [serialize_scheduled_task(task) for task in scheduled_tasks],
            },
        }
    if tool_call.tool_name == "spawn_subrun":
        result = run_subrun_flow(
            parent_run_id=str(tool_call.run_id),
            input_text=str(args.get("input_text") or ""),
            metadata=dict(args.get("metadata") or {}),
            join_policy=str(args.get("join_policy") or "WAIT_ALL"),
            quorum=int(args["quorum"]) if args.get("quorum") not in (None, "") else None,
            timeout_seconds=int(args["timeout_seconds"]) if args.get("timeout_seconds") not in (None, "") else None,
            failure_policy=str(args.get("failure_policy") or "IGNORE_FAILURE"),
            group_id=str(args.get("group_id") or "") or None,
        )
        child_failed = bool(result.get("child_failed")) or str(result.get("child_status") or "").upper() == "FAILED"
        subrun_circuit_open = bool(result.get("subrun_circuit_open"))
        stderr = ""
        status = "COMPLETED"
        if child_failed:
            stderr = str(
                result.get("child_error_summary")
                or (result.get("child_failure") or {}).get("summary")
                or "Child subrun failed."
            ).strip()
        elif subrun_circuit_open:
            stderr = str(
                result.get("child_error_summary")
                or (result.get("child_failure") or {}).get("summary")
                or "Subruns are unavailable for the rest of this run."
            ).strip()
            status = "COMPLETED_WITH_WARNING"
        return {
            "request_id": str(tool_call.id),
            "status": status,
            "exit_code": 0,
            "stdout": "",
            "stderr": stderr,
            "duration_ms": 0,
            "result": result,
        }

    if tool_call.tool_name == "google_bridge":
        from google_bridge.services.bridge import execute_google_task

        try:
            result = execute_google_task(
                payload=args or {},
                workspace=tool_call.run.workspace,
                owner=tool_call.run.started_by or tool_call.run.agent.owner,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "request_id": str(tool_call.id),
                "status": "FAILED",
                "exit_code": 1,
                "stdout": "",
                "stderr": str(exc),
                "duration_ms": 0,
                "result": {"ok": False, "error": str(exc)},
            }
        return {
            "request_id": str(tool_call.id),
            "status": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "result": result,
        }

    if tool_call.tool_name == INTERNAL_HEADLESS_APPROVAL_TOOL_NAME:
        result = continue_headless_run_after_approval_gate(tool_call)
        if result.get("continued"):
            status = "COMPLETED"
            exit_code = 0
            stderr = ""
        else:
            status = "FAILED"
            exit_code = 1
            stderr = str(result.get("error") or "scheduled headless approval drifted")
        return {
            "request_id": str(tool_call.id),
            "status": status,
            "exit_code": exit_code,
            "stdout": "",
            "stderr": stderr,
            "duration_ms": 0,
            "result": result,
        }
    raise RuntimeError(f"Unsupported native tool: {tool_call.tool_name}")
