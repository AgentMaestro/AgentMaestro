from __future__ import annotations

import logging
from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import SAFE_COMMAND_OUTPUT_LIMIT
from ..models import RunCommandSafeArgs
from .policies.run_command_safe import SafeCommandDecision, evaluate_run_command_safe
from .subprocess_utils import run_subprocess, timeout_details

logger = logging.getLogger(__name__)


def _structured_result(
    *,
    ok: bool,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
    truncated: bool,
    normalized_command: list[str],
    policy_reason: str | None,
    duration_ms: int,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    timeout_seconds: int | None = None,
    timeout_source: str | None = None,
) -> dict[str, object]:
    return {
        "ok": ok,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "truncated": truncated,
        "normalized_command": normalized_command,
        "policy_reason": policy_reason,
        "duration_ms": duration_ms,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        **timeout_details(timeout_seconds, timeout_source),
    }


def _policy_rejection_response(decision: SafeCommandDecision, reason: str) -> JSONResponse:
    logger.warning(
        "run_command_safe rejected command=%s cwd=%s reason=%s",
        decision.redacted_command,
        decision.resolved_cwd,
        reason,
    )
    result = _structured_result(
        ok=False,
        exit_code=None,
        stdout="",
        stderr=reason,
        timed_out=False,
        truncated=False,
        normalized_command=decision.normalized_command,
        policy_reason=reason,
        duration_ms=0,
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "error": {
                "code": "tool_runner.POLICY_REJECTED",
                "message": reason,
                "details": {
                    "normalized_command": decision.normalized_command,
                    "cwd": str(decision.resolved_cwd) if decision.resolved_cwd else None,
                },
            },
            "result": result,
        },
    )


def _execution_error_response(decision: SafeCommandDecision, code: str, message: str) -> JSONResponse:
    logger.warning(
        "run_command_safe execution failed command=%s cwd=%s code=%s message=%s",
        decision.redacted_command,
        decision.resolved_cwd,
        code,
        message,
    )
    result = _structured_result(
        ok=False,
        exit_code=None,
        stdout="",
        stderr=message,
        timed_out=False,
        truncated=False,
        normalized_command=decision.normalized_command,
        policy_reason=None,
        duration_ms=0,
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "error": {
                "code": f"tool_runner.{code}",
                "message": message,
                "details": {
                    "normalized_command": decision.normalized_command,
                    "cwd": str(decision.resolved_cwd) if decision.resolved_cwd else None,
                },
            },
            "result": result,
        },
    )


def run_command_safe(
    run_dir: Path,
    args: RunCommandSafeArgs,
    policy: dict | None = None,
    *,
    max_output_bytes: int | None = None,
):
    decision = evaluate_run_command_safe(
        run_dir=run_dir,
        argv=args.argv,
        cwd=args.cwd,
        policy=policy,
    )
    if not decision.allowed:
        return _policy_rejection_response(decision, str(decision.policy_reason or "command rejected by policy"))

    resolved_cwd = decision.resolved_cwd
    if resolved_cwd is None:
        return _policy_rejection_response(decision, "working directory resolution failed")
    if not resolved_cwd.exists():
        return _execution_error_response(decision, "NOT_FOUND", f"working directory '{args.cwd}' does not exist")

    output_limit = min(max_output_bytes or SAFE_COMMAND_OUTPUT_LIMIT, SAFE_COMMAND_OUTPUT_LIMIT)
    logger.info(
        "run_command_safe allowed command=%s cwd=%s timeout_s=%s",
        decision.redacted_command,
        resolved_cwd,
        args.timeout_seconds,
    )
    try:
        completed = run_subprocess(
            decision.normalized_command,
            cwd=resolved_cwd,
            timeout_seconds=args.timeout_seconds,
            max_output_bytes=output_limit,
        )
    except FileNotFoundError as exc:
        return _execution_error_response(decision, "NOT_FOUND", str(exc))
    except PermissionError as exc:
        return _execution_error_response(decision, "PERMISSION_DENIED", str(exc))
    except (ValueError, OSError) as exc:
        return _execution_error_response(decision, "INVALID_ARGUMENT", str(exc))

    stderr = completed.stderr
    if completed.timed_out and not stderr:
        stderr = (
            f"command timed out after {args.timeout_seconds} seconds "
            f"(source=args.timeout_seconds)"
        )

    logger.info(
        "run_command_safe completed command=%s exit_code=%s duration_ms=%s timed_out=%s",
        decision.redacted_command,
        completed.exit_code,
        completed.duration_ms,
        completed.timed_out,
    )
    result = _structured_result(
        ok=not completed.timed_out and completed.exit_code == 0,
        exit_code=completed.exit_code,
        stdout=completed.stdout,
        stderr=stderr,
        timed_out=completed.timed_out,
        truncated=completed.truncated,
        normalized_command=decision.normalized_command,
        policy_reason=None,
        duration_ms=completed.duration_ms,
        stdout_truncated=completed.stdout_truncated,
        stderr_truncated=completed.stderr_truncated,
        timeout_seconds=args.timeout_seconds,
        timeout_source="args.timeout_seconds",
    )
    return JSONResponse(status_code=200, content={"ok": True, "result": result})
