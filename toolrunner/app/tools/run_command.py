from __future__ import annotations

from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import resolve_policy_path
from ..models import RunCommandArgs
from .subprocess_utils import run_subprocess, timeout_details


def _error_response(
    code: str,
    message: str,
    details: dict | None = None,
    status_code: int = 400,
):
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


def run_command(run_dir: Path, args: RunCommandArgs, policy: dict | None = None):
    try:
        working_dir = resolve_policy_path(run_dir, args.cwd or ".", policy)
    except ValueError as exc:
        error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
        return _error_response(error_code, str(exc), {"cwd": args.cwd})
    if not working_dir.exists():
        return _error_response(
            "NOT_FOUND",
            f"working directory '{args.cwd}' does not exist",
            {"cwd": args.cwd, "resolved_cwd": str(working_dir)},
        )

    timeout_seconds = args.timeout_ms / 1000 if args.timeout_ms > 0 else None
    try:
        completed = run_subprocess(
            args.cmd,
            cwd=working_dir,
            env=args.env,
            timeout_seconds=int(timeout_seconds) if timeout_seconds is not None else None,
            max_output_bytes=args.max_output_bytes,
            stdin_text=args.stdin_text,
        )
    except FileNotFoundError as exc:
        return _error_response(
            "NOT_FOUND",
            str(exc),
            {"cmd0": args.cmd[0] if args.cmd else None},
        )
    except PermissionError as exc:
        return _error_response("PERMISSION_DENIED", str(exc))
    except ValueError as exc:
        return _error_response("INVALID_ARGUMENT", str(exc))
    except OSError as exc:
        return _error_response("INVALID_ARGUMENT", str(exc))

    result = {
        "exit_code": completed.exit_code,
        "duration_ms": completed.duration_ms,
        "timed_out": completed.timed_out,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "stdout_truncated": completed.stdout_truncated,
        "stderr_truncated": completed.stderr_truncated,
        **timeout_details(int(timeout_seconds) if timeout_seconds is not None else None, "args.timeout_ms"),
    }
    if completed.timed_out and not result["stderr"]:
        result["stderr"] = (
            f"command timed out after {result['timeout_ms']} ms "
            f"(source={result['timeout_source']})"
        )
    return JSONResponse(status_code=200, content={"ok": True, "result": result})
