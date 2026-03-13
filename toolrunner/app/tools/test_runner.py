from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from fastapi.responses import JSONResponse

from ..config import PYTHON_INTERPRETER, PYTHON_INTERPRETER_SOURCE, is_under_allowed_root, normalize_search_root, policy_allowed_roots
from ..models import RunCommandArgs, RunnerTestArgs
from ..sandbox import safe_join
from .python_runner_support import detect_missing_python_module, missing_python_module_response
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


def _tool_failure_response(
    code: str,
    message: str,
    details: dict[str, object],
    result: dict[str, object],
) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details,
            },
            "result": result,
        },
    )


def _invoke_run_command(run_dir: Path, run_args: RunCommandArgs, policy: dict | None):
    if policy is None:
        return run_command(run_dir, run_args)
    try:
        return run_command(run_dir, run_args, policy)
    except TypeError as exc:
        if "positional arguments but 3 were given" not in str(exc):
            raise
        return run_command(run_dir, run_args)


def _policy_root(policy: dict | None, key: str) -> Path | None:
    if not policy:
        return None
    raw = str(policy.get(key) or "").strip()
    if not raw:
        return None
    try:
        return normalize_search_root(Path(raw))
    except Exception:
        return None


def _resolve_script_path(run_dir: Path, script_path: str, policy: dict | None = None) -> tuple[Path | None, list[str]]:
    requested = Path(script_path)
    searched_roots: list[str] = []
    if requested.is_absolute():
        try:
            target = requested.resolve()
        except Exception:
            return None, []
        return target, [str(target.parent)]

    candidate_roots: list[Path] = []
    seen: set[Path] = set()
    for root in (
        _policy_root(policy, "repo_root"),
        _policy_root(policy, "tmp_root"),
        run_dir,
        *policy_allowed_roots(policy),
    ):
        if root is None:
            continue
        try:
            normalized = normalize_search_root(root)
        except Exception:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        candidate_roots.append(root)

    for base_root in candidate_roots:
        searched_roots.append(str(base_root))
        try:
            candidate = (base_root / requested).resolve()
        except Exception:
            continue
        if candidate.exists():
            return candidate, searched_roots
    if candidate_roots:
        try:
            return (candidate_roots[0] / requested).resolve(), searched_roots
        except Exception:
            return None, searched_roots
    return None, searched_roots


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


def run_tests(run_dir: Path, args: RunnerTestArgs, policy: dict | None = None):
    command: List[str]
    if args.kind == "powershell_script":
        if not args.script_path:
            return _error_response("INVALID_ARGUMENT", "script_path is required for powershell_script")
        script_path, searched_roots = _resolve_script_path(run_dir, args.script_path, policy)
        if script_path is None:
            return _error_response("NOT_FOUND", "script not found", {"path": args.script_path, "searched_roots": searched_roots})
        allowed_roots = [normalize_search_root(run_dir), *policy_allowed_roots(policy)]
        repo_root = _policy_root(policy, "repo_root")
        tmp_root = _policy_root(policy, "tmp_root")
        if repo_root is not None:
            allowed_roots.append(repo_root)
        if tmp_root is not None:
            allowed_roots.append(tmp_root)
        if not is_under_allowed_root(script_path, allowed_roots):
            return _error_response(
                "PATH_NOT_ALLOWED",
                "script path not permitted",
                {"path": str(script_path), "requested_path": args.script_path, "searched_roots": searched_roots},
            )
        if not script_path.exists():
            return _error_response("NOT_FOUND", "script not found", {"path": args.script_path, "searched_roots": searched_roots})
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
        command = [PYTHON_INTERPRETER, "-m", "pytest", *(args.pytest_args or [])]
    else:
        command = args.cmd or []

    run_args = RunCommandArgs(
        cmd=command,
        cwd=args.cwd,
        env=args.env,
        timeout_ms=args.timeout_ms,
        max_output_bytes=args.max_output_bytes,
    )
    response = _invoke_run_command(run_dir, run_args, policy)
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as exc:  # pragma: no cover
        return _error_response("INTERNAL", str(exc))
    details = {
        "kind": args.kind,
        "cwd": args.cwd,
        "script_path": args.script_path,
        "pytest_args": args.pytest_args,
        "cmd": args.cmd,
        "timeout_ms": args.timeout_ms,
        "timeout_source": "args.timeout_ms",
    }
    if not payload.get("ok"):
        upstream_error = payload.get("error") or {}
        upstream_error = upstream_error if isinstance(upstream_error, dict) else {}
        upstream_result = payload.get("result") or {}
        upstream_result = upstream_result if isinstance(upstream_result, dict) else {}
        stderr = str(upstream_result.get("stderr") or upstream_error.get("message") or "")
        stdout = str(upstream_result.get("stdout") or "")
        failed_result = {
            "exit_code": upstream_result.get("exit_code"),
            "duration_ms": upstream_result.get("duration_ms", 0),
            "timed_out": bool(upstream_result.get("timed_out")),
            "summary": None,
            "parse_mode": args.parse,
            "failed_tests": [],
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": upstream_result.get("stdout_truncated", False),
            "stderr_truncated": upstream_result.get("stderr_truncated", False),
        }
        details["upstream_code"] = upstream_error.get("code")
        return _tool_failure_response(
            str(upstream_error.get("code") or "tool_runner.TEST_RUNNER_FAILED"),
            str(upstream_error.get("message") or "test_runner command failed"),
            details,
            failed_result,
        )

    result = payload["result"]
    if args.kind == "pytest":
        missing_module = detect_missing_python_module(result, ("pytest",))
        if missing_module:
            return missing_python_module_response(
                tool_name="test_runner",
                module_name=missing_module,
                result=result,
                details=details,
            )
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
        "python_interpreter": PYTHON_INTERPRETER if args.kind == "pytest" else None,
        "python_interpreter_source": PYTHON_INTERPRETER_SOURCE if args.kind == "pytest" else None,
    }
    if final["timed_out"]:
        return _tool_failure_response(
            "tool_runner.TIMED_OUT",
            f"test_runner timed out after {args.timeout_ms} ms (source=args.timeout_ms)",
            details,
            final,
        )
    if isinstance(final["exit_code"], int) and final["exit_code"] != 0:
        return _tool_failure_response(
            "tool_runner.TESTS_FAILED",
            f"test_runner exited with code {final['exit_code']}",
            details,
            final,
        )
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
