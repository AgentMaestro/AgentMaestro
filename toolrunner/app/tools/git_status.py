from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from ..models import GitStatusArgs, RunCommandArgs
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


_FIELD_SPLITS = {"1": 8, "2": 9, "u": 8}


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n")


def _extract_path(line: str, maxsplit: int) -> str:
    trimmed = line.split("\t", 1)[0]
    parts = trimmed.split(" ", maxsplit)
    return parts[-1] if parts else ""


def _parse_branch_line(line: str, branch_info: dict) -> None:
    remainder = line[2:].lstrip()
    if not remainder:
        return
    key, _, value = remainder.partition(" ")
    value = value.strip()
    if key == "branch.oid":
        branch_info["head_oid"] = None if value == "(initial)" else value or None
    elif key == "branch.head":
        branch_info["name"] = value or None
    elif key == "branch.upstream":
        branch_info["upstream"] = value or None
    elif key == "branch.ab":
        ahead = 0
        behind = 0
        parts = value.split()
        if parts:
            try:
                ahead = int(parts[0].lstrip("+"))
            except ValueError:
                ahead = 0
        if len(parts) > 1:
            try:
                behind = int(parts[1].lstrip("-"))
            except ValueError:
                behind = 0
        branch_info["ahead"] = ahead
        branch_info["behind"] = behind


def _parse_status_lines(
    stdout: str, include_untracked: bool
) -> tuple[list[str], list[str], list[str], list[str], dict]:
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicts: list[str] = []
    branch_info = {
        "name": None,
        "head_oid": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
    }

    for raw_line in stdout.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        if line.startswith("#"):
            _parse_branch_line(line, branch_info)
            continue
        if line.startswith("? "):
            if include_untracked:
                untracked.append(line[2:])
            continue
        prefix = line[0]
        fields = _FIELD_SPLITS.get(prefix, 8)
        if prefix in {"1", "2"}:
            path = _extract_path(line, fields)
            xy = line[2:4]
            if len(xy) == 2:
                if xy[0] != ".":
                    staged.append(path)
                if xy[1] != ".":
                    unstaged.append(path)
            continue
        if prefix == "u":
            path = _extract_path(line, fields)
            conflicts.append(path)
            continue
    return staged, unstaged, untracked, conflicts, branch_info


def run_git_status(run_dir: Path, args: GitStatusArgs):
    repo_dir = args.repo_dir or "."
    try:
        repo_path = safe_join(run_dir, repo_dir)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    command = ["git", "status", f"--porcelain={args.porcelain}", "--branch"]
    if not args.include_untracked:
        command.append("--untracked-files=no")

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
        return _error_response("INTERNAL", "failed to parse git status output")

    if not payload.get("ok"):
        return run_result

    result_payload = payload.get("result", {}) or {}
    stdout = result_payload.get("stdout", "")
    stderr = result_payload.get("stderr", "")
    normalized_stdout = _normalize_newlines(stdout)

    staged, unstaged, untracked, conflicts, branch_info = _parse_status_lines(
        normalized_stdout, args.include_untracked
    )

    branch_name = branch_info["name"]
    detached = bool(branch_name and "(detached" in branch_name)

    is_clean = (
        not staged
        and not unstaged
        and not conflicts
        and (not args.include_untracked or not untracked)
    )

    result = {
        "repo_dir": repo_dir,
        "branch": {
            "name": branch_name,
            "head_oid": branch_info["head_oid"],
            "upstream": branch_info["upstream"],
            "ahead": branch_info["ahead"],
            "behind": branch_info["behind"],
            "detached": detached,
        },
        "is_clean": is_clean,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "conflicts": conflicts,
        "raw": {
            "stdout": normalized_stdout,
            "stderr": stderr,
            "stdout_truncated": result_payload.get("stdout_truncated", False),
            "stderr_truncated": result_payload.get("stderr_truncated", False),
        },
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": result})
