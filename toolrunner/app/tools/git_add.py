from __future__ import annotations

import json
from pathlib import Path
from typing import List

from fastapi.responses import JSONResponse

from ..config import resolve_path_from_base, resolve_policy_path
from ..models import GitAddArgs, RunCommandArgs
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


def run_git_add(run_dir: Path, args: GitAddArgs, policy: dict | None = None):
    try:
        repo_path = resolve_policy_path(run_dir, args.repo_dir or ".", policy)
    except ValueError as exc:
        error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
        return _error_response(error_code, str(exc))

    command: List[str] = ["git", "add"]
    if args.all:
        command.append("-A")
    elif args.intent_to_add:
        command.append("-N")

    normalized_paths: List[str] = []
    if args.paths:
        for rel_path in args.paths:
            try:
                target = resolve_path_from_base(repo_path, rel_path, policy)
            except ValueError as exc:
                error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
                return _error_response(error_code, str(exc))
            try:
                normalized = target.relative_to(repo_path).as_posix()
            except ValueError:
                normalized = target.as_posix()
            normalized_paths.append(normalized)
        command.append("--")
        command.extend(normalized_paths)

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
        return _error_response("INTERNAL", "failed to parse git add response")
    if not payload.get("ok"):
        return run_result

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "repo_dir": args.repo_dir or ".",
                "staged_paths": normalized_paths,
                "raw": {
                    "stdout": payload["result"].get("stdout", ""),
                    "stderr": payload["result"].get("stderr", ""),
                    "stdout_truncated": payload["result"].get("stdout_truncated", False),
                    "stderr_truncated": payload["result"].get("stderr_truncated", False),
                },
            },
        },
    )
