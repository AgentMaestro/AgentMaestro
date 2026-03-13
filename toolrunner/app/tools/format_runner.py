from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from fastapi.responses import JSONResponse

from ..config import PYTHON_INTERPRETER, PYTHON_INTERPRETER_SOURCE, resolve_policy_path
from ..models import FormatArgs, RunCommandArgs
from .python_runner_support import detect_missing_python_module, missing_python_module_response
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


def _build_command(run_dir: Path, args: FormatArgs, policy: dict | None = None) -> List[str]:
    if args.tool == "command":
        if not args.cmd:
            raise ValueError("cmd is required when tool is command")
        return list(args.cmd or [])

    if args.tool == "ruff_format":
        command = [PYTHON_INTERPRETER, "-m", "ruff", "format"]
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
            abs_path = resolve_policy_path(run_dir, rel_path, policy)
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


def _invoke_run_command(run_dir: Path, run_args: RunCommandArgs, policy: dict | None):
    if policy is None:
        return run_command(run_dir, run_args)
    try:
        return run_command(run_dir, run_args, policy)
    except TypeError as exc:
        if "positional arguments but 3 were given" not in str(exc):
            raise
        return run_command(run_dir, run_args)


def run_formatter(run_dir: Path, args: FormatArgs, policy: dict | None = None):
    try:
        command = _build_command(run_dir, args, policy)
    except ValueError as exc:
        error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
        return _error_response(error_code, str(exc))

    run_args = RunCommandArgs(
        cmd=command,
        cwd=args.cwd,
        timeout_ms=args.timeout_ms,
        max_output_bytes=args.max_output_bytes,
    )
    response = _invoke_run_command(run_dir, run_args, policy)
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as exc:  # pragma: no cover
        return _error_response("INTERNAL", str(exc))

    if not payload.get("ok"):
        return response

    result = payload["result"]
    if args.tool == "ruff_format":
        missing_module = detect_missing_python_module(result, ("ruff",))
        if missing_module:
            return missing_python_module_response(
                tool_name="format_runner",
                module_name=missing_module,
                result=result,
                details={"runner_tool": args.tool, "mode": args.mode},
            )
    formatter_check_failed = (
        args.tool != "command"
        and args.mode == "check"
        and isinstance(result.get("exit_code"), int)
        and result.get("exit_code") != 0
        and not result.get("timed_out")
    )
    if result.get("timed_out"):
        return _error_response(
            "TIMED_OUT",
            f"formatter timed out after {args.timeout_ms} ms (source=args.timeout_ms)",
            {
                "tool": args.tool,
                "mode": args.mode,
                "cwd": args.cwd,
                "paths": args.paths or [],
                "timeout_ms": args.timeout_ms,
                "timeout_source": "args.timeout_ms",
            },
        )
    if isinstance(result.get("exit_code"), int) and result.get("exit_code") != 0 and not formatter_check_failed:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "error": {
                    "code": "tool_runner.FORMAT_FAILED",
                    "message": f"formatter exited with code {result['exit_code']}",
                    "details": {"tool": args.tool, "mode": args.mode, "cwd": args.cwd, "paths": args.paths or [], "command": command},
                },
                "result": result,
            },
        )
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
        "python_interpreter": PYTHON_INTERPRETER if args.tool == "ruff_format" else None,
        "python_interpreter_source": PYTHON_INTERPRETER_SOURCE if args.tool == "ruff_format" else None,
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
