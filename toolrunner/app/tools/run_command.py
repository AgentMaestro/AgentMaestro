from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from fastapi.responses import JSONResponse

from ..models import RunCommandArgs
from ..sandbox import safe_join


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


def _truncate_output(payload: bytes | str | None, max_bytes: int) -> tuple[str, bool]:
    if payload is None:
        return "", False
    if isinstance(payload, str):
        data = payload.encode("utf-8", errors="replace")
    else:
        data = payload
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace"), False
    ellipsis = "\u2026"
    ellipsis_bytes = ellipsis.encode("utf-8")
    available = max(max_bytes - len(ellipsis_bytes), 0)
    truncated_data = data[:available]
    safe_bytes = truncated_data
    safe_text = ""
    while safe_bytes:
        try:
            safe_text = safe_bytes.decode("utf-8")
            break
        except UnicodeDecodeError as exc:
            safe_bytes = safe_bytes[: exc.start]
    else:
        safe_text = ""
    text = safe_text + ellipsis
    return text, True


def _terminate_tree(proc: subprocess.Popen):
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass
    else:
        try:
            proc.kill()
        except Exception:
            pass


def run_command(run_dir: Path, args: RunCommandArgs):
    try:
        working_dir = safe_join(run_dir, args.cwd or ".")
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))
    if not working_dir.exists():
        return _error_response(
            "NOT_FOUND",
            f"working directory '{args.cwd}' does not exist",
            {"cwd": args.cwd},
        )

    merged_env = os.environ.copy()
    if args.env:
        merged_env.update(args.env)

    timeout_s = args.timeout_ms / 1000 if args.timeout_ms > 0 else None
    input_data = args.stdin_text.encode("utf-8") if args.stdin_text is not None else None
    start = time.monotonic()
    stdout_bytes: bytes | None = None
    stderr_bytes: bytes | None = None
    exit_code: int | None = None
    stdout_truncated = False
    stderr_truncated = False
    timed_out = False
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            args.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_data is not None else None,
            cwd=working_dir,
            env=merged_env,
            creationflags=creationflags,
        )
        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                input=input_data, timeout=timeout_s
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            if proc:
                _terminate_tree(proc)
                try:
                    stdout_bytes, stderr_bytes = proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    stdout_bytes = exc.stdout or b""
                    stderr_bytes = exc.stderr or b""
                finally:
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
            exit_code = None
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
    finally:
        duration_ms = int(round((time.monotonic() - start) * 1000))

    stdout, stdout_truncated = _truncate_output(stdout_bytes, args.max_output_bytes)
    stderr, stderr_truncated = _truncate_output(stderr_bytes, args.max_output_bytes)
    result = {
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": result})
