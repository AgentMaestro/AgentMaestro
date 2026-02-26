from __future__ import annotations

import json
from pathlib import Path
from typing import List

from fastapi.responses import JSONResponse

from ..models import GitPushArgs, RunCommandArgs
from ..sandbox import safe_join
from .run_command import run_command


def _error_response(code: str, message: str, details: dict | None = None, status_code: int = 400):
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


def run_git_push(run_dir: Path, args: GitPushArgs):
    try:
        repo_path = safe_join(run_dir, args.repo_dir or ".")
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    command: List[str] = ["git", "push"]
    if args.set_upstream:
        command.append("-u")
    command.append(args.remote)
    command.extend(["--", args.ref])
    if args.force:
        command.append("--force-with-lease")

    run_result = run_command(
        repo_path,
        RunCommandArgs(
            cmd=command,
            cwd=".",
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
        ),
    )
    try:
        payload = json.loads(run_result.body.decode("utf-8"))
    except json.JSONDecodeError:
        return _error_response("INTERNAL", "failed to parse git push response")
    if not payload.get("ok"):
        return run_result

    result = payload["result"]
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "repo_dir": args.repo_dir or ".",
                "remote": args.remote,
                "ref": args.ref,
                "pushed": True,
                "raw": {
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "stdout_truncated": result.get("stdout_truncated", False),
                    "stderr_truncated": result.get("stderr_truncated", False),
                },
            },
        },
    )
