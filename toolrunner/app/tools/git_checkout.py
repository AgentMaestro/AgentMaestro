from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from ..models import GitCheckoutArgs, RunCommandArgs
from ..sandbox import safe_join
from .run_command import run_command


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


def _decode_result(response: JSONResponse) -> dict | None:
    try:
        return json.loads(response.body.decode("utf-8"))
    except Exception:
        return None


def _is_detached(stdout: str, exit_code: int | None) -> bool:
    if exit_code is None or exit_code != 0:
        return False
    lowered = stdout.lower()
    return (
        "detached head" in lowered
        or "switched to commit" in lowered
        or "note: switching to" in lowered
    )


def run_git_checkout(run_dir: Path, args: GitCheckoutArgs):
    repo_dir = args.repo_dir or "."
    try:
        repo_path = safe_join(run_dir, repo_dir)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    command = ["git", "checkout"]
    if args.create:
        command.extend(["-b", args.ref])
    else:
        command.extend(["--", args.ref])

    run_response = run_command(
        repo_path,
        RunCommandArgs(
            cmd=command,
            cwd=".",
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
        ),
    )

    payload = _decode_result(run_response)
    if payload is None:
        return _error_response("INTERNAL", "failed to parse git checkout output")
    if not payload.get("ok"):
        return run_response

    result = payload.get("result", {}) or {}
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "repo_dir": repo_dir,
                "ref": args.ref,
                "detached": _is_detached(stdout, result.get("exit_code")),
                "raw": {
                    "stdout": stdout.replace("\r\n", "\n"),
                    "stderr": stderr,
                    "stdout_truncated": result.get("stdout_truncated", False),
                    "stderr_truncated": result.get("stderr_truncated", False),
                },
            },
        },
    )
