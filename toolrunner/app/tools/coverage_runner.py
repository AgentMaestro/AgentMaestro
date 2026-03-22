from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import PYTHON_INTERPRETER, PYTHON_INTERPRETER_SOURCE, resolve_policy_path
from ..models import CoverageArgs, RunCommandArgs
from .python_runner_support import detect_missing_python_module, missing_python_module_response
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


def _invoke_run_command(run_dir: Path, run_args: RunCommandArgs, policy: dict | None):
    if policy is None:
        return run_command(run_dir, run_args)
    try:
        return run_command(run_dir, run_args, policy)
    except TypeError as exc:
        if "positional arguments but 3 were given" not in str(exc):
            raise
        return run_command(run_dir, run_args)


def _coverage_file_summary(path: str, info: dict[str, object]) -> dict[str, object]:
    summary = info.get("summary") if isinstance(info.get("summary"), dict) else {}
    percent = summary.get("percent_covered", info.get("percent_covered"))
    item: dict[str, object] = {
        "path": path,
        "percent": percent,
    }
    for key in (
        "covered_lines",
        "missing_lines",
        "excluded_lines",
        "num_statements",
        "percent_covered",
    ):
        value = summary.get(key, info.get(key))
        if value is not None:
            item[key] = value
    return item


def run_coverage(run_dir: Path, args: CoverageArgs, policy: dict | None = None):
    try:
        working_dir = resolve_policy_path(run_dir, args.cwd or ".", policy)
    except ValueError as exc:
        error_code = (
            "PATH_OUTSIDE_WORKSPACE"
            if "path traversal outside of workspace" in str(exc)
            else "PATH_NOT_ALLOWED"
        )
        return _error_response(error_code, str(exc))

    pytest_cmd = [PYTHON_INTERPRETER, "-m", "coverage", "run", "-m", "pytest", *(args.args or [])]
    run_result = _invoke_run_command(
        run_dir,
        RunCommandArgs(
            cmd=pytest_cmd,
            cwd=args.cwd,
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
        ),
        policy,
    )
    try:
        payload = json.loads(run_result.body.decode("utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover
        return _error_response("INTERNAL", str(exc))
    if not payload.get("ok"):
        return run_result
    run_exec = payload.get("result") or {}
    missing_module = detect_missing_python_module(run_exec, ("coverage", "pytest"))
    if missing_module:
        return missing_python_module_response(
            tool_name="coverage_runner",
            module_name=missing_module,
            result=run_exec,
            details={"phase": "pytest", "cwd": args.cwd, "args": args.args or []},
        )
    if run_exec.get("timed_out"):
        return _error_response(
            "TIMED_OUT",
            f"coverage test run timed out after {args.timeout_ms} ms (source=args.timeout_ms)",
            {
                "phase": "pytest",
                "cwd": args.cwd,
                "args": args.args or [],
                "timeout_ms": args.timeout_ms,
                "timeout_source": "args.timeout_ms",
            },
        )
    if isinstance(run_exec.get("exit_code"), int) and run_exec.get("exit_code") != 0:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "error": {
                    "code": "tool_runner.COVERAGE_TESTS_FAILED",
                    "message": f"coverage test run exited with code {run_exec['exit_code']}",
                    "details": {"phase": "pytest", "cwd": args.cwd, "args": args.args or []},
                },
                "result": run_exec,
            },
        )

    json_cmd = [PYTHON_INTERPRETER, "-m", "coverage", "json", "-o", "coverage.json"]
    json_run = _invoke_run_command(
        run_dir,
        RunCommandArgs(
            cmd=json_cmd,
            cwd=args.cwd,
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
        ),
        policy,
    )
    try:
        json_payload = json.loads(json_run.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return _error_response("INTERNAL", str(exc))
    if not json_payload.get("ok"):
        return json_run
    json_exec = json_payload.get("result") or {}
    missing_module = detect_missing_python_module(json_exec, ("coverage",))
    if missing_module:
        return missing_python_module_response(
            tool_name="coverage_runner",
            module_name=missing_module,
            result=json_exec,
            details={"phase": "coverage_json", "cwd": args.cwd},
        )
    if json_exec.get("timed_out"):
        return _error_response(
            "TIMED_OUT",
            f"coverage report generation timed out after {args.timeout_ms} ms (source=args.timeout_ms)",
            {
                "phase": "coverage_json",
                "cwd": args.cwd,
                "timeout_ms": args.timeout_ms,
                "timeout_source": "args.timeout_ms",
            },
        )
    if isinstance(json_exec.get("exit_code"), int) and json_exec.get("exit_code") != 0:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "error": {
                    "code": "tool_runner.COVERAGE_REPORT_FAILED",
                    "message": f"coverage json exited with code {json_exec['exit_code']}",
                    "details": {"phase": "coverage_json", "cwd": args.cwd},
                },
                "result": json_exec,
            },
        )

    coverage_path = working_dir / "coverage.json"
    if not coverage_path.exists():
        return _error_response(
            "NOT_FOUND",
            "coverage.json not generated",
            {"expected_path": str(coverage_path)},
        )
    try:
        with coverage_path.open("r", encoding="utf-8") as handle:
            coverage_data = json.load(handle)
    except json.JSONDecodeError:
        return _error_response(
            "INTERNAL",
            "coverage.json invalid",
            {"path": str(coverage_path)},
        )

    total_percent = coverage_data.get("totals", {}).get("percent_covered")
    files_data = coverage_data.get("files", {})
    files: list[dict[str, object]] = []
    if isinstance(files_data, dict):
        for path, info in files_data.items():
            if isinstance(info, dict):
                files.append(_coverage_file_summary(path, info))
    files.sort(key=lambda item: item["path"])

    final = {
        "requested_cwd": args.cwd,
        "resolved_cwd": str(working_dir),
        "requested_args": args.args or [],
        "exit_code": payload["result"].get("exit_code"),
        "duration_ms": payload["result"].get("duration_ms", 0),
        "timed_out": payload["result"].get("timed_out", False),
        "total_percent": total_percent,
        "files": files,
        "stdout": payload["result"].get("stdout", ""),
        "stderr": payload["result"].get("stderr", ""),
        "stdout_truncated": payload["result"].get("stdout_truncated", False),
        "stderr_truncated": payload["result"].get("stderr_truncated", False),
        "coverage_stdout": json_payload["result"].get("stdout", ""),
        "coverage_stderr": json_payload["result"].get("stderr", ""),
        "coverage_duration_ms": json_payload["result"].get("duration_ms", 0),
        "coverage_json_path": str(coverage_path),
        "python_interpreter": PYTHON_INTERPRETER,
        "python_interpreter_source": PYTHON_INTERPRETER_SOURCE,
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
