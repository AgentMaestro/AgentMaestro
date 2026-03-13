from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import resolve_policy_path
from ..models import GitLogArgs, RunCommandArgs
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


def _isoformat_epoch_seconds(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _run_command(
    repo_path: Path,
    command: list[str],
    timeout_ms: int,
    max_output_bytes: int,
) -> tuple[dict | None, JSONResponse | None]:
    response = run_command(
        repo_path,
        RunCommandArgs(
            cmd=command,
            cwd=".",
            timeout_ms=timeout_ms,
            max_output_bytes=max_output_bytes,
        ),
    )
    payload = _decode_result(response)
    if payload is None:
        return None, _error_response("INTERNAL", "failed to parse git log output")
    if not payload.get("ok"):
        return None, response
    result = payload.get("result") or {}
    if result.get("timed_out"):
        effective_timeout_ms = result.get("timeout_ms") or timeout_ms
        timeout_source = result.get("timeout_source") or "args.timeout_ms"
        return None, _error_response(
            "TIMED_OUT",
            f"git_log timed out after {effective_timeout_ms} ms (source={timeout_source})",
            {
                "command": command,
                "timeout_ms": effective_timeout_ms,
                "timeout_source": timeout_source,
            },
        )
    return result, None


def run_git_log(run_dir: Path, args: GitLogArgs, policy: dict | None = None):
    repo_dir = args.repo_dir or "."
    try:
        repo_path = resolve_policy_path(run_dir, repo_dir, policy)
    except ValueError as exc:
        error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
        return _error_response(error_code, str(exc))

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

    result, error = _run_command(
        repo_path,
        command,
        args.timeout_ms,
        args.max_output_bytes,
    )
    if error:
        return error

    stdout = _normalize_newlines(result.get("stdout", ""))
    commits: list[dict[str, object]] = []
    skipped_record_count = 0
    malformed_author_time_count = 0
    for line in stdout.splitlines():
        if not line:
            continue
        parts = line.split("\x00")
        if len(parts) < 5:
            skipped_record_count += 1
            continue
        oid, author_name, author_email, author_time, subject = parts[:5]
        author_time_epoch: int | None
        try:
            author_time_epoch = int(author_time)
        except ValueError:
            author_time_epoch = None
            malformed_author_time_count += 1
        commits.append(
            {
                "oid": oid,
                "author_name": author_name,
                "author_email": author_email,
                "author_time_epoch": author_time_epoch,
                "author_time_iso": _isoformat_epoch_seconds(author_time_epoch),
                "subject": subject,
            }
        )

    stderr = _normalize_newlines(result.get("stderr", ""))
    stdout_truncated = result.get("stdout_truncated", False)
    stderr_truncated = result.get("stderr_truncated", False)
    parse_warnings: list[str] = []
    if stdout_truncated:
        parse_warnings.append("stdout truncated; commits may be incomplete")
    if skipped_record_count:
        parse_warnings.append(f"skipped {skipped_record_count} malformed log record(s)")
    if malformed_author_time_count:
        parse_warnings.append(
            f"{malformed_author_time_count} commit record(s) had invalid author timestamps"
        )
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
            "parse_stats": {
                "skipped_record_count": skipped_record_count,
                "malformed_author_time_count": malformed_author_time_count,
            },
        },
    }
    if parse_warnings:
        response_payload["result"]["parse_warning"] = "; ".join(parse_warnings)
    return JSONResponse(
        status_code=200,
        content=response_payload,
    )
