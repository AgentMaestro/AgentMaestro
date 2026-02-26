from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from ..models import GitCommitArgs, RunCommandArgs
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


def _run_git_command(
    repo_path: Path,
    cmd: list[str],
    timeout_ms: int,
    max_output_bytes: int,
) -> tuple[dict | None, JSONResponse | None]:
    response = run_command(
        repo_path,
        RunCommandArgs(
            cmd=cmd,
            cwd=".",
            timeout_ms=timeout_ms,
            max_output_bytes=max_output_bytes,
        ),
    )
    payload = _decode_result(response)
    if payload is None:
        return None, _error_response("INTERNAL", "failed to parse git output")
    if not payload.get("ok"):
        return None, response
    return payload["result"], None


def _check_exit_code(result: dict, error_format: str) -> JSONResponse | None:
    exit_code = result.get("exit_code")
    if exit_code is None or exit_code == 0:
        return None
    combined = f"{result.get('stdout','')}\n{result.get('stderr','')}"
    text = combined.strip()
    if "nothing to commit" in text.lower():
        return _error_response("CONFLICT", "nothing to commit")
    return _error_response("INVALID_ARGUMENT", text or error_format, {"stdout": result.get("stdout"), "stderr": result.get("stderr")})


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n")


def run_git_commit(run_dir: Path, args: GitCommitArgs):
    repo_dir = args.repo_dir or "."
    try:
        repo_path = safe_join(run_dir, repo_dir)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    if not repo_path.exists():
        return _error_response("NOT_FOUND", f"repo_dir '{repo_dir}' does not exist")

    if args.paths_to_add and args.add_all:
        return _error_response(
            "INVALID_ARGUMENT",
            "paths_to_add and add_all cannot both be set",
        )

    normalized_paths: list[str] = []
    if args.paths_to_add:
        for rel_path in args.paths_to_add:
            try:
                target = safe_join(repo_path, rel_path)
            except ValueError as exc:
                return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))
            try:
                relative = target.relative_to(repo_path).as_posix()
            except ValueError:
                relative = target.as_posix()
            normalized_paths.append(relative)

    if normalized_paths:
        add_cmd = ["git", "add", "--"] + normalized_paths
        result, error = _run_git_command(repo_path, add_cmd, args.timeout_ms, args.max_output_bytes)
        if error:
            return error
        exit_error = _check_exit_code(result, "git add failed")
        if exit_error:
            return exit_error

    if args.add_all:
        add_all_cmd = ["git", "add", "-A"]
        result, error = _run_git_command(repo_path, add_all_cmd, args.timeout_ms, args.max_output_bytes)
        if error:
            return error
        exit_error = _check_exit_code(result, "git add --all failed")
        if exit_error:
            return exit_error

    commit_cmd = ["git", "commit", "-m", args.message]
    if args.signoff:
        commit_cmd.append("--signoff")
    if args.amend:
        commit_cmd.append("--amend")

    commit_result, commit_error = _run_git_command(
        repo_path, commit_cmd, args.timeout_ms, args.max_output_bytes
    )
    if commit_error:
        return commit_error
    exit_error = _check_exit_code(commit_result, "git commit failed")
    if exit_error:
        return exit_error

    rev_result, rev_error = _run_git_command(
        repo_path, ["git", "rev-parse", "HEAD"], args.timeout_ms, args.max_output_bytes
    )
    if rev_error:
        return rev_error
    commit_oid = rev_result.get("stdout", "").strip()

    diff_result, diff_error = _run_git_command(
        repo_path,
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        args.timeout_ms,
        args.max_output_bytes,
    )
    if diff_error:
        return diff_error
    changed_files_list = [line for line in diff_result.get("stdout", "").splitlines() if line]
    changed_files = len(changed_files_list)
    changed_files_truncated = diff_result.get("stdout_truncated", False)

    summary = args.message.splitlines()[0]

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "repo_dir": repo_dir,
                "commit_oid": commit_oid,
                "summary": summary,
                "changed_files": changed_files,
                "changed_files_truncated": changed_files_truncated,
                "raw": {
                    "stdout": _normalize_newlines(commit_result.get("stdout", "")),
                    "stderr": commit_result.get("stderr", ""),
                    "stdout_truncated": commit_result.get("stdout_truncated", False),
                    "stderr_truncated": commit_result.get("stderr_truncated", False),
                },
            },
        },
    )
