from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from fastapi.responses import JSONResponse

from ..models import RunCommandArgs, TypecheckArgs
from ..sandbox import safe_join
from .run_command import run_command

TYPECHECK_DEFAULT_ARGS: Dict[str, List[str]] = {
    "pyright": ["--outputjson"],
    "mypy": ["--show-column-numbers", "--show-error-context"],
    "tsc": ["--pretty", "false"],
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


def _ensure_pyright_output(args: List[str]) -> List[str]:
    if any(arg.startswith("--outputjson") or arg.startswith("--outputjson-format") for arg in args):
        return args
    return args + ["--outputjson"]


def _build_command(run_dir: Path, args: TypecheckArgs) -> List[str]:
    if args.tool == "command":
        return list(args.cmd or [])

    if args.tool == "pyright":
        command = ["python", "-m", "pyright"]
    elif args.tool == "mypy":
        command = ["python", "-m", "mypy"]
    elif args.tool == "tsc":
        command = ["npx", "tsc"]
    else:
        command = [args.tool]

    tool_args = list(args.args) if args.args else list(TYPECHECK_DEFAULT_ARGS.get(args.tool, []))
    if args.tool == "pyright":
        tool_args = _ensure_pyright_output(tool_args)
    command.extend(tool_args)
    return command


def _parse_pyright(stdout: str) -> List[Dict[str, object]]:
    diagnostics: List[Dict[str, object]] = []
    payload = json.loads(stdout)
    general = payload.get("generalDiagnostics", [])
    for item in general:
        if not isinstance(item, dict):
            continue
        path = item.get("file")
        code = item.get("rule") or item.get("code") or item.get("messageId") or ""
        message = item.get("message") or ""
        severity = item.get("severity", "error")
        range_info = item.get("range", {})
        start = range_info.get("start", {})
        line = (start.get("line", 0) or 0) + 1
        col = (start.get("character") or start.get("column") or 0) + 1
        diagnostics.append(
            {
                "path": path or "",
                "line": line,
                "col": col,
                "severity": severity,
                "code": code,
                "message": message,
            }
        )
    return diagnostics


MYPY_PATTERN = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+)(?::(?P<col>\d+))?: (?P<severity>error|warning): (?P<message>.+?)(?: \[(?P<code>[^\]]+)\])?$"
)


def _parse_mypy(stdout: str) -> List[Dict[str, object]]:
    diagnostics: List[Dict[str, object]] = []
    for line in stdout.splitlines():
        match = MYPY_PATTERN.match(line.strip())
        if not match:
            continue
        diagnostics.append(
            {
                "path": match.group("path"),
                "line": int(match.group("line")),
                "col": int(match.group("col") or 0),
                "severity": match.group("severity"),
                "code": match.group("code") or "",
                "message": match.group("message") or "",
            }
        )
    return diagnostics


TSC_PATTERN = re.compile(
    r"^(?P<path>[^()]+)\((?P<line>\d+),(?P<col>\d+)\): (?P<severity>error|warning) (?P<code>TS\d+): (?P<message>.+)$"
)


def _parse_tsc(stdout: str) -> List[Dict[str, object]]:
    diagnostics: List[Dict[str, object]] = []
    for line in stdout.splitlines():
        match = TSC_PATTERN.match(line.strip())
        if not match:
            continue
        diagnostics.append(
            {
                "path": match.group("path").strip(),
                "line": int(match.group("line")),
                "col": int(match.group("col")),
                "severity": match.group("severity"),
                "code": match.group("code"),
                "message": match.group("message"),
            }
        )
    return diagnostics


def run_typecheck(run_dir: Path, args: TypecheckArgs):
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
    diagnostics: List[Dict[str, object]] = []
    parse_warning: str | None = None
    parse_source = "none"
    parse_mode = "none" if args.tool == "command" else args.parse

    parser_map = {
        "pyright": _parse_pyright,
        "mypy": _parse_mypy,
        "tsc": _parse_tsc,
    }

    if args.parse in parser_map:
        parser = parser_map[args.parse]
        for source, text in (("stdout", stdout), ("stderr", stderr)):
            if not text.strip():
                continue
            try:
                diagnostics = parser(text)
                parse_source = source
                break
            except (json.JSONDecodeError, ValueError):
                continue
        if not diagnostics and parse_source == "none":
            parse_source = "stdout"
            parse_warning = (
                f"{args.parse} output is not valid JSON"
                if args.parse == "pyright"
                else f"{args.parse} output is not valid"
            )

    final = {
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms", 0),
        "timed_out": result.get("timed_out", False),
        "diagnostics": diagnostics,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": result.get("stdout_truncated", False),
        "stderr_truncated": result.get("stderr_truncated", False),
        "parse_mode": parse_mode,
        "parse_source": parse_source,
        "parse_warning": parse_warning,
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
