from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from fastapi.responses import JSONResponse

from ..models import LintArgs, RunCommandArgs
from ..sandbox import safe_join
from .run_command import run_command

TOOL_DEFAULT_ARGS: Dict[str, List[str]] = {
    "ruff": ["check"],
    "flake8": ["."],
    "eslint": ["."],
    "prettier": ["."],
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


def _ensure_output_format(args: List[str]) -> List[str]:
    if any(arg.startswith("--output-format") for arg in args):
        return args
    return args + ["--output-format=json"]


def _build_command(run_dir: Path, args: LintArgs) -> List[str]:
    if args.tool == "command":
        return list(args.cmd or [])

    if args.tool == "ruff":
        command: List[str] = ["python", "-m", "ruff"]
    else:
        command = [args.tool]

    tool_args = list(args.args) if args.args else list(TOOL_DEFAULT_ARGS.get(args.tool, []))
    if args.tool == "ruff":
        tool_args = _ensure_output_format(tool_args)
    command.extend(tool_args)

    if args.paths:
        for rel_path in args.paths:
            abs_path = safe_join(run_dir, rel_path)
            command.append(str(abs_path))
    return command


def _parse_ruff_issues(stdout: str) -> List[Dict[str, object]]:
    issues: List[Dict[str, object]] = []
    data = json.loads(stdout)

    entries = data if isinstance(data, list) else [data]
    for item in entries:
        if not isinstance(item, dict):
            continue
        line_value = item.get("row") or item.get("line") or item.get("line_no")
        col_value = item.get("column") or item.get("col") or item.get("column_offset")
        try:
            line = int(line_value) if line_value is not None else 0
        except (ValueError, TypeError):
            line = 0
        try:
            col = int(col_value) if col_value is not None else 0
        except (ValueError, TypeError):
            col = 0
        severity = item.get("severity") or item.get("type") or "error"
        issues.append(
            {
                "path": item.get("path") or item.get("filename") or "",
                "line": line,
                "col": col,
                "code": item.get("code") or "",
                "severity": severity,
                "message": item.get("message") or "",
            }
        )
    return issues


def run_linters(run_dir: Path, args: LintArgs):
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
    stderr = result.get("stderr", "")
    issues: List[Dict[str, object]] = []
    parse_warning: str | None = None
    parse_source = "none"
    stdout_truncated = result.get("stdout_truncated", False)
    if args.parse == "ruff":
        parse_source = "stdout" if stdout else ("stderr" if stderr else "none")
        if stdout_truncated:
            parse_warning = "ruff output truncated; issues not parsed"
        else:
            for source, text in (("stdout", stdout), ("stderr", stderr)):
                if not text:
                    continue
                try:
                    issues = _parse_ruff_issues(text)
                    parse_source = source
                    break
                except json.JSONDecodeError:
                    continue
            else:
                parse_warning = "ruff output is not valid JSON"

    final = {
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms", 0),
        "timed_out": result.get("timed_out", False),
        "issues": issues,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": result.get("stderr_truncated", False),
        "parse_mode": args.parse,
        "parse_source": parse_source,
        "parse_warning": parse_warning,
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
