from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from fastapi.responses import JSONResponse

from ..models import CoverageArgs, RunCommandArgs
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


def run_coverage(run_dir: Path, args: CoverageArgs):
    try:
        working_dir = safe_join(run_dir, args.cwd or ".")
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    pytest_cmd = ["python", "-m", "coverage", "run", "-m", "pytest", *(args.args or [])]
    run_result = run_command(
        run_dir,
        RunCommandArgs(
            cmd=pytest_cmd,
            cwd=args.cwd,
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
        ),
    )
    try:
        payload = json.loads(run_result.body.decode("utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover
        return _error_response("INTERNAL", str(exc))
    if not payload.get("ok"):
        return run_result

    json_cmd = ["python", "-m", "coverage", "json", "-o", "coverage.json"]
    json_run = run_command(
        run_dir,
        RunCommandArgs(
            cmd=json_cmd,
            cwd=args.cwd,
            timeout_ms=args.timeout_ms,
            max_output_bytes=args.max_output_bytes,
        ),
    )
    try:
        json_payload = json.loads(json_run.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return _error_response("INTERNAL", str(exc))
    if not json_payload.get("ok"):
        return json_run

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
    except json.JSONDecodeError as exc:
        return _error_response(
            "INTERNAL",
            "coverage.json invalid",
            {"path": str(coverage_path)},
        )

    total_percent = coverage_data.get("totals", {}).get("percent_covered")
    files_data = coverage_data.get("files", {})
    files = []
    if isinstance(files_data, dict):
        for path, info in files_data.items():
            percent = info.get("percent_covered")
            if percent is not None:
                files.append({"path": path, "percent": percent})
    files.sort(key=lambda item: item["path"])

    final = {
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
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": final})
