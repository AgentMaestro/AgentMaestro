from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from ..models import GitLogArgs, RunCommandArgs
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


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n")


def _run_command(repo_path: Path, command: list[str]) -> tuple[dict | None, JSONResponse | None]:
    response = run_command(
        repo_path,
        RunCommandArgs(
            cmd=command,
            cwd=".",
        ),
    )
    payload = _decode_result(response)
    if payload is None:
        return None, _error_response("INTERNAL", "failed to parse git log output")
    if not payload.get("ok"):
        return None, response
    return payload["result"], None


def run_git_log(run_dir: Path, args: GitLogArgs):
    repo_dir = args.repo_dir or "."
    try:
        repo_path = safe_join(run_dir, repo_dir)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    if not repo_path.exists():
        return _error_response("NOT_FOUND", f"repo_dir '{repo_dir}' does not exist")

    if args.ref.startswith("-"):
        return _error_response("INVALID_ARGUMENT", "ref must not start with '-'")

    format_string = "%H%x00%an%x00%ae%x00%at%x00%s"
    command = [
        "git",
        "log",
        f"--max-count={args.max_count}",
        args.ref,
        f"--format={format_string}",
    ]

    result, error = _run_command(repo_path, command)
    if error:
        return error

    stdout = _normalize_newlines(result.get("stdout", ""))
    commits: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line:
            continue
        parts = line.split("\x00")
        if len(parts) < 5:
            continue
        oid, author_name, author_email, author_time, subject = parts[:5]
        try:
            author_time_epoch = int(author_time)
        except ValueError:
            author_time_epoch = 0
        commits.append(
            {
                "oid": oid,
                "author_name": author_name,
                "author_email": author_email,
                "author_time_epoch": author_time_epoch,
                "subject": subject,
            }
        )

    stderr = _normalize_newlines(result.get("stderr", ""))
    stdout_truncated = result.get("stdout_truncated", False)
    stderr_truncated = result.get("stderr_truncated", False)
    response_payload = {
        "ok": True,
        "result": {
            "repo_dir": repo_dir,
            "ref": args.ref,
            "max_count": args.max_count,
            "commits": commits,
            "raw": {
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
        },
    }
    if stdout_truncated:
        response_payload["result"]["parse_warning"] = "stdout truncated; commits may be incomplete"
    return JSONResponse(
        status_code=200,
        content=response_payload,
    )
