from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import resolve_policy_path
from ..models import GitBranchCreateArgs, RunCommandArgs
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


def _decode_response(response: JSONResponse, phase: str) -> tuple[dict | None, JSONResponse | None]:
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except json.JSONDecodeError:
        return None, _error_response(
            "INTERNAL",
            f"failed to parse {phase} response",
            {"phase": phase},
        )
    if not payload.get("ok"):
        return None, response
    result = payload.get("result") or {}
    if result.get("timed_out"):
        timeout_ms = result.get("timeout_ms")
        timeout_source = result.get("timeout_source") or "args.timeout_ms"
        return None, _error_response(
            "TIMED_OUT",
            f"git_branch_create timed out during {phase} after {timeout_ms} ms (source={timeout_source})",
            {
                "phase": phase,
                "timed_out": True,
                "timeout_ms": timeout_ms,
                "timeout_source": timeout_source,
            },
        )
    return result, None


def run_git_branch_create(run_dir: Path, args: GitBranchCreateArgs, policy: dict | None = None):
    try:
        repo_path = resolve_policy_path(run_dir, args.repo_dir or ".", policy)
    except ValueError as exc:
        error_code = (
            "PATH_OUTSIDE_WORKSPACE"
            if "path traversal outside of workspace" in str(exc)
            else "PATH_NOT_ALLOWED"
        )
        return _error_response(error_code, str(exc))

    command: list[str] = ["git", "branch"]
    if args.force:
        command.append("-f")
    command.extend(["--", args.name, args.start_point])

    branch_result = run_command(
        repo_path,
        RunCommandArgs(
            cmd=command,
            cwd=".",
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
        ),
    )
    _branch_payload, branch_error = _decode_response(branch_result, "branch")
    if branch_error:
        return branch_error

    did_checkout = False
    if args.checkout:
        checkout_result = run_command(
            repo_path,
            RunCommandArgs(
                cmd=["git", "switch", "--", args.name],
                cwd=".",
                timeout_ms=args.timeout_ms,
                max_output_bytes=args.max_output_bytes,
            ),
        )
        _checkout_payload, checkout_error = _decode_response(checkout_result, "checkout")
        if checkout_error:
            return checkout_error
        did_checkout = True

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "repo_dir": args.repo_dir or ".",
                "name": args.name,
                "checked_out": args.checkout and did_checkout,
            },
        },
    )
