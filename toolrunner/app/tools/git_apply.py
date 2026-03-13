from __future__ import annotations

import json
from pathlib import Path
from typing import List

from fastapi.responses import JSONResponse

from ..config import resolve_policy_path
from ..models import GitApplyArgs, RunCommandArgs
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


def _list_reject_files(repo_path: Path) -> set[str]:
    rejects: set[str] = set()
    for path in repo_path.rglob("*.rej"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(repo_path).as_posix()
        except ValueError:
            relative = path.as_posix()
        rejects.add(relative)
    return rejects


def _patch_path(value: str) -> str | None:
    candidate = value.strip()
    if not candidate or candidate == "/dev/null":
        return None
    if candidate.startswith("a/") or candidate.startswith("b/"):
        return candidate[2:]
    return candidate


def _extract_touched_paths(patch_unified: str) -> list[str]:
    touched_paths: list[str] = []
    seen: set[str] = set()

    def _add(path_value: str | None) -> None:
        if not path_value or path_value in seen:
            return
        seen.add(path_value)
        touched_paths.append(path_value)

    for line in patch_unified.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                _add(_patch_path(parts[2]))
                _add(_patch_path(parts[3]))
            continue
        if line.startswith("rename to ") or line.startswith("copy to "):
            _add(_patch_path(line.split(" ", 2)[2]))
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            _add(_patch_path(line[4:]))
    return touched_paths


def run_git_apply(run_dir: Path, args: GitApplyArgs, policy: dict | None = None):
    try:
        repo_dir = args.repo_dir or "."
        repo_path = resolve_policy_path(run_dir, repo_dir, policy)
    except ValueError as exc:
        error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
        return _error_response(error_code, str(exc))

    command: List[str] = ["git", "apply"]
    command.append(f"-p{args.strip_prefix}")
    if args.reject:
        command.append("--reject")
    if args.check:
        command.append("--check")

    pre_rejects = _list_reject_files(repo_path) if args.reject else set()

    run_result = run_command(
        repo_path,
        RunCommandArgs(
            cmd=command,
            cwd=".",
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
            stdin_text=args.patch_unified,
        ),
    )

    try:
        payload = json.loads(run_result.body.decode("utf-8"))
    except json.JSONDecodeError:
        return _error_response("INTERNAL", "failed to parse git apply response")
    if not payload.get("ok"):
        return run_result

    result_payload = payload["result"]
    exit_code = result_payload.get("exit_code")
    check_passed = exit_code == 0 if args.check and exit_code is not None else None
    applied = (not args.check) and exit_code == 0
    touched_paths = _extract_touched_paths(args.patch_unified)
    post_rejects = _list_reject_files(repo_path) if args.reject else set()
    new_rejects = sorted(post_rejects.difference(pre_rejects))
    rejects_created = bool(new_rejects)
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "repo_dir": repo_dir,
                "strip_prefix": args.strip_prefix,
                "check_passed": check_passed,
                "applied": applied,
                "touched_paths": touched_paths,
                "rejects_created": rejects_created,
                "reject_paths": new_rejects,
                "raw": {
                    "stdout": result_payload.get("stdout", ""),
                    "stderr": result_payload.get("stderr", ""),
                    "stdout_truncated": result_payload.get("stdout_truncated", False),
                    "stderr_truncated": result_payload.get("stderr_truncated", False),
                },
            },
        },
    )
