from __future__ import annotations

import json
from pathlib import Path
from typing import List

from fastapi.responses import JSONResponse

from ..models import GitBranchCreateArgs, RunCommandArgs
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


def run_git_branch_create(run_dir: Path, args: GitBranchCreateArgs):
    try:
        repo_path = safe_join(run_dir, args.repo_dir or ".")
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    command: List[str] = ["git", "branch"]
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
    try:
        payload = json.loads(branch_result.body.decode("utf-8"))
    except json.JSONDecodeError:
        return _error_response(
            "INTERNAL",
            "failed to parse branch response",
            {"phase": "branch"},
        )
    if not payload.get("ok"):
        return branch_result

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
        try:
            checkout_payload = json.loads(checkout_result.body.decode("utf-8"))
        except json.JSONDecodeError:
            return _error_response(
                "INTERNAL",
                "failed to parse checkout response",
                {"phase": "checkout"},
            )
        if not checkout_payload.get("ok"):
            return checkout_result
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
