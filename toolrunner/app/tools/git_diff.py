from __future__ import annotations

import json
from pathlib import Path
from typing import List

from fastapi.responses import JSONResponse

from ..config import resolve_path_from_base, resolve_policy_path
from ..models import GitDiffArgs, RunCommandArgs
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


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run_git_diff(run_dir: Path, args: GitDiffArgs, policy: dict | None = None):
    try:
        repo_path = resolve_policy_path(run_dir, args.repo_dir or ".", policy)
    except ValueError as exc:
        error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
        return _error_response(error_code, str(exc))

    command: List[str] = ["git", "diff"]
    if args.staged:
        command = ["git", "diff", "--cached"]
    if args.detect_renames:
        command.append("--find-renames")
    if args.context_lines is not None:
        command.append(f"-U{args.context_lines}")
    normalized_paths: List[str] = []
    if args.paths:
        for rel_path in args.paths:
            try:
                target = resolve_path_from_base(repo_path, rel_path, policy)
            except ValueError as exc:
                error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
                return _error_response(error_code, str(exc))
            try:
                rel = target.relative_to(repo_path).as_posix()
            except ValueError:
                rel = target.as_posix()
            normalized_paths.append(rel)
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
    except Exception:
        return _error_response("INTERNAL", "failed to parse git diff response")
    if not payload.get("ok"):
        return run_result

    result = payload["result"]
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    diff_text = _normalize_newlines(stdout)
    repo_dir_out = args.repo_dir or "."
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "repo_dir": repo_dir_out,
                "staged": args.staged,
                "paths": normalized_paths or None,
                "diff": diff_text,
                "truncated": result.get("stdout_truncated", False),
                "raw": {
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_truncated": result.get("stdout_truncated", False),
                    "stderr_truncated": result.get("stderr_truncated", False),
                },
            },
        },
    )
