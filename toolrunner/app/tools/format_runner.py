from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from fastapi.responses import JSONResponse

from ..models import FormatArgs, RunCommandArgs
from ..sandbox import safe_join
from .run_command import run_command

FORMAT_DEFAULT_ARGS: Dict[str, List[str]] = {
    "ruff_format": [],
    "black": [],
    "prettier": [],
}


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


def _build_command(run_dir: Path, args: FormatArgs) -> List[str]:
    if args.tool == "command":
        if not args.cmd:
            raise ValueError("cmd is required when tool is command")
        return list(args.cmd or [])

    if args.tool == "ruff_format":
        command = ["python", "-m", "ruff", "format"]
    else:
        command = [args.tool]

    tool_args = list(args.args) if args.args else list(FORMAT_DEFAULT_ARGS.get(args.tool, []))
    if args.tool == "ruff_format":
        if args.mode == "check":
            tool_args = ["--check", "--diff"] + tool_args
        else:
            tool_args = tool_args + ["--diff"]

    command.extend(tool_args)

    if args.paths:
        for rel_path in args.paths:
            abs_path = safe_join(run_dir, rel_path)
            command.append(str(abs_path))
    return command


def _collect_changed_files(stdout: str) -> List[str]:
    files: List[str] = []
    for line in stdout.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                continue
            if path.startswith("b/"):
                path = path[2:]
            files.append(path)
    return sorted(set(files))


def run_formatter(run_dir: Path, args: FormatArgs):
    try:
        command = _build_command(run_dir, args)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    run_args = RunCommandArgs(
        cmd=command,
        cwd=args.cwd,
        timeout_ms=args.timeout_ms,
        max_output_bytes=args.max_output_bytes,
    )
    response = run_command(run_dir, run_args)
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as exc:  # pragma: no cover
        return _error_response("INTERNAL", str(exc))

    if not payload.get("ok"):
        return response

    result = payload["result"]
    stdout = result.get("stdout", "")
    changed_files = _collect_changed_files(stdout) if args.tool == "ruff_format" else []
    parse_warning: str | None = None
    if args.tool == "ruff_format" and result.get("stdout_truncated", False):
        parse_warning = "stdout truncated; changed_files may be incomplete"

    final = {
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms", 0),
        "timed_out": result.get("timed_out", False),
        "changed_files": changed_files,
        "parse_mode": args.tool,
        "parse_warning": parse_warning,
        "stdout": stdout,
        "stderr": result.get("stderr", ""),
        "stdout_truncated": result.get("stdout_truncated", False),
        "stderr_truncated": result.get("stderr_truncated", False),
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
