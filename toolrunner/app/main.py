from __future__ import annotations

import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ValidationError

from .auth import verify_signature
from .models import (
    CoverageArgs,
    ExecuteRequest,
    ExecuteResponse,
    FileDeleteArgs,
    FilePatchArgs,
    FileReadArgs,
    FileWriteArgs,
    FormatArgs,
    GitAddArgs,
    GitApplyArgs,
    GitBranchCreateArgs,
    GitCheckoutArgs,
    GitCommitArgs,
    GitDiffArgs,
    GitLogArgs,
    GitPushArgs,
    GitStatusArgs,
    LintArgs,
    PythonArgs,
    RepoTreeArgs,
    RunCommandArgs,
    RunnerTestArgs,
    SearchCodeArgs,
    ShellArgs,
    TypecheckArgs,
)
from .run_manager import REPO_ROOT, RunContext, RunManager
from .schemas import SchemaValidationError
from .srs.readiness import compute_readiness, ensure_readiness, READINESS_SCORE_THRESHOLD
from .sandbox import get_run_dir
from .tools import (
    apply_patch,
    delete_file,
    create_webhook,
    run_coverage,
    run_formatter,
    run_git_add,
    run_git_apply,
    run_git_branch_create,
    run_git_checkout,
    run_git_commit,
    run_git_diff,
    run_git_log,
    run_git_push,
    run_git_status,
    run_linters,
    list_repo_tree,
    list_search_code,
    read_file,
    run_command,
    run_python,
    run_shell,
    run_tests,
    run_typecheck,
    write_file,
)
from .planning.plan_compiler import PlanCompilerError, compile_plan
from .verify import read_run_metadata, run_post_run_verification


def _make_execute_response(
        request_id: str,
        status_text: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        duration_ms: int,
        result: dict | None,
) -> ExecuteResponse:
    return ExecuteResponse(
        request_id=request_id,
        status=status_text,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        result=result,
    )


def _json_error_response(
        code: str,
        message: str,
        details: dict[str, object] | None = None,
        *,
        status_code: int = 400,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": f"tool_runner.{code}",
                "message": message,
                "details": details or {},
            },
        },
    )


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(sub_value) for key, sub_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _validation_error_details(exc: ValidationError) -> list[dict[str, object]]:
    return _json_safe(exc.errors())  # type: ignore[return-value]


def _normalize_command_result(
        tool_name: str,
        command: Sequence[str],
        cwd: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        *,
        timed_out: bool,
        timeout_value: float | int | None = None,
        timeout_unit: str = "seconds",
        timeout_source: str | None = None,
) -> dict[str, object]:
    result = {
        "command": list(command),
        "cwd": cwd,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "timeout_value": timeout_value,
        "timeout_unit": timeout_unit,
        "timeout_source": timeout_source,
    }
    if timed_out:
        timeout_message = f"{tool_name} timed out"
        if timeout_value is not None:
            timeout_message = (
                f"{tool_name} timed out after {timeout_value} {timeout_unit}"
                f" (source={timeout_source or 'unknown'})"
            )
        return {
            "ok": False,
            "error": {
                "code": "tool_runner.TIMED_OUT",
                "message": timeout_message,
                "details": {
                    "tool": tool_name,
                    "command": list(command),
                    "cwd": cwd,
                    "timeout_value": timeout_value,
                    "timeout_unit": timeout_unit,
                    "timeout_source": timeout_source,
                },
            },
            "result": result,
        }
    if exit_code not in (0, None):
        return {
            "ok": False,
            "error": {
                "code": "tool_runner.COMMAND_FAILED",
                "message": f"{tool_name} exited with code {exit_code}",
                "details": {"tool": tool_name, "command": list(command), "cwd": cwd, "exit_code": exit_code},
            },
            "result": result,
        }
    return {"ok": True, "result": result}


def _run_shell_exec_tool(run_dir: Path, parsed_args: ShellArgs, payload: ExecuteRequest) -> dict[str, object]:
    exit_code, stdout, stderr, working_dir = run_shell(
        run_dir,
        parsed_args.cmd,
        parsed_args.cwd,
        payload.limits.timeout_s,
        payload.limits.max_output_bytes,
        env=parsed_args.env,
        policy=payload.policy,
    )
    return _normalize_command_result(
        "shell_exec",
        parsed_args.cmd,
        str(working_dir),
        exit_code,
        stdout,
        stderr,
        timed_out=exit_code is None,
        timeout_value=payload.limits.timeout_s,
        timeout_unit="seconds",
        timeout_source="limits.timeout_s",
    )


def _run_python_exec_tool(run_dir: Path, parsed_args: PythonArgs, payload: ExecuteRequest) -> dict[str, object]:
    exit_code, stdout, stderr = run_python(
        run_dir,
        parsed_args,
        payload.limits.timeout_s,
        payload.limits.max_output_bytes,
        payload.policy,
    )
    command = [str(parsed_args.entrypoint)] if parsed_args.entrypoint else [payload.tool_name, "<inline_code>"]
    return _normalize_command_result(
        "python_exec",
        command,
        str(run_dir.resolve()),
        exit_code,
        stdout,
        stderr,
        timed_out=exit_code is None,
        timeout_value=payload.limits.timeout_s,
        timeout_unit="seconds",
        timeout_source="limits.timeout_s",
    )


_TOOL_HANDLERS: dict[str, tuple[type[BaseModel], object]] = {
    "file_read": (FileReadArgs, lambda run_dir, parsed_args, payload: read_file(run_dir, parsed_args, payload.policy)),
    "file_write": (FileWriteArgs, lambda run_dir, parsed_args, payload: write_file(run_dir, parsed_args, payload.policy)),
    "file_delete": (FileDeleteArgs, lambda run_dir, parsed_args, payload: delete_file(run_dir, parsed_args, payload.policy)),
    "file_patch": (FilePatchArgs, lambda run_dir, parsed_args, payload: apply_patch(run_dir, parsed_args, payload.policy)),
    "repo_tree": (RepoTreeArgs, lambda run_dir, parsed_args, payload: list_repo_tree(run_dir, parsed_args, payload.policy)),
    "search_code": (SearchCodeArgs, lambda run_dir, parsed_args, payload: list_search_code(run_dir, parsed_args, payload.policy)),
    "shell_exec": (ShellArgs, _run_shell_exec_tool),
    "python_exec": (PythonArgs, _run_python_exec_tool),
    "run_command": (RunCommandArgs, lambda run_dir, parsed_args, payload: run_command(run_dir, parsed_args, payload.policy)),
    "test_runner": (RunnerTestArgs, lambda run_dir, parsed_args, payload: run_tests(run_dir, parsed_args, payload.policy)),
    "format_runner": (FormatArgs, lambda run_dir, parsed_args, payload: run_formatter(run_dir, parsed_args, payload.policy)),
    "coverage_runner": (CoverageArgs, lambda run_dir, parsed_args, payload: run_coverage(run_dir, parsed_args, payload.policy)),
    "lint_runner": (LintArgs, lambda run_dir, parsed_args, payload: run_linters(run_dir, parsed_args, payload.policy)),
    "typecheck_runner": (TypecheckArgs, lambda run_dir, parsed_args, payload: run_typecheck(run_dir, parsed_args, payload.policy)),
    "git_status": (GitStatusArgs, lambda run_dir, parsed_args, payload: run_git_status(run_dir, parsed_args, payload.policy)),
    "git_diff": (GitDiffArgs, lambda run_dir, parsed_args, payload: run_git_diff(run_dir, parsed_args, payload.policy)),
    "git_log": (GitLogArgs, lambda run_dir, parsed_args, payload: run_git_log(run_dir, parsed_args, payload.policy)),
    "git_apply": (GitApplyArgs, lambda run_dir, parsed_args, payload: run_git_apply(run_dir, parsed_args, payload.policy)),
    "git_branch_create": (GitBranchCreateArgs, lambda run_dir, parsed_args, payload: run_git_branch_create(run_dir, parsed_args, payload.policy)),
    "git_checkout": (GitCheckoutArgs, lambda run_dir, parsed_args, payload: run_git_checkout(run_dir, parsed_args, payload.policy)),
    "git_commit": (GitCommitArgs, lambda run_dir, parsed_args, payload: run_git_commit(run_dir, parsed_args, payload.policy)),
    "git_push": (GitPushArgs, lambda run_dir, parsed_args, payload: run_git_push(run_dir, parsed_args, payload.policy)),
    "git_add": (GitAddArgs, lambda run_dir, parsed_args, payload: run_git_add(run_dir, parsed_args, payload.policy)),
}


def _tool_status(tool_result: dict[str, object]) -> tuple[bool, int | None, str, str]:
    result = tool_result.get("result") if isinstance(tool_result, dict) else {}
    result = result if isinstance(result, dict) else {}
    error = tool_result.get("error") if isinstance(tool_result, dict) else {}
    error = error if isinstance(error, dict) else {}
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    if not stderr and error.get("message"):
        stderr = str(error.get("message"))
    exit_code = result.get("exit_code")
    timed_out = bool(result.get("timed_out"))
    command_failed = timed_out or isinstance(exit_code, int) and exit_code != 0
    success = bool(tool_result.get("ok")) and not command_failed
    return success, exit_code if isinstance(exit_code, int) else None, stdout, stderr


def _apply_execute_limits(parsed_args: BaseModel, payload: ExecuteRequest) -> BaseModel:
    updates: dict[str, int] = {}
    model_fields = type(parsed_args).model_fields
    limit_timeout_ms = payload.limits.timeout_s * 1000

    if "timeout_ms" in model_fields:
        configured_timeout = getattr(parsed_args, "timeout_ms", None)
        if not isinstance(configured_timeout, int) or configured_timeout <= 0:
            updates["timeout_ms"] = limit_timeout_ms
        else:
            updates["timeout_ms"] = min(configured_timeout, limit_timeout_ms)

    if "max_output_bytes" in model_fields:
        configured_output_limit = getattr(parsed_args, "max_output_bytes", None)
        limit_output_bytes = payload.limits.max_output_bytes
        if not isinstance(configured_output_limit, int) or configured_output_limit <= 0:
            updates["max_output_bytes"] = limit_output_bytes
        else:
            updates["max_output_bytes"] = min(configured_output_limit, limit_output_bytes)

    if not updates:
        return parsed_args
    return parsed_args.model_copy(update=updates)


async def _handle_execute(payload: ExecuteRequest) -> ExecuteResponse:
    run_dir = get_run_dir(payload.workspace_id, payload.run_id)

    stdout = ""
    stderr = ""
    exit_code: int | None = None
    duration_ms = 0
    result: dict[str, object] = {"tool": payload.tool_name}
    if payload.policy:
        result["policy"] = payload.policy
    start = time.monotonic()
    success = False
    tool_result: dict[str, object] | None = None

    try:
        handler_spec = _TOOL_HANDLERS.get(payload.tool_name)
        if handler_spec is None:
            stderr = f"unsupported tool: {payload.tool_name}"
            result["tool_result"] = {
                "ok": False,
                "error": {
                    "code": "tool_runner.UNSUPPORTED_TOOL",
                    "message": stderr,
                    "details": {"tool": payload.tool_name},
                },
            }
        else:
            args_model, handler = handler_spec
            try:
                parsed_args = args_model(**payload.args)
                parsed_args = _apply_execute_limits(parsed_args, payload)
                raw_tool_result = handler(run_dir, parsed_args, payload)
                tool_result = _normalize_tool_result(raw_tool_result)
                success, exit_code, stdout, stderr = _tool_status(tool_result)
            except ValidationError as exc:
                stderr = "tool argument validation failed"
                tool_result = {
                    "ok": False,
                    "error": {
                        "code": "tool_runner.INVALID_ARGUMENT",
                        "message": stderr,
                        "details": {"tool": payload.tool_name, "errors": _validation_error_details(exc)},
                    },
                }
            except (ValueError, FileNotFoundError) as exc:
                stderr = str(exc)
                tool_result = {
                    "ok": False,
                    "error": {
                        "code": "tool_runner.INVALID_ARGUMENT",
                        "message": stderr,
                        "details": {"tool": payload.tool_name},
                    },
                }
    except Exception as exc:  # pragma: no cover
        stderr = str(exc)
        tool_result = {
            "ok": False,
            "error": {
                "code": "tool_runner.INTERNAL",
                "message": stderr or "unexpected toolrunner exception",
                "details": {"tool": payload.tool_name},
            },
        }
    finally:
        duration_ms = int(round((time.monotonic() - start) * 1000))

    if tool_result is not None:
        result["tool_result"] = tool_result
    status_text = "COMPLETED" if success else "FAILED"
    if not success and stderr and "error" not in result:
        result["error"] = stderr

    return _make_execute_response(
        payload.request_id,
        status_text,
        exit_code,
        stdout,
        stderr,
        duration_ms,
        result,
    )


def _normalize_tool_result(tool_result: object) -> object:
    if isinstance(tool_result, JSONResponse):
        return _extract_json_response(tool_result)
    return tool_result


def _extract_json_response(response: JSONResponse) -> dict:
    content = response.body
    if content is None:
        return {"ok": False, "error": {"code": "tool_runner.EMPTY_RESPONSE", "message": "handler returned empty JSONResponse", "details": {}}}
    if isinstance(content, bytes):
        try:
            decoded = content.decode(response.charset or "utf-8")
            return json.loads(decoded)
        except Exception:
            return {
                "ok": False,
                "error": {
                    "code": "tool_runner.INVALID_TOOL_RESPONSE",
                    "message": "failed to parse JSONResponse body",
                    "details": {"status_code": response.status_code},
                },
            }
    return {
        "ok": False,
        "error": {
            "code": "tool_runner.INVALID_TOOL_RESPONSE",
            "message": "unexpected JSONResponse body type",
            "details": {"status_code": response.status_code},
        },
    }


class RunCreateRequest(BaseModel):
    repo_dir: str = "."
    slug: str
    srs_path: Optional[str] = None


class TrivialTrialRequest(BaseModel):
    repo_dir: Optional[str] = None
    slug: Optional[str] = None
    start: bool = True
    override_readiness: bool = True
    template: str = "todo_cli_v1"


class SRSSectionUpdate(BaseModel):
    content: str
    action: Literal["draft", "lock"]


class ApprovalRequest(BaseModel):
    step_id: str
    milestone_id: str
    decision: Literal["approve", "deny"]
    scope: Literal["once", "path"]
    path: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    mode: str = "srs_builder"


TRIVIAL_TRIAL_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "todo_cli_v1": [
        (
            "project_summary",
            "Build a tiny Python CLI app `todo` that supports add, list, done, saves tasks in a local JSON file, and ships with pytest unit tests for every command.",
        ),
        (
            "goals_non_goals",
            "Goals:\n- Implement add/list/done CLI commands backed by JSON persistence.\n- Keep help text clean and guidance focused.\n- Provide pytest coverage for the primary flows.\nNon-goals:\n- No web UI, external database, sync, or authentication.",
        ),
        (
            "functional_requirements",
            "- `python -m todo add \"text\"` appends a task with an integer ID and status open.\n- `python -m todo list` prints each task with id/status/text lines.\n- `python -m todo done <id>` marks the task completed and updates local `todo_data.json` (or `data/todo.json`).\n- Invalid arguments or unknown IDs exit with a non-zero status so automation can detect failures.",
        ),
        (
            "interfaces",
            "CLI entry point via `python -m todo`. Commands: add, list, done, plus `--help`. Output is plain text showing the identifier, status, and description for each task.",
        ),
        (
            "acceptance_criteria",
            "- `pytest -q` finishes successfully.\n- Running add then list shows the new task; running done updates its status.\n- Repository stays clean after the run and the feature branch commit exists.",
        ),
        (
            "risks_assumptions",
            "- Windows permissions may block temp/artifact folders; assume the workspace allows writing under `.agentmaestro` and `scripts`.\n- The existing `scripts/test.ps1` entry point can run inside the cloned repo and Python CLI tooling remains functional.",
        ),
    ],
}

TRIVIAL_TRIAL_ALLOWED_TOOLS = {
    "tier1": [
        "file_write",
        "git_add",
        "git_commit",
        "repo_tree",
        "format_runner",
        "lint_runner",
        "typecheck_runner",
        "test_runner",
        "run_command",
    ],
    "tier2": [],
    "git": ["git_status"],
}

run_manager = RunManager()
UI_RUN_CONTEXT = run_manager.create_run("ui")
UI_RUN_ID = UI_RUN_CONTEXT.run_id

COMPLETENESS_SECTIONS = [
    ("project_summary", "Project Summary"),
    ("goals_non_goals", "Goals & Non-Goals"),
    ("functional_requirements", "Functional Requirements"),
    ("acceptance_criteria", "Acceptance Criteria"),
    ("risks_assumptions", "Risks & Assumptions"),
]

app = FastAPI()


@app.middleware("http")
async def read_body(request: Request, call_next):
    request.state._body = await request.body()
    return await call_next(request)


@app.post("/v1/run/tool")
async def run_tool_endpoint(request: Request, raw=Depends(verify_signature)):
    try:
        payload_data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return _json_error_response("INVALID_JSON", "request body is not valid JSON", {"error": str(exc)}, status_code=400)
    try:
        payload = ExecuteRequest(**payload_data)
    except ValidationError as exc:
        return _json_error_response(
            "INVALID_REQUEST",
            "request validation failed",
            {
                "errors": _validation_error_details(exc),
                "tool": payload_data.get("tool_name"),
                "request_id": payload_data.get("request_id"),
            },
            status_code=422,
        )
    response = await _handle_execute(payload)
    return response


@app.post("/v1/run/tool/webhook")
async def run_tool_webhook_endpoint(request: Request, raw=Depends(verify_signature)):
    payload = json.loads(raw.decode("utf-8"))
    return create_webhook(payload)


# Legacy aliases (to be removed once clients migrate)
@app.post("/v1/execute")
async def execute(request: Request, raw=Depends(verify_signature)):
    return await run_tool_endpoint(request, raw)


@app.post("/v1/webhook")
async def webhook_endpoint(request: Request, raw=Depends(verify_signature)):
    return await run_tool_webhook_endpoint(request, raw)


def _get_run_context(run_id: str) -> RunContext:
    try:
        return run_manager.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _plan_dir(context: RunContext) -> Path:
    directory = context.run_root / "plans"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _verification_paths(context: RunContext) -> tuple[Path, Path, Path]:
    directory = context.run_root / "verify"
    directory.mkdir(parents=True, exist_ok=True)
    return directory, directory / "verification.json", directory / "verification.md"


def _run_status_summary(context: RunContext) -> dict[str, Any]:
    return {
        "run_id": context.run_id,
        "status": context.statuses.get("status"),
        "reason": context.statuses.get("reason"),
        "last_event_id": context.event_logger.last_id(),
        "run_root": str(context.run_root),
    }


def _step_reports_for_run(context: RunContext) -> list[dict[str, str]]:
    reports_dir = context.run_root / "step_reports"
    if not reports_dir.exists():
        return []
    entries: list[dict[str, str]] = []
    for milestone_dir in sorted(reports_dir.iterdir()):
        if not milestone_dir.is_dir():
            continue
        for report_file in sorted(milestone_dir.glob("*.json")):
            entries.append(
                {
                    "milestone_id": milestone_dir.name,
                    "step_id": report_file.stem,
                    "path": str(report_file.relative_to(context.run_root)),
                }
            )
    return entries


def _append_chat_message(context: RunContext, role: str, content: str, meta: dict[str, Any] | None = None) -> dict[
    str, Any]:
    message = context.chat_transcript.append(role, content, meta or {})
    context.event_logger.log(
        "CHAT_MESSAGE",
        {
            "run_id": context.run_id,
            "role": role,
            "message_id": message["id"],
            "content": content[:240],
        },
    )
    return message


def _apply_srs_updates(context: RunContext, updates: Sequence[dict[str, Any]] | None) -> list[dict[str, str]]:
    applied: list[dict[str, str]] = []
    if not updates:
        return applied
    locked_applied = False
    for update in updates:
        section_id = update.get("section_id")
        action = update.get("action")
        content = (update.get("content") or "").strip()
        if not section_id or not action:
            continue
        if action == "draft":
            if not content:
                continue
            context.srs_drafts[section_id] = content
            context.event_logger.log(
                "SRS_UPDATED",
                {"run_id": context.run_id, "section_id": section_id, "action": "draft"},
            )
            applied.append({"section_id": section_id, "action": "draft"})
            continue

        if action == "lock":
            payload = content or context.srs_drafts.get(section_id, "")
            if not payload:
                continue
            try:
                locked = context.srs_builder.record_section(section_id, payload)
            except ValueError:
                continue
            context.srs_builder.save()
            context.srs_drafts.pop(section_id, None)
            context.event_logger.log(
                "SRS_UPDATED",
                {"run_id": context.run_id, "section_id": section_id, "action": "lock"},
            )
            context.event_logger.log(
                "SRS_SECTION_LOCKED",
                {
                    "run_id": context.run_id,
                    "section_id": section_id,
                    "sha256": locked.get("sha256"),
                },
            )
            applied.append({"section_id": section_id, "action": "lock"})
            locked_applied = True
    if locked_applied:
        compute_readiness(context)
    return applied


def _srs_preview_data(context: RunContext) -> dict[str, Any]:
    builder = context.srs_builder
    return {
        "md": builder.render_srs(),
        "locked_sections": list(builder.locked_sections.keys()),
    }


def _render_user_partial(run_id: str, section_id: Optional[str] = None, log_prompt: bool = True) -> str:
    context = _get_run_context(run_id)
    builder = context.srs_builder
    preview = _srs_preview_data(context)
    locked_sections = set(preview["locked_sections"])
    messages, _ = context.chat_transcript.read_since(0)
    message_blocks: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        role_label = "Maestro" if role == "maestro" else "User"
        badge_class = "badge-maestro" if role == "maestro" else "badge-user"
        timestamp = html.escape(message.get("ts", ""))
        content = html.escape(message.get("content", ""))
        meta: dict[str, Any] = message.get("meta") or {}
        meta_html = ""
        if role == "maestro":
            questions = meta.get("questions", [])
            if questions:
                items = "".join(f"<li>{html.escape(str(question))}</li>" for question in questions)
                meta_html += f'<div class="chat-meta"><strong>Questions:</strong><ul>{items}</ul></div>'
            srs_updates = meta.get("srs_updates", [])
            if srs_updates:
                updates = "".join(
                    f"<li>{html.escape(str(update.get('section_id', '')))}: {html.escape(str(update.get('action', '')))}</li>"
                    for update in srs_updates
                )
                meta_html += f'<div class="chat-meta"><strong>SRS updates:</strong><ul>{updates}</ul></div>'
            if meta.get("requires_user_decision"):
                meta_html += '<div class="chat-meta decision">Requires your approval.</div>'
        block = (
            f'<div class="chat-message {html.escape(role)}" data-message-id="{message.get("id", 0)}">'
            f"<div class=\"chat-message-header\">"
            f"<span class=\"chat-badge {badge_class}\">{role_label}</span>"
            f"<span class=\"chat-ts\">{timestamp}</span>"
            f"</div>"
            f"<div class=\"chat-message-body\">{content}</div>"
            f"{meta_html}"
            f"</div>"
        )
        message_blocks.append(block)
    if not message_blocks:
        message_blocks.append(
            '<div class="chat-empty">No conversation yet — say hello to Maestro to kick off the SRS.</div>'
        )
    completeness_items: list[str] = []
    for section_key, title in COMPLETENESS_SECTIONS:
        locked = section_key in locked_sections
        status_label = "Locked" if locked else "Pending"
        status_class = "completeness-locked" if locked else "completeness-pending"
        badge_state = "badge-locked" if locked else "badge-draft"
        completeness_items.append(
            f'<li class="completeness-item {status_class}" data-section-id="{html.escape(section_key)}">'
            f'<span class="completeness-title">{html.escape(title)}</span>'
            f'<span class="badge {badge_state}">{status_label}</span>'
            f"</li>"
        )
    events, _ = context.event_logger.read_since(0)
    recent_updates: list[dict[str, str]] = []
    for event in reversed(events):
        if event.get("type") != "SRS_UPDATED":
            continue
        data = event.get("data") or {}
        section_id = data.get("section_id")
        action = data.get("action")
        if not section_id or not action:
            continue
        recent_updates.append({"section_id": section_id, "action": action})
        if len(recent_updates) >= 3:
            break
    recent_updates_html = "".join(
        f'<li>{html.escape(update["section_id"])}: {html.escape(update["action"])}</li>'
        for update in recent_updates
    )
    updates_payload = json.dumps(recent_updates, ensure_ascii=False).replace("</script", "<\\/script")
    preview_markdown = (preview.get("md", "") or "").strip() or "No SRS content yet."
    return f"""
<div class="user-chat">
  <div class="chat-layout">
    <div class="chat-column">
      <div class="chat-header">
        <div>
          <h2>Maestro Chat</h2>
          <p class="chat-subtitle">Discuss the SRS with Maestro and draft/lock sections conversationally.</p>
        </div>
      </div>
      <div id="chat-messages" class="chat-messages">
        {''.join(message_blocks)}
      </div>
      <form id="chat-form" class="chat-form">
        <textarea id="chat-input" placeholder="Send a message to Maestro..." rows="3"></textarea>
        <div class="chat-actions">
          <button type="submit" id="chat-send-button">Send</button>
        </div>
        <div id="chat-status" class="chat-status" aria-live="polite"></div>
      </form>
    </div>
    <div class="srs-column">
      <div class="srs-preview-card">
        <h3>SRS Live Preview</h3>
        <pre id="srs-preview">{html.escape(preview_markdown)}</pre>
      </div>
      <div class="srs-completeness-card">
        <h4>SRS Completeness Meter</h4>
        <ul id="completeness-list">
          {''.join(completeness_items)}
        </ul>
      </div>
      <div class="recent-updates-card">
        <h4>Recent SRS Updates</h4>
        <ul id="recent-updates-list">
          {recent_updates_html or '<li>No updates yet.</li>'}
        </ul>
      </div>
    </div>
  </div>
  <script id="recent-updates-data" type="application/json">
    {updates_payload}
  </script>
</div>
"""


def _render_maestro_partial(run_id: str) -> str:
    context = _get_run_context(run_id)
    content = "<p>No plan has been generated yet.</p>"
    plan_data: dict[str, Any] = {}
    if context.latest_plan_id:
        plan_path = context.run_root / "plans" / f"{context.latest_plan_id}.json"
        if plan_path.exists():
            try:
                plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                plan_data = {}
    milestones = plan_data.get("milestones", [])
    milestone_html = []
    for ms in milestones:
        steps = ms.get("steps", [])
        steps_html = []
        for step in steps:
            badges = []
            if step.get("requires_approval"):
                badges.append('<span class="badge badge-approval">Approval required</span>')
            elif step.get("risk_tags"):
                tags = ", ".join(html.escape(str(tag)) for tag in step.get("risk_tags", []))
                badges.append(f'<span class="badge badge-risk">Risk tags: {tags}</span>')
            badge_markup = " ".join(badges)
            steps_html.append(
                f"<li>{html.escape(step.get('step_id', ''))}: {html.escape(step.get('intent', ''))} {badge_markup}</li>"
            )
        milestone_html.append(
            f"<h4>{html.escape(ms.get('title', ''))}</h4><ul>{''.join(steps_html)}</ul>"
        )
    plan_data = plan_data if "plan_data" in locals() else {}
    plan_json = html.escape(json.dumps(plan_data, indent=2))
    goal_html = f"<p><strong>Goal:</strong> {html.escape(plan_data.get('goal', ''))}</p>"
    content = (
        f"{goal_html}"
        f"{''.join(milestone_html)}"
        f'<details><summary>Raw plan JSON</summary><pre>{plan_json}</pre></details>'
    )
    readiness = ensure_readiness(context)
    readiness_score = readiness.get("score", 0)
    readiness_missing = readiness.get("missing", [])
    readiness_warnings = readiness.get("warnings", [])
    missing_html = "".join(f"<li>{html.escape(item)}</li>" for item in readiness_missing) or "<li>None</li>"
    warnings_html = "".join(f"<li>{html.escape(item)}</li>" for item in readiness_warnings) or "<li>None</li>"
    score_class = "ready" if readiness_score >= READINESS_SCORE_THRESHOLD else "pending"
    readiness_card = f"""
  <div class="readiness-card readiness-{score_class}">
    <div class="readiness-score">
      <span class="readiness-label">Readiness Score</span>
      <span id="readiness-score" class="readiness-score-value">{readiness_score}</span>/100
    </div>
    <div class="readiness-progress">
      <span style="width:{readiness_score}%;"></span>
    </div>
    <div class="readiness-details">
      <div>
        <h5>Missing</h5>
        <ul id="readiness-missing">{missing_html}</ul>
      </div>
      <div>
        <h5>Warnings</h5>
        <ul id="readiness-warnings">{warnings_html}</ul>
      </div>
    </div>
    <label class="override-gate">
      <input type="checkbox" id="override-readiness" />
      Override readiness gate
    </label>
    <p id="readiness-gate-message" class="readiness-gate-message"></p>
  </div>
"""
    generate_disabled = "" if readiness_score >= READINESS_SCORE_THRESHOLD else "disabled"
    return f"""
<div class="maestro-tab">
  {readiness_card}
  <div class="maestro-actions">
    <button id="generate-plan-btn" data-base-url="/v1/runs/{run_id}/plan/generate" {generate_disabled} hx-post="/v1/runs/{run_id}/plan/generate" hx-target="#maestro-tab-content" hx-swap="outerHTML">Generate Plan</button>
    <button hx-get="/ui/partials/maestro?run_id={run_id}" hx-target="#maestro-tab-content" hx-swap="innerHTML">Refresh</button>
  </div>
  <div class="plan-summary">
    {content}
  </div>
</div>
"""


def _render_apprentice_partial(run_id: str) -> str:
    return f"""
<div class="apprentice-tab">
  <div class="apprentice-actions">
    <button hx-post="/v1/runs/{run_id}/start" hx-target="#run-control-status" hx-swap="innerHTML">Start Run</button>
    <button hx-post="/v1/runs/{run_id}/stop" hx-target="#run-control-status" hx-swap="innerHTML">Stop Run</button>
  </div>
  <div id="run-control-status" class="run-control-status"></div>
  <div id="stuck-banner" class="stuck-banner"></div>
  <div class="apprentice-columns">
    <div class="event-section">
      <h3>Event Feed</h3>
      <div id="event-feed" class="event-feed"></div>
    </div>
  <div class="reports-panel">
      <h3>Step Reports</h3>
      <ul id="step-report-list" class="report-list"></ul>
      <h4>Report Viewer</h4>
      <pre id="step-report-viewer" class="report-viewer"><em>Select a report to view JSON.</em></pre>
      <div class="verification-panel">
        <h3>Post-Run Verification</h3>
        <div id="verification-summary" class="verification-summary">
          <div class="verification-overview">
            <span id="verification-status">Verification pending</span>
            <span id="verification-score"></span>
          </div>
          <div class="verification-flags">
            <span id="verification-autonomy" class="verification-flag">Autonomy: pending</span>
            <span id="verification-hygiene" class="verification-flag">Hygiene: pending</span>
          </div>
          <ul id="verification-findings" class="verification-findings">
            <li>No verification data yet.</li>
          </ul>
          <ul id="verification-warnings" class="verification-warnings">
            <li>No warnings.</li>
          </ul>
        </div>
        <div class="verification-actions">
          <button id="verification-json-btn">View verification.json</button>
          <button id="verification-md-btn">View verification.md</button>
        </div>
        <pre id="verification-viewer" class="verification-viewer"><em>Verification output appears here.</em></pre>
      </div>
    </div>
  </div>
  <div id="approval-modal" class="approval-modal">
    <div class="modal-content">
      <h4>Approval Requested</h4>
      <p id="approval-description"></p>
      <div style="display:flex;gap:0.5rem;">
        <button id="approval-approve" style="background:#0fbc9c;">Approve</button>
        <button id="approval-deny" style="background:#ff3864;">Deny</button>
      </div>
      <button id="approval-close" style="background:#394667;">Close</button>
    </div>
  </div>
</div>
"""


_CONSOLE_HTML = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>ToolRunner Console</title>
    <style>
      :root {
        color-scheme: dark;
      }
      body {
        margin: 0;
        font-family: "JetBrains Mono", "Fira Mono", SFMono-Regular, Menlo, Consolas, monospace;
        background: #05070c;
        color: #f5f6fa;
      }
      .console-shell {
        display: flex;
        flex-direction: column;
        height: 100vh;
      }
      .console-toolbar {
        padding: 0.75rem 1rem;
        border-bottom: 1px solid #1b1f2d;
        display: flex;
        align-items: center;
        gap: 0.75rem;
        background: #0f1119;
      }
      .console-toolbar button {
        background: #394667;
        color: #f5f6fa;
        border: 1px solid #1b1f2d;
        padding: 0.35rem 0.75rem;
        border-radius: 0.35rem;
        cursor: pointer;
      }
      .console-toolbar button:hover {
        background: #54648c;
      }
      .console-toolbar label {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        font-size: 0.9rem;
      }
      #console-status {
        margin-left: auto;
        font-size: 0.85rem;
        color: #9aa7c6;
      }
      .console-terminal {
        flex: 1;
        overflow-y: auto;
        padding: 1rem;
        background: #03050a;
        line-height: 1.4;
        border: none;
      }
      .line {
        padding: 0.25rem 0;
        display: flex;
        align-items: baseline;
        gap: 0.5rem;
      }
      .line .summary {
        flex: 1;
      }
      .line .details {
        color: #58c7ff;
        font-size: 0.85rem;
        text-decoration: none;
        border-bottom: 1px dotted #58c7ff;
      }
      .line.level-error {
        color: #ff6b6b;
      }
      .line.level-tool {
        color: #4ce0c3;
      }
      .line.level-info {
        color: #e5e9ff;
      }
    </style>
  </head>
  <body>
    <div class="console-shell">
      <div class="console-toolbar">
        <label><input type="checkbox" id="auto-scroll" checked /> Auto-scroll</label>
        <button id="clear-btn">Clear console</button>
        <button id="copy-btn">Copy all</button>
        <span id="console-status">Connecting...</span>
      </div>
      <div id="terminal" class="console-terminal" role="log" aria-live="polite"></div>
    </div>
    <script>
      const streamUrl = "http://127.0.0.1:8000/llm/console/stream";
      const terminal = document.getElementById("terminal");
      const statusEl = document.getElementById("console-status");
      const autoToggle = document.getElementById("auto-scroll");
      let cursor = localStorage.getItem("consoleCursor") || "";
      let autoScroll = true;
      let source;

      function appendLine(event) {
        const line = document.createElement("div");
        line.className = `line level-${event.level.toLowerCase()}`;
        const summary = document.createElement("span");
        summary.className = "summary";
        summary.textContent = event.summary;
        const details = document.createElement("a");
        details.className = "details";
        details.target = "_blank";
        details.rel = "noreferrer";
        details.href = event.details_url;
        details.textContent = "[Details]";
        line.appendChild(summary);
        line.appendChild(details);
        terminal.appendChild(line);
        if (autoScroll) {
          terminal.scrollTop = terminal.scrollHeight;
        }
      }

      function connect() {
        if (source) {
          source.close();
        }
        const url = cursor ? `${streamUrl}?since=${encodeURIComponent(cursor)}` : streamUrl;
        source = new EventSource(url);
        statusEl.textContent = "Streaming console...";
        source.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            cursor = payload.cursor;
            localStorage.setItem("consoleCursor", cursor);
            appendLine(payload);
          } catch (err) {
            console.error("Failed to parse console event:", err);
          }
        };
        source.onopen = () => {
          statusEl.textContent = "Connected";
        };
        source.onerror = () => {
          statusEl.textContent = "Disconnected, retrying...";
          source.close();
          setTimeout(connect, 1500);
        };
      }

      document.getElementById("clear-btn").addEventListener("click", () => {
        terminal.innerHTML = "";
      });

      document.getElementById("copy-btn").addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(terminal.innerText);
          statusEl.textContent = "Copied to clipboard";
          setTimeout(() => (statusEl.textContent = "Connected"), 1500);
        } catch (error) {
          statusEl.textContent = "Clipboard write failed";
        }
      });

      autoToggle.addEventListener("change", (event) => {
        autoScroll = event.target.checked;
      });

      window.addEventListener("beforeunload", () => {
        if (source) {
          source.close();
        }
      });

      connect();
    </script>
  </body>
</html>
"""


@app.get("/ui", response_class=HTMLResponse)
def ui_dashboard():
    html_template = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>ToolRunner Dashboard</title>
    <script src="https://unpkg.com/htmx.org@1.9.5"></script>
<style>
  body {
    font-family: system-ui, sans-serif;
    margin: 0;
    padding: 0;
    background: #111;
    color: #f2f7ff;
  }

  .navbar {
    background: #0b1935;
    padding: 1rem;
    display: flex;
    align-items: center;
    gap: 1rem;
  }

  .tabs-card {
    margin: 1rem;
    border: 1px solid #394667;
    border-radius: 0.9rem;
    background: #050b15;
    padding: 0.5rem 0.75rem 0.75rem;
  }

  .tabs-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
  }

  .tabs-head .ark-tabs {
    flex: 1;
    min-width: 0;
  }

  #run-id-label {
    font-size: 0.85rem;
    color: #93a1c5;
    white-space: nowrap;
  }

  /* ===========================
     ARK TABS (FINAL)
     - folder-tab look
     - disables glider/tabsShadow
     - overrides any .tab / tab-primary / etc styling
     =========================== */

  .ark-tabs[role="tablist"] {
    position: relative;
    display: flex;
    align-items: flex-end;
    gap: 0.25rem;
    padding: 0 0.25rem;
    margin: 0;
    border-bottom: 1px solid var(--ark-border, #394667);
  }

  /* kill the segmented-control artifacts */
  .ark-tabs .glider,
  .ark-tabs .tabsShadow {
    display: none !important;
  }

  .ark-tab[role="tab"] {
    position: relative;
    appearance: none !important;

    /* hard override “button” classes */
    background: #11172b !important;
    color: #cbd4f9 !important;
    box-shadow: none !important;

    border: 1px solid var(--ark-border, #394667) !important;
    border-bottom: none !important;

    border-radius: 12px 12px 0 0 !important;

    padding: 0.55rem 0.9rem;
    font: inherit;
    line-height: 1;
    cursor: pointer;

    /* inactive tabs sit slightly “behind” */
    transform: translateY(6px);
    opacity: 0.9;
    transition: opacity 0.2s ease, transform 0.2s ease;
  }

  .ark-tab[role="tab"]:hover {
    opacity: 1;
  }

  .ark-tab[role="tab"]:focus-visible {
    outline: 2px solid var(--ark-focus, #6ea8fe);
    outline-offset: 2px;
  }

  .ark-tab[role="tab"][aria-selected="true"] {
    background: #050b15 !important; /* match panel surface */
    color: #ffffff !important;
    transform: translateY(0);
    opacity: 1;
    z-index: 5;
  }

  /* hide seam between active tab + panel */
  .ark-tab[role="tab"][aria-selected="true"]::after {
    content: "";
    position: absolute;
    left: 0;
    right: 0;
    bottom: -1px;
    height: 2px;
    background: #050b15;
  }

  /* (optional) subtle bottom line for inactive tabs */
  .ark-tab[role="tab"]:not([aria-selected="true"])::after {
    content: "";
    position: absolute;
    left: 0.45rem;
    right: 0.45rem;
    bottom: 0;
    height: 1px;
    background: rgba(255, 255, 255, 0.06);
  }

  /* ===========================
     TAB PANELS (FINAL)
     - frame + top seam removed so tabs look attached
     =========================== */

  .tab-panels {
    padding: 0 1rem 1rem;
  }

  .tab-panel {
    display: none;
  }

  .tab-panel.active {
    display: block;
  }

  .tab-panel[role="tabpanel"] {
    background: #050b15;
    border: 1px solid var(--ark-border, #394667);
    border-top: none;
    border-radius: 0 0 12px 12px;
    padding: 1rem;
  }

  /* ===========================
     REST OF YOUR STYLES (UNCHANGED)
     =========================== */

  .trial-control {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
  }

  #trivial-trial-btn {
    background: #0fbc9c;
    border: none;
    color: #0b1b0f;
    padding: 0.5rem 1rem;
    border-radius: 0.4rem;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s ease;
  }

  #trivial-trial-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .trial-status {
    font-size: 0.9rem;
    color: #cbd4f9;
  }

  .chat-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.8fr) minmax(0, 1fr);
    gap: 1rem;
  }

  .chat-column,
  .srs-column {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .chat-header h2 {
    margin: 0;
  }

  .chat-subtitle {
    margin: 0.25rem 0 0;
    color: #93a1c5;
  }

  .chat-messages {
    background: #050b15;
    border: 1px solid #394667;
    padding: 0.75rem;
    border-radius: 0.6rem;
    max-height: 420px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .chat-message {
    padding: 0.75rem;
    border-radius: 0.5rem;
    border: 1px solid #1b2540;
    background: #0c1326;
  }

  .chat-message-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.35rem;
    font-size: 0.85rem;
    color: #9fb0d3;
  }

  .chat-badge {
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    font-size: 0.7rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }

  .badge-maestro {
    background: #bf67ff;
    color: #0b0c15;
  }

  .badge-user {
    background: #3a7bfd;
    color: #0b0b1f;
  }

  .chat-message-body {
    white-space: pre-wrap;
    line-height: 1.35;
  }

  .chat-meta {
    margin-top: 0.35rem;
    font-size: 0.8rem;
    color: #cbd4f9;
  }

  .chat-meta ul {
    margin: 0.25rem 0 0;
    padding-left: 1rem;
  }

  .chat-meta.decision {
    color: #ffb703;
  }

  .chat-form {
    background: #050b15;
    border: 1px solid #394667;
    border-radius: 0.5rem;
    padding: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }

  .chat-actions {
    display: flex;
    justify-content: flex-end;
  }

  #chat-input {
    resize: vertical;
    min-height: 60px;
    border-radius: 0.35rem;
  }

  #chat-send-button {
    background: #05c97c;
    border: none;
    color: #0b1b0f;
    padding: 0.5rem 1rem;
    border-radius: 0.35rem;
    font-weight: 600;
  }

  .chat-status {
    min-height: 1.2rem;
    font-size: 0.85rem;
    color: #f6c5c5;
  }

  .chat-empty {
    text-align: center;
    color: #93a1c5;
    font-size: 0.9rem;
  }

  .srs-preview-card,
  .srs-completeness-card,
  .recent-updates-card {
    background: #050b15;
    border: 1px solid #394667;
    border-radius: 0.6rem;
    padding: 0.75rem;
  }

  .srs-preview-card pre {
    max-height: 240px;
    overflow: auto;
  }

  .completeness-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.25rem 0;
    border-bottom: 1px solid #1b2540;
    font-size: 0.85rem;
  }

  .completeness-item:last-child {
    border-bottom: none;
  }

  .completeness-title {
    font-size: 0.85rem;
  }

  .completeness-locked .badge {
    background: #0fbc9c;
    color: #0b1c10;
  }

  .completeness-pending .badge {
    background: #3a7bfd;
    color: #051225;
  }

  .recent-updates-card ul {
    margin: 0;
    padding-left: 1rem;
    font-size: 0.85rem;
    color: #cbd4f9;
  }

  .readiness-card {
    background: #050b15;
    border: 1px solid #394667;
    border-radius: 0.6rem;
    padding: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .readiness-card .readiness-score {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 1rem;
  }

  .readiness-card .readiness-score-value {
    font-size: 1.8rem;
    font-weight: 600;
  }

  .readiness-progress {
    background: #1a1f33;
    border-radius: 0.4rem;
    height: 0.5rem;
    overflow: hidden;
  }

  .readiness-progress span {
    display: block;
    height: 100%;
    background: linear-gradient(90deg, #0fbc9c, #3a7bfd);
  }

  .readiness-details {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 0.75rem;
  }

  .readiness-details h5 {
    margin: 0 0 0.25rem;
    font-size: 0.85rem;
    color: #c5d1f3;
  }

  .readiness-details ul {
    margin: 0;
    padding-left: 1rem;
    font-size: 0.85rem;
    color: #f7fbff;
  }

  .override-gate {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    color: #cbd4f9;
  }

  .override-gate input {
    transform: scale(1.1);
  }

  .readiness-gate-message {
    height: 1.1rem;
    font-size: 0.85rem;
    color: #ffb703;
    margin: 0;
  }

  .maestro-actions,
  .apprentice-actions {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
  }

  .stuck-banner {
    padding: 0.75rem 1rem;
    background: #ffb703;
    color: #1c0c00;
    border-radius: 0.5rem;
    margin-bottom: 0.75rem;
    display: none;
  }

  .apprentice-columns {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 1rem;
  }

  .reports-panel {
    background: #0b0f1f;
    border: 1px solid #394667;
    padding: 0.5rem;
  }

  .report-list {
    list-style: none;
    padding: 0;
    margin: 0;
    max-height: 220px;
    overflow-y: auto;
  }

  .report-list li {
    margin-bottom: 0.25rem;
  }

  .report-list button {
    width: 100%;
    text-align: left;
    background: #11172b;
    border: 1px solid #21305a;
    color: inherit;
    padding: 0.4rem;
    cursor: pointer;
  }

  .report-viewer {
    min-height: 180px;
    background: #050b15;
    border: 1px solid #2d3b56;
    padding: 0.5rem;
    overflow: auto;
  }

  .verification-panel {
    margin-top: 1rem;
    border-top: 1px solid #394667;
    padding-top: 1rem;
  }

  .verification-overview {
    display: flex;
    justify-content: space-between;
    font-weight: 600;
    margin-bottom: 0.5rem;
  }

  .verification-score {
    color: #0fbc9c;
  }

  .verification-flags {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
  }

  .verification-flag {
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border: 1px solid transparent;
  }

  .verification-flag.pass {
    background: #123821;
    border-color: #0fbc9c;
  }

  .verification-flag.fail {
    background: #3b0e0e;
    border-color: #ff3864;
  }

  .verification-flag.warn {
    background: #4f370b;
    border-color: #f9c74f;
  }

  .verification-findings {
    margin: 0;
    padding-left: 1rem;
    color: #cbd4f9;
    min-height: 2rem;
  }

  .verification-warnings {
    margin: 0 0 0.5rem 1rem;
    padding-left: 1rem;
    color: #f9c74f;
    min-height: 1.25rem;
  }

  .verification-warnings li {
    margin-bottom: 0.2rem;
  }

  .verification-actions {
    display: flex;
    gap: 0.5rem;
    margin: 0.5rem 0;
  }

  .verification-actions button {
    flex: 1;
    padding: 0.4rem;
    background: #1c2a4a;
    border: 1px solid #394667;
    color: inherit;
    cursor: pointer;
  }

  .verification-viewer {
    background: #050b15;
    border: 1px solid #2d3b56;
    padding: 0.5rem;
    min-height: 140px;
    font-size: 0.85rem;
    overflow: auto;
  }

  .approval-modal {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.65);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }

  .approval-modal .modal-content {
    background: #111a2d;
    border: 1px solid #394667;
    padding: 1rem;
    width: min(420px, 90%);
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .approval-modal button {
    padding: 0.5rem 1rem;
    border: none;
    cursor: pointer;
  }

  .event-feed {
    background: #050b15;
    border: 1px solid #2d3b56;
    min-height: 120px;
    padding: 0.5rem;
    font-family: monospace;
  }

  .event-item {
    margin-bottom: 0.4rem;
    border-bottom: 1px solid #14213d;
    padding-bottom: 0.2rem;
  }
</style>
  </head>
  <body>
    <div class="navbar">
      <span>Deprecated autop-run UI</span>
    </div>
    <div class="tabs-card">
      <div class="tabs-head">
        <div class="ark-tabs" role="tablist" aria-label="ToolRunner Tabs">
          <button
            id="tab-label-user"
            class="ark-tab active"
            type="button"
            role="tab"
            aria-controls="tab-user"
            aria-selected="true"
            data-tab="user"
            tabindex="0"
          >
            User
          </button>

          <button
            id="tab-label-maestro"
            class="ark-tab"
            type="button"
            role="tab"
            aria-controls="tab-maestro"
            aria-selected="false"
            data-tab="maestro"
            tabindex="-1"
          >
            Maestro
          </button>

          <button
            id="tab-label-apprentice"
            class="ark-tab"
            type="button"
            role="tab"
            aria-controls="tab-apprentice"
            aria-selected="false"
            data-tab="apprentice"
            tabindex="-1"
          >
            Apprentice
          </button>

          <div class="tabsShadow"></div>
          <div class="glider"></div>
        </div>
        <span id="run-id-label">Run ID: __RUN_ID__</span>
      </div>
      <div class="tab-panels">
        <div id="tab-user" class="tab-panel active" role="tabpanel" aria-labelledby="tab-label-user">
          <div class="trial-control">
            <button id="trivial-trial-btn">Trivial Trial (Seed + Plan + Run)</button>
            <span id="trivial-trial-status" class="trial-status">Ready for a new trial.</span>
          </div>
          <div id="user-tab-content" hx-get="/ui/partials/user?run_id=__RUN_ID__" hx-trigger="load"></div>
        </div>
        <div id="tab-maestro" class="tab-panel" role="tabpanel" aria-labelledby="tab-label-maestro">
          <div id="maestro-tab-content" hx-get="/ui/partials/maestro?run_id=__RUN_ID__" hx-trigger="load"></div>
        </div>
        <div id="tab-apprentice" class="tab-panel" role="tabpanel" aria-labelledby="tab-label-apprentice">
          <div id="apprentice-tab-content" hx-get="/ui/partials/apprentice?run_id=__RUN_ID__" hx-trigger="load"></div>
        </div>
      </div>
    </div>
    <script>
      let runId = "__RUN_ID__";
      const TAB_NAMES = ["user", "maestro", "apprentice"];

      function updateGlider() {
        const card = document.querySelector(".ark-tabs");
        const glider = card?.querySelector(".glider");
        const activeTab = card?.querySelector(".ark-tab.active");
        if (!card || !glider || !activeTab) return;
        const cardRect = card.getBoundingClientRect();
        const tabRect = activeTab.getBoundingClientRect();
        const leftPct = ((tabRect.left - cardRect.left) / cardRect.width) * 100;
        const widthPct = (tabRect.width / cardRect.width) * 100;
        card.style.setProperty("--glider-left", `${leftPct}%`);
        card.style.setProperty("--glider-width", `${widthPct}%`);
      }

      function activateTab(name, { pushHash = true } = {}) {
        if (!TAB_NAMES.includes(name)) {
          name = "user";
        }
        const tabs = Array.from(document.querySelectorAll(".ark-tab[role='tab']"));
        const panels = Array.from(document.querySelectorAll(".tab-panel[role='tabpanel']"));

        tabs.forEach((tab) => {
          const isActive = tab.dataset.tab === name;
          tab.classList.toggle("active", isActive);
          tab.setAttribute("aria-selected", isActive ? "true" : "false");
          tab.tabIndex = isActive ? 0 : -1;
        });

        panels.forEach((panel) => {
          const isActive = panel.id === "tab-" + name;
          panel.classList.toggle("active", isActive);
          panel.hidden = !isActive;
        });

        updateGlider();

        if (pushHash) {
          history.replaceState(null, "", `#${name}`);
        }
      }

      let resizeTimer;

      window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(updateGlider, 150);
      });

      function initTabs() {
        document.querySelectorAll(".ark-tab[role='tab']").forEach((tab) => {
          tab.addEventListener("click", () => activateTab(tab.dataset.tab));
          tab.addEventListener("keydown", (evt) => {
            if (evt.key !== "ArrowRight" && evt.key !== "ArrowLeft") {
              return;
            }
            evt.preventDefault();
            const tabs = Array.from(document.querySelectorAll(".ark-tab[role='tab']"));
            const idx = tabs.indexOf(tab);
            if (idx === -1) {
              return;
            }
            const nextIndex = evt.key === "ArrowRight" ? (idx + 1) % tabs.length : (idx - 1 + tabs.length) % tabs.length;
            const target = tabs[nextIndex];
            target.focus();
            activateTab(target.dataset.tab);
          });
        });
        document.querySelectorAll(".tab-panel").forEach((panel) => {
          panel.hidden = !panel.classList.contains("active");
        });
      }

      let lastEventId = 0;
      let pendingApproval = null; // {type, data, ...} or null
     
      const approvalModal = document.getElementById("approval-modal");
      const approvalDescription = document.getElementById("approval-description");
      const approvalApprove = document.getElementById("approval-approve");
      const approvalDeny = document.getElementById("approval-deny");
      const approvalClose = document.getElementById("approval-close");

      function showApprovalModal(event) {
        pendingApproval = event;
        if (approvalDescription) {
          const tags = (event.data?.risk_tags || []).join(", ");
          approvalDescription.textContent = `Step ${event.data?.step} requires approval${tags ? " (risk tags: " + tags + ")" : ""}.`;
        }
        if (approvalModal) {
          approvalModal.style.display = "flex";
        }
      }

      function hideApprovalModal() {
        pendingApproval = null;
        if (approvalModal) {
          approvalModal.style.display = "none";
        }
      }

      function showStuckBanner(reason, remediation, excerpt) {
        const banner = document.getElementById("stuck-banner");
        if (!banner) return;
        if (reason) {
          let html = `<p><strong>Run blocked:</strong> ${escapeHtml(reason)}</p>`;
          if (remediation) {
            html += `<p><strong>Remediation:</strong> ${escapeHtml(remediation)}</p>`;
          }
          if (excerpt) {
            html += `<p><strong>Last failure:</strong> ${escapeHtml(excerpt)}</p>`;
          }
          banner.innerHTML = html;
          banner.style.display = "block";
        } else {
          banner.style.display = "none";
        }
      }

      function updateRunIdLabel(value) {
        const label = document.getElementById("run-id-label");
        if (label) {
          label.textContent = `Run ID: ${value}`;
        }
      }

      function setNewRunId(newId) {
        runId = newId;
        lastEventId = 0;
        pendingApproval = null;
        const feed = document.getElementById("event-feed");
        if (feed) {
          feed.innerHTML = "";
        }
        const reports = document.getElementById("step-report-list");
        if (reports) {
          reports.innerHTML = "";
        }
        const statusNode = document.getElementById("run-control-status");
        if (statusNode) {
          statusNode.textContent = `Run ${newId} selected`;
        }
        updateRunIdLabel(newId);
        pollEvents();
        updateStepReports();
        updateVerificationSummary();
      }

      function switchToApprenticeTab() {
        const apprenticeTab = document.querySelector(".tab[data-tab='apprentice']");
        if (apprenticeTab) {
          apprenticeTab.click();
        }
      }

      async function refreshUserPartialForRun(newId) {
        if (typeof htmx === "undefined") {
          return;
        }
        try {
          await htmx.ajax("GET", `/ui/partials/user?run_id=${encodeURIComponent(newId)}`, {
            target: "#user-tab-content",
            swap: "innerHTML",
          });
        } catch (error) {
          console.error("failed to refresh user tab for new run", error);
        }
      }

      function initTrivialTrialControls() {
        const button = document.getElementById("trivial-trial-btn");
        if (!button) {
          return;
        }
        button.addEventListener("click", (event) => {
          event.preventDefault();
          startTrivialTrial();
        });
      }

      async function startTrivialTrial() {
        const button = document.getElementById("trivial-trial-btn");
        const statusNode = document.getElementById("trivial-trial-status");
        if (!button || !statusNode) {
          return;
        }
        button.disabled = true;
        const stages = ["Seeding SRS\u2026", "Generating plan\u2026", "Starting run\u2026"];
        let phaseIndex = 0;
        statusNode.textContent = stages[phaseIndex];
        const phaseInterval = setInterval(() => {
          phaseIndex = Math.min(phaseIndex + 1, stages.length - 1);
          statusNode.textContent = stages[phaseIndex];
        }, 600);
        try {
          const resp = await fetch("/v1/trials/trivial", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
          const data = await resp.json();
          if (!resp.ok) {
            throw new Error(data.detail || "Trivial trial failed");
          }
          if (!data.ok) {
            throw new Error("Trivial trial endpoint reported failure");
          }
          const finalMessage = data.run_started ? `Run started: ${data.run_id}` : `Plan ready: ${data.run_id}`;
          statusNode.textContent = finalMessage;
          setNewRunId(data.run_id);
          await refreshUserPartialForRun(data.run_id);
          switchToApprenticeTab();
        } catch (error) {
          statusNode.textContent = `Error: ${error.message || "Unable to start trial"}`;
          console.error("Trivial trial failed", error);
        } finally {
          clearInterval(phaseInterval);
          button.disabled = false;
        }
      }

      function escapeHtml(text) {
        if (!text) return "";
        return text
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
      }

      async function pollEvents() {
        const feed = document.getElementById("event-feed");
        if (!feed) return;
        try {
          const resp = await fetch(`/v1/runs/${runId}/events?since=${lastEventId}`);
          const payload = await resp.json();
          for (const evt of payload.events) {
            const entry = document.createElement("div");
            entry.className = "event-item";
            entry.innerHTML = "<strong>" + escapeHtml(evt.type) + "</strong>: " + escapeHtml(JSON.stringify(evt.data));
            feed.appendChild(entry);
            handleEvent(evt);
          }
          lastEventId = payload.next_since ?? lastEventId;
        } catch (error) {
          console.error("event poll failed", error);
        }
      }

      async function updateStepReports() {
        const list = document.getElementById("step-report-list");
        if (!list) return;
        try {
          const resp = await fetch(`/v1/runs/${runId}/step_reports`);
          const reports = await resp.json();
          list.innerHTML = "";
          reports.forEach((entry) => {
            const li = document.createElement("li");
            const btn = document.createElement("button");
            btn.textContent = `${entry.milestone_id}/${entry.step_id}`;
            btn.addEventListener("click", () => viewStepReport(entry.milestone_id, entry.step_id));
            li.appendChild(btn);
            list.appendChild(li);
          });
        } catch (error) {
          console.error("report list error", error);
        }
      }

      async function viewStepReport(milestoneId, stepId) {
        const viewer = document.getElementById("step-report-viewer");
        if (!viewer) return;
        try {
          const resp = await fetch(`/v1/runs/${runId}/step_reports/${milestoneId}/${stepId}`);
          const data = await resp.json();
          viewer.textContent = JSON.stringify(data, null, 2);
        } catch (error) {
          viewer.textContent = "Failed to load report.";
        }
      }

      async function updateVerificationSummary() {
        const statusNode = document.getElementById("verification-status");
        const scoreNode = document.getElementById("verification-score");
        const findingsNode = document.getElementById("verification-findings");
        const autonomyNode = document.getElementById("verification-autonomy");
        const hygieneNode = document.getElementById("verification-hygiene");
        const warningsNode = document.getElementById("verification-warnings");
        if (
          !statusNode ||
          !scoreNode ||
          !findingsNode ||
          !autonomyNode ||
          !hygieneNode ||
          !warningsNode
        ) {
          return;
        }
        const resetSummary = () => {
          statusNode.textContent = "Verification pending";
          scoreNode.textContent = "";
          findingsNode.innerHTML = "<li>No verification data yet.</li>";
          warningsNode.innerHTML = "<li>No warnings.</li>";
          autonomyNode.textContent = "Autonomy: pending";
          autonomyNode.className = "verification-flag";
          hygieneNode.textContent = "Hygiene: pending";
          hygieneNode.className = "verification-flag";
        };
        try {
          const resp = await fetch(`/v1/runs/${runId}/verify`);
          const payload = await resp.json();
          if (!resp.ok || payload.ok === false) {
            resetSummary();
            findingsNode.innerHTML = `<li>${escapeHtml(payload.detail || "No verification data.")}</li>`;
            return;
          }
          const autonomyStatus = payload.autonomy_ok ? "PASS" : "FAIL";
          const hygieneStatus = payload.hygiene_ok ? "PASS" : "WARN";
          const applyFlag = (node, label, status) => {
            node.textContent = `${label}: ${status}`;
            node.classList.toggle("pass", status === "PASS");
            node.classList.toggle("fail", status === "FAIL");
            node.classList.toggle("warn", status === "WARN");
          };
          statusNode.textContent = payload.overall_ok ? "Verification PASS" : "Verification FAIL";
          scoreNode.textContent = `Score ${payload.score_overall}/100`;
          applyFlag(autonomyNode, "Autonomy", autonomyStatus);
          applyFlag(hygieneNode, "Hygiene", hygieneStatus);

          const findings = payload.fail_reasons || [];
          findingsNode.innerHTML = findings.length
            ? findings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
            : "<li>No findings.</li>";

          const warnings = [...(payload.warnings || [])];
          if (payload.remediation && payload.remediation.length) {
            payload.remediation.forEach((entry) => warnings.push(`Remediation: ${entry}`));
          }
          warningsNode.innerHTML = warnings.length
            ? warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
            : "<li>No warnings.</li>";
        } catch (error) {
          resetSummary();
          findingsNode.innerHTML = "<li>Unable to load verification summary.</li>";
        }
      }

      async function displayVerificationContent(kind) {
        const viewer = document.getElementById("verification-viewer");
        if (!viewer) return;
        try {
          const url = kind === "json" ? `/v1/runs/${runId}/verify` : `/v1/runs/${runId}/verify/md`;
          const resp = await fetch(url);
          const payload = await resp.json();
          if (!resp.ok || (kind === "json" && payload.ok === false)) {
            viewer.textContent = payload.detail || "Verification not available.";
            return;
          }
          if (kind === "json") {
            viewer.textContent = JSON.stringify(payload, null, 2);
          } else {
            viewer.textContent = payload.content || "No markdown content.";
          }
        } catch (error) {
          viewer.textContent = "Failed to load verification content.";
        }
      }

      function initVerificationButtons() {
        const jsonBtn = document.getElementById("verification-json-btn");
        const mdBtn = document.getElementById("verification-md-btn");
        if (jsonBtn) {
          jsonBtn.addEventListener("click", () => displayVerificationContent("json"));
        }
        if (mdBtn) {
          mdBtn.addEventListener("click", () => displayVerificationContent("md"));
        }
      }

      function handleEvent(evt) {
        if (evt.type === "APPROVAL_REQUESTED") {
          showApprovalModal(evt);
        }
        if (evt.type === "STEP_REPORT_WRITTEN") {
          updateStepReports();
        }
        if (evt.type === "RUN_FINALIZED" && evt.data?.status === "blocked") {
          showStuckBanner(evt.data?.reason, evt.data?.remediation, evt.data?.failure_excerpt);
        }
        if (evt.type === "VERIFICATION_WRITTEN") {
          updateVerificationSummary();
        }
      }

      function submitApproval(decision) {
        return async function () {
          if (!pendingApproval) return;
          try {
            await fetch(`/v1/runs/${runId}/approve`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                step_id: pendingApproval.data?.step,
                milestone_id: pendingApproval.data?.milestone,
                decision,
                scope: "once",
              }),
            });
            hideApprovalModal();
            pollEvents();
          } catch (error) {
            console.error("approval failed", error);
          }
        };
      }

      if (approvalApprove) {
        approvalApprove.addEventListener("click", submitApproval("approve"));
      }
      if (approvalDeny) {
        approvalDeny.addEventListener("click", submitApproval("deny"));
      }
      if (approvalClose) {
        approvalClose.addEventListener("click", hideApprovalModal);
      }

      setInterval(pollEvents, 1500);
      pollEvents();
      updateStepReports();
      initVerificationButtons();
      updateVerificationSummary();
      const chatModule = (() => {
        const state = {
          messages: [],
          lastId: 0,
          recentUpdates: [],
        };
        let elements = {};

        function refreshElements() {
          elements = {
            chatMessages: document.getElementById("chat-messages"),
            chatForm: document.getElementById("chat-form"),
            chatInput: document.getElementById("chat-input"),
            chatStatus: document.getElementById("chat-status"),
            chatSendButton: document.getElementById("chat-send-button"),
            completenessList: document.getElementById("completeness-list"),
            srsPreview: document.getElementById("srs-preview"),
            recentUpdatesList: document.getElementById("recent-updates-list"),
          };
        }

        function buildMessageElement(message) {
          const wrapper = document.createElement("div");
          wrapper.className = `chat-message ${message.role || "user"}`;
          if (message.id) {
            wrapper.dataset.messageId = String(message.id);
          }
          const header = document.createElement("div");
          header.className = "chat-message-header";
          const badge = document.createElement("span");
          badge.className = `chat-badge ${message.role === "maestro" ? "badge-maestro" : "badge-user"}`;
          badge.textContent = message.role === "maestro" ? "Maestro" : "User";
          header.appendChild(badge);
          const ts = document.createElement("span");
          ts.className = "chat-ts";
          ts.textContent = message.ts || "";
          header.appendChild(ts);
          wrapper.appendChild(header);
          const body = document.createElement("div");
          body.className = "chat-message-body";
          body.textContent = message.content || "";
          wrapper.appendChild(body);
          if (message.role === "maestro") {
            const meta = message.meta || {};
            if (Array.isArray(meta.questions) && meta.questions.length) {
              const block = document.createElement("div");
              block.className = "chat-meta";
              const title = document.createElement("strong");
              title.textContent = "Questions:";
              block.appendChild(title);
              const list = document.createElement("ul");
              meta.questions.forEach((question) => {
                const item = document.createElement("li");
                item.textContent = question;
                list.appendChild(item);
              });
              block.appendChild(list);
              wrapper.appendChild(block);
            }
            if (Array.isArray(meta.srs_updates) && meta.srs_updates.length) {
              const block = document.createElement("div");
              block.className = "chat-meta";
              const title = document.createElement("strong");
              title.textContent = "SRS updates:";
              block.appendChild(title);
              const list = document.createElement("ul");
              meta.srs_updates.forEach((update) => {
                const item = document.createElement("li");
                const sectionId = update.section_id || "unknown";
                const action = update.action || "draft";
                item.textContent = `${sectionId}: ${action}`;
                list.appendChild(item);
              });
              block.appendChild(list);
              wrapper.appendChild(block);
            }
            if (meta.requires_user_decision) {
              const decision = document.createElement("div");
              decision.className = "chat-meta decision";
              decision.textContent = "Requires your approval.";
              wrapper.appendChild(decision);
            }
          }
          return wrapper;
        }

        function renderMessages(messages) {
          const container = elements.chatMessages;
          if (!container) return;
          container.innerHTML = "";
          messages.forEach((message) => container.appendChild(buildMessageElement(message)));
          container.scrollTop = container.scrollHeight;
        }

        async function loadChatHistory() {
          refreshElements();
          if (!elements.chatMessages) return;
          try {
            const resp = await fetch(`/v1/runs/${runId}/chat/history`);
            const payload = await resp.json();
            if (Array.isArray(payload.messages)) {
              state.messages = payload.messages;
              state.lastId = payload.next_since || state.lastId;
              renderMessages(state.messages);
            }
          } catch (error) {
            console.error("chat history failed", error);
          }
        }

        async function updateSrsPreview() {
          if (!elements.srsPreview) return;
          try {
            const resp = await fetch(`/v1/runs/${runId}/srs/md`);
            if (!resp.ok) throw new Error("failed preview refresh");
            const data = await resp.json();
            elements.srsPreview.textContent = data?.content || "No SRS content yet.";
          } catch (error) {
            console.error("SRS preview refresh failed", error);
          }
        }

        async function updateCompleteness() {
          const list = elements.completenessList;
          if (!list) return;
          try {
            const resp = await fetch(`/v1/runs/${runId}/srs/sections`);
            if (!resp.ok) throw new Error("completeness fetch failed");
            const sections = await resp.json();
            const statusMap = {};
            sections.forEach((section) => {
              statusMap[section.section_id] = section.status === "locked";
            });
            list.querySelectorAll(".completeness-item").forEach((item) => {
              const sectionId = item.getAttribute("data-section-id");
              const locked = !!statusMap[sectionId];
              item.classList.toggle("completeness-locked", locked);
              item.classList.toggle("completeness-pending", !locked);
              const badge = item.querySelector(".badge");
              if (badge) {
                badge.textContent = locked ? "Locked" : "Pending";
                badge.classList.toggle("badge-locked", locked);
                badge.classList.toggle("badge-draft", !locked);
              }
            });
          } catch (error) {
            console.error("completeness update failed", error);
          }
        }

        function renderRecentUpdates() {
          const list = elements.recentUpdatesList;
          if (!list) return;
          if (!state.recentUpdates.length) {
            list.innerHTML = "<li>No updates yet.</li>";
            return;
          }
          list.innerHTML = state.recentUpdates
            .map((update) => `<li>${escapeHtml(update.section_id)}: ${escapeHtml(update.action)}</li>`)
            .join("");
        }

        function applyRecentUpdates(newUpdates) {
          if (!Array.isArray(newUpdates) || !newUpdates.length) {
            return;
          }
          state.recentUpdates = [...newUpdates, ...state.recentUpdates];
          state.recentUpdates = state.recentUpdates.slice(0, 3);
          renderRecentUpdates();
        }

        function parseRecentUpdates() {
          const node = document.getElementById("recent-updates-data");
          if (!node) return [];
          try {
            const text = node.textContent?.trim();
            return text ? JSON.parse(text) : [];
          } catch {
            return [];
          }
        }

        function setChatStatus(message, error = false) {
          if (!elements.chatStatus) return;
          elements.chatStatus.textContent = message || "";
          elements.chatStatus.style.color = error ? "#ffb703" : "#cbd4f9";
        }

        async function sendChatMessage(event) {
          event.preventDefault();
          if (!elements.chatInput) return;
          const message = elements.chatInput.value.trim();
          if (!message) return;
          setChatStatus("");
          if (elements.chatSendButton instanceof HTMLButtonElement) {
            elements.chatSendButton.disabled = true;
          }
          try {
            const resp = await fetch(`/v1/runs/${runId}/chat`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ message }),
            });
            if (!resp.ok) {
              throw new Error(await resp.text());
            }
            const payload = await resp.json();
            const additions = [];
            if (payload.user_message) {
              additions.push(payload.user_message);
            }
            if (payload.maestro_message) {
              additions.push(payload.maestro_message);
            }
            state.messages = [...state.messages, ...additions];
            state.lastId = payload.maestro_message?.id ?? payload.user_message?.id ?? state.lastId;
            renderMessages(state.messages);
            applyRecentUpdates(payload.applied_updates || []);
            await Promise.all([updateSrsPreview(), updateCompleteness()]);
            elements.chatInput.value = "";
          } catch (error) {
            console.error("chat send failed", error);
            setChatStatus("Unable to send message. Try again.", true);
          } finally {
            if (elements.chatSendButton instanceof HTMLButtonElement) {
              elements.chatSendButton.disabled = false;
            }
          }
        }

        return {
          init() {
            refreshElements();
            if (!elements.chatMessages) return;
            if (elements.chatForm) {
              elements.chatForm.addEventListener("submit", sendChatMessage);
            }
            if (!state.recentUpdates.length) {
              state.recentUpdates = parseRecentUpdates();
            }
            renderRecentUpdates();
            loadChatHistory();
            updateSrsPreview();
            updateCompleteness();
          },
        };
      })();
      window.chatModule = chatModule;
      document.body.addEventListener("htmx:afterSwap", (evt) => {
        if (evt.detail?.target?.id === "user-tab-content") {
          chatModule.init();
          setupReadinessGate();
        }
      });
      document.addEventListener("DOMContentLoaded", () => {
        chatModule.init();
        initTrivialTrialControls();
        setupReadinessGate();
        initTabs();
        const hash = (location.hash || "").replace("#", "");
        if (["user", "maestro", "apprentice"].includes(hash)) {
          activateTab(hash, { pushHash: false });
        } else {
          activateTab("user", { pushHash: false });
        }
      });
      function setupReadinessGate() {
        const threshold = 60;
        const planButton = document.getElementById("generate-plan-btn");
        const overrideCheckbox = document.getElementById("override-readiness");
        const readinessScoreEl = document.getElementById("readiness-score");
        const gateMessage = document.getElementById("readiness-gate-message");
        const score = Number(readinessScoreEl?.textContent?.trim() ?? "0");
        const baseUrl =
          planButton?.dataset.baseUrl ?? planButton?.getAttribute("hx-post") ?? "";

        function refreshGate() {
          const overrideEnabled = overrideCheckbox?.checked ?? false;
          const passed = score >= threshold || overrideEnabled;
          if (planButton) {
            planButton.disabled = !passed;
            if (baseUrl) {
              const targetUrl = overrideEnabled ? `${baseUrl}?override=true` : baseUrl;
              planButton.setAttribute("hx-post", targetUrl);
            }
          }
          if (gateMessage) {
            if (!passed && !overrideEnabled) {
              gateMessage.textContent = `Score below ${threshold}. Lock more sections to improve readiness.`;
            } else if (overrideEnabled) {
              gateMessage.textContent = "Override enabled; plan generation allowed.";
            } else {
              gateMessage.textContent = "";
            }
          }
        }

        overrideCheckbox?.addEventListener("change", refreshGate);
        refreshGate();
      }
      setupReadinessGate();
    </script>
  </body>
</html>
"""
    html_content = html_template.replace("__RUN_ID__", UI_RUN_ID)
    return HTMLResponse(content=html_content)


@app.get("/ui/partials/user", response_class=HTMLResponse)
def user_partial(run_id: str = Query(UI_RUN_ID), section_id: Optional[str] = None):
    return HTMLResponse(content=_render_user_partial(run_id, section_id))


@app.get("/ui/partials/maestro", response_class=HTMLResponse)
def maestro_partial(run_id: str = Query(UI_RUN_ID)):
    return HTMLResponse(content=_render_maestro_partial(run_id))


@app.get("/ui/partials/apprentice", response_class=HTMLResponse)
def apprentice_partial(run_id: str = Query(UI_RUN_ID)):
    return HTMLResponse(content=_render_apprentice_partial(run_id))


@app.post("/v1/runs")
def create_run(request: RunCreateRequest):
    context = run_manager.create_run(request.slug, request.repo_dir, request.srs_path)
    return {"run_id": context.run_id}


@app.get("/v1/runs/{run_id}")
def get_run_status(run_id: str):
    context = _get_run_context(run_id)
    return _run_status_summary(context)


@app.post("/v1/runs/{run_id}/start")
def start_run(run_id: str):
    return run_manager.start_run(run_id)


@app.post("/v1/runs/{run_id}/stop")
def stop_run(run_id: str):
    return run_manager.stop_run(run_id)


def _normalize_trial_slug(slug: str | None) -> str:
    base = (slug or "trivial-trial").strip().lower()
    base = base.replace(" ", "-")
    if not base.startswith("trial-"):
        base = f"trial-{base or 'trivial'}"
    return base or "trial-trivial"


def _bool_from_form_value(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if not lowered:
        return None
    return lowered not in {"0", "false", "no", "off"}


def _relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


@app.post("/v1/trials/trivial")
async def trivial_trial(request: Request, payload: TrivialTrialRequest | None = Body(None)):
    data: dict[str, Any] = payload.dict(exclude_none=True) if payload else {}
    if payload is None:
        form = await request.form()
        repo_dir_val = form.get("repo_dir")
        if repo_dir_val:
            data["repo_dir"] = repo_dir_val
        slug_val = form.get("slug")
        if slug_val:
            data["slug"] = slug_val
        template_val = form.get("template")
        if template_val:
            data["template"] = template_val
        start_val = _bool_from_form_value(form.get("start"))
        if start_val is not None:
            data["start"] = start_val
        override_val = _bool_from_form_value(form.get("override_readiness"))
        if override_val is not None:
            data["override_readiness"] = override_val
    trial_request = TrivialTrialRequest(**data)
    slug = _normalize_trial_slug(trial_request.slug)
    repo_dir = trial_request.repo_dir or str(REPO_ROOT)
    context = run_manager.create_run(slug, repo_dir)
    context.event_logger.log(
        "TRIAL_STARTED",
        {"run_id": context.run_id, "slug": slug, "template": trial_request.template},
    )
    charter_data = json.loads(context.charter_path.read_text(encoding="utf-8"))
    charter_data.setdefault("allowed_tools", {})
    charter_data["allowed_tools"] = TRIVIAL_TRIAL_ALLOWED_TOOLS
    context.charter_path.write_text(json.dumps(charter_data, indent=2), encoding="utf-8")
    template_sections = TRIVIAL_TRIAL_TEMPLATES.get(trial_request.template)
    if not template_sections:
        raise HTTPException(status_code=400, detail=f"unknown trial template {trial_request.template}")
    updates = [
        {"section_id": section_id, "action": "lock", "content": content.strip()}
        for section_id, content in template_sections
    ]
    applied = _apply_srs_updates(context, updates)
    seeded_sections = [update["section_id"] for update in applied]
    context.event_logger.log(
        "TRIAL_SRS_SEEDED",
        {"run_id": context.run_id, "template": trial_request.template, "seeded_sections": seeded_sections},
    )
    metadata_path = context.run_root / "run_metadata.json"
    metadata_payload = {
        "template": trial_request.template,
        "slug": slug,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    metadata_path.write_text(json.dumps(metadata_payload, indent=2), encoding="utf-8")
    readiness = ensure_readiness(context)
    if not trial_request.override_readiness and readiness.get("score", 0) < READINESS_SCORE_THRESHOLD:
        raise HTTPException(
            status_code=400,
            detail=f"readiness score {readiness.get('score', 0)} is below the threshold ({READINESS_SCORE_THRESHOLD})",
        )
    try:
        plan = compile_plan(context.run_id)
    except (PlanCompilerError, SchemaValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    context.event_logger.log("PLAN_GENERATED", {"run_id": context.run_id, "plan_id": plan.plan_id})
    context.latest_plan_id = plan.plan_id
    context.event_logger.log(
        "TRIAL_PLAN_GENERATED",
        {"run_id": context.run_id, "plan_id": plan.plan_id, "template": trial_request.template},
    )
    run_started = False
    if trial_request.start:
        run_response = run_manager.start_run(context.run_id)
        run_started = run_response.get("status") == "running"
        context.event_logger.log(
            "TRIAL_RUN_STARTED",
            {"run_id": context.run_id, "status": run_response.get("status")},
        )
    paths = {
        "run_dir": _relative_to_root(context.run_root),
        "srs_md": _relative_to_root(context.run_root / "srs" / "SRS.md"),
        "plan_latest": _relative_to_root(context.run_root / "plans" / "latest.json"),
    }
    return {
        "ok": True,
        "run_id": context.run_id,
        "paths": paths,
        "seeded_sections": seeded_sections,
        "plan_generated": True,
        "run_started": run_started,
    }


@app.get("/v1/runs/{run_id}/events")
def list_events(run_id: str, since: int = Query(0)):
    context = _get_run_context(run_id)
    events, next_since = context.event_logger.read_since(since)
    return {"events": events, "next_since": next_since}


@app.get("/v1/runs/{run_id}/verify")
def get_verification(run_id: str):
    context = _get_run_context(run_id)
    _, json_path, _ = _verification_paths(context)
    if not json_path.exists():
        return {"ok": False, "detail": "verification not found"}
    return json.loads(json_path.read_text(encoding="utf-8"))


@app.get("/v1/runs/{run_id}/verify/md")
def get_verification_markdown(run_id: str):
    context = _get_run_context(run_id)
    _, _, md_path = _verification_paths(context)
    if not md_path.exists():
        return {"ok": False, "detail": "verification markdown not found"}
    return {"content": md_path.read_text(encoding="utf-8")}


@app.post("/v1/runs/{run_id}/verify/run")
def run_verification(run_id: str):
    context = _get_run_context(run_id)
    metadata = read_run_metadata(context.run_root)
    template = metadata.get("template")
    result = run_post_run_verification(
        context.run_id,
        str(REPO_ROOT),
        context.run_root,
        template_slug=template,
        event_logger=context.event_logger,
    )
    return result.model_dump()


@app.post("/v1/runs/{run_id}/approve")
def approve_step(run_id: str, payload: ApprovalRequest):
    context = _get_run_context(run_id)
    record = context.record_approval(
        payload.step_id,
        payload.milestone_id,
        payload.decision,
        payload.scope,
        payload.path,
    )
    context.event_logger.log("APPROVAL_RECORDED", {"run_id": run_id, **record})
    return record


@app.get("/v1/runs/{run_id}/srs/sections")
def list_srs_sections(run_id: str):
    context = _get_run_context(run_id)
    builder = context.srs_builder
    return [
        {
            "section_id": section.section_id,
            "title": section.title,
            "status": "locked" if builder.is_locked(section.section_id) else "draft",
        }
        for section in builder.sections
    ]


@app.get("/v1/runs/{run_id}/srs/sections/{section_id}/prompt")
def section_prompt(run_id: str, section_id: str):
    context = _get_run_context(run_id)
    builder = context.srs_builder
    try:
        section = next(sec for sec in builder.sections if sec.section_id == section_id)
    except StopIteration:
        raise HTTPException(status_code=404, detail="section not found")
    prompt = builder.prompt(section_id)
    context.event_logger.log(
        "SRS_SECTION_PROMPTED",
        {"run_id": run_id, "section_id": section_id},
    )
    draft = context.srs_drafts.get(section_id)
    return {**prompt, "draft": draft}


@app.post("/v1/runs/{run_id}/srs/sections/{section_id}")
async def save_section(
        run_id: str,
        section_id: str,
        request: Request,
        payload: SRSSectionUpdate | None = Body(None),
):
    context = _get_run_context(run_id)
    if payload is None:
        form = await request.form()
        if "content" not in form or "action" not in form:
            raise HTTPException(status_code=422, detail="missing form fields")
        payload = SRSSectionUpdate(content=form["content"], action=form["action"])
    if payload.action not in {"draft", "lock"}:
        raise HTTPException(status_code=400, detail="invalid action")
    if payload.action == "draft":
        context.srs_drafts[section_id] = payload.content
        context.event_logger.log(
            "SRS_SECTION_DRAFT_SAVED",
            {"run_id": run_id, "section_id": section_id},
        )
        return HTMLResponse(content=_render_user_partial(run_id, section_id, log_prompt=False))

    try:
        locked = context.srs_builder.record_section(section_id, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    context.srs_builder.save()
    context.srs_drafts.pop(section_id, None)
    context.event_logger.log(
        "SRS_SECTION_LOCKED",
        {"run_id": run_id, "section_id": section_id, "sha256": locked["sha256"]},
    )
    compute_readiness(context)
    return HTMLResponse(content=_render_user_partial(run_id, section_id, log_prompt=False))


@app.get("/v1/runs/{run_id}/srs/md")
def srs_markdown(run_id: str):
    context = _get_run_context(run_id)
    if context.srs_builder.srs_path.exists():
        return JSONResponse(content={"content": context.srs_builder.srs_path.read_text(encoding="utf-8")})
    return JSONResponse(content={"content": ""})


@app.get("/v1/runs/{run_id}/srs/lock")
def srs_lock(run_id: str):
    context = _get_run_context(run_id)
    if context.srs_builder.lock_path.exists():
        return JSONResponse(content=json.loads(context.srs_builder.lock_path.read_text(encoding="utf-8")))
    return JSONResponse(content={})


@app.get("/v1/runs/{run_id}/srs/readiness")
def srs_readiness(run_id: str):
    context = _get_run_context(run_id)
    readiness = ensure_readiness(context)
    return JSONResponse(content=readiness)


@app.post("/v1/runs/{run_id}/chat")
async def post_chat_message(
        run_id: str,
        request: Request,
        payload: ChatRequest | None = Body(None),
):
    data = payload.dict() if payload else {}
    if payload is None:
        form = await request.form()
        data["message"] = form.get("message", "")
        data["mode"] = form.get("mode", "srs_builder")
    message = (data.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    context = _get_run_context(run_id)
    user_message = _append_chat_message(context, "user", message, {"mode": data.get("mode", "srs_builder")})
    maestro_payload = run_manager.chat_engine.respond(message, context.srs_builder)
    updates = _apply_srs_updates(context, maestro_payload.get("meta", {}).get("srs_updates"))
    maestro_message = _append_chat_message(
        context,
        "maestro",
        maestro_payload.get("content", ""),
        maestro_payload.get("meta", {}),
    )
    preview = _srs_preview_data(context)
    return {
        "ok": True,
        "run_id": run_id,
        "user_message": user_message,
        "maestro_message": maestro_message,
        "applied_updates": updates,
        "srs": preview,
    }


@app.get("/v1/runs/{run_id}/chat/history")
def chat_history(run_id: str, since: int = Query(0)):
    context = _get_run_context(run_id)
    messages, next_since = context.chat_transcript.read_since(since)
    return {"ok": True, "messages": messages, "next_since": next_since}


@app.post("/v1/runs/{run_id}/chat/reset")
def chat_reset(run_id: str):
    context = _get_run_context(run_id)
    context.chat_transcript.reset()
    context.event_logger.log("CHAT_RESET", {"run_id": run_id})
    return {"ok": True}


@app.get("/v1/runs/{run_id}/step_reports")
def list_step_reports(run_id: str):
    context = _get_run_context(run_id)
    return JSONResponse(content=_step_reports_for_run(context))


@app.get("/v1/runs/{run_id}/step_reports/{milestone_id}/{step_id}")
def get_step_report(run_id: str, milestone_id: str, step_id: str):
    context = _get_run_context(run_id)
    report_path = context.run_root / "step_reports" / milestone_id / f"{step_id}.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="step report missing")
    return JSONResponse(content=json.loads(report_path.read_text(encoding="utf-8")))


@app.post("/v1/runs/{run_id}/plan/generate")
def plan_generate(run_id: str, override: bool = Query(False)):
    context = _get_run_context(run_id)
    readiness = ensure_readiness(context)
    if not override and readiness.get("score", 0) < READINESS_SCORE_THRESHOLD:
        raise HTTPException(
            status_code=400,
            detail=f"readiness score {readiness.get('score', 0)} is below the threshold ({READINESS_SCORE_THRESHOLD})",
        )
    try:
        plan = compile_plan(run_id)
    except (PlanCompilerError, SchemaValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    context.event_logger.log("PLAN_GENERATED", {"run_id": run_id, "plan_id": plan.plan_id})
    context.latest_plan_id = plan.plan_id
    return HTMLResponse(content=_render_maestro_partial(run_id))


@app.get("/v1/runs/{run_id}/plan")
def get_plan(run_id: str):
    context = _get_run_context(run_id)
    if not context.latest_plan_id:
        raise HTTPException(status_code=404, detail="plan not generated")
    plan_path = context.run_root / "plans" / f"{context.latest_plan_id}.json"
    if not plan_path.exists():
        raise HTTPException(status_code=404, detail="plan file missing")
    return JSONResponse(content=json.loads(plan_path.read_text(encoding="utf-8")))


@app.get("/ui/console", response_class=HTMLResponse)
def ui_console():
    return HTMLResponse(_CONSOLE_HTML)
