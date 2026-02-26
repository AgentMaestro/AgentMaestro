from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from fastapi.responses import JSONResponse

from ..models import RunCommandArgs, RunnerTestArgs
from ..sandbox import safe_join
from .run_command import run_command

SUMMARY_PATTERN = re.compile(r"=+\s*(?P<body>.+?)\s*in\s*[\d.]+s\s*=+")
FAILURE_HEADER = re.compile(r"_{10,}\s*(?P<nodeid>.+?)\s*_{10,}")
SEPARATOR_PATTERN = re.compile(r"^(?:={5,}|-{5,}|_{5,})")

SUMMARY_LABEL_MAP = {
    "pass": "passed",
    "passes": "passed",
    "passed": "passed",
    "fail": "failed",
    "fails": "failed",
    "failed": "failed",
    "skip": "skipped",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xfail": "xfailed",
    "xpassed": "xpassed",
    "xpass": "xpassed",
    "error": "errors",
    "errors": "errors",
}
SEPARATOR_PATTERN = re.compile(r"^(?:={5,}|-{5,}|_{5,})")


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


def _parse_summary(text: str) -> dict[str, int]:
    summary: Dict[str, int] = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "errors": 0,
    }
    for line in text.splitlines():
        match = SUMMARY_PATTERN.search(line.strip())
        if not match:
            continue
        tokens = [token.strip() for token in match.group("body").split(",")]
        for token in tokens:
            if not token:
                continue
            parts = token.split()
            if len(parts) < 2:
                continue
            try:
                count = int(parts[0])
            except ValueError:
                continue
            label = parts[1].lower().rstrip(".,")
            label = SUMMARY_LABEL_MAP.get(label, label)
            if label in summary:
                summary[label] = count
    return summary


def _collect_tracebacks(text: str) -> Dict[str, str]:
    traces: Dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = FAILURE_HEADER.match(lines[index])
        if not match:
            index += 1
            continue
        nodeid = match.group("nodeid").strip()
        index += 1
        block: List[str] = []
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if SEPARATOR_PATTERN.match(stripped) or FAILURE_HEADER.match(line):
                break
            block.append(line)
            index += 1
        traces[nodeid] = "\n".join(block).strip()
    return traces


def _extract_failures(text: str) -> List[Dict[str, object]]:
    failures: List[Dict[str, object]] = []
    traces = _collect_tracebacks(text)
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("FAILED ") or line.startswith("ERROR ")):
            continue
        status, rest = line.split(" ", 1)
        nodeid, sep, message = rest.partition(" - ")
        failure = {
            "nodeid": nodeid.strip(),
            "file": nodeid.split("::", 1)[0] if "::" in nodeid else nodeid,
            "line": 0,
            "message": message.strip() if sep else "",
            "traceback": traces.get(nodeid.strip(), ""),
            "status": status,
        }
        failures.append(failure)
    return failures


def run_tests(run_dir: Path, args: RunnerTestArgs):
    command: List[str]
    if args.kind == "powershell_script":
        if not args.script_path:
            return _error_response("INVALID_ARGUMENT", "script_path is required for powershell_script")
        try:
            script_path = safe_join(run_dir, args.script_path)
        except ValueError as exc:
            return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))
        if not script_path.exists():
            return _error_response("NOT_FOUND", "script not found", {"path": args.script_path})
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
        if args.script_args:
            command.append("--")
            command.extend(args.script_args)
    elif args.kind == "pytest":
        command = ["python", "-m", "pytest", *(args.pytest_args or [])]
    else:
        command = args.cmd or []

    run_args = RunCommandArgs(
        cmd=command,
        cwd=args.cwd,
        env=args.env,
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
    parse_text = "\n".join(filter(None, (stdout, stderr)))
    summary = _parse_summary(parse_text) if args.parse == "pytest" else None
    failed_tests = _extract_failures(parse_text) if args.parse == "pytest" else []
    final = {
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms", 0),
        "timed_out": result.get("timed_out", False),
        "summary": summary,
        "parse_mode": args.parse,
        "failed_tests": failed_tests,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": result.get("stdout_truncated", False),
        "stderr_truncated": result.get("stderr_truncated", False),
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
