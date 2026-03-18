from __future__ import annotations

import logging
from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import RUN_TESTS_OUTPUT_LIMIT
from ..models import RunTestsArgs
from .subprocess_utils import run_subprocess

logger = logging.getLogger(__name__)

_ALLOWED_SUITES = ("backend", "toolrunner")
_SCRIPT_CANDIDATES = {
    "backend": ("backend/scripts/runtests.ps1", "backend/scripts/test.ps1"),
    "toolrunner": ("toolrunner/scripts/runtests.ps1", "toolrunner/scripts/test.ps1"),
}


def _workspace_root(run_dir: Path, policy: dict | None) -> Path:
    repo_root = str((policy or {}).get("repo_root") or "").strip()
    if repo_root:
        return Path(repo_root).resolve()
    return run_dir.resolve()


def _normalize_suites(values: list[str]) -> list[str]:
    requested = [str(value).strip().lower() for value in values if str(value).strip()]
    if "all" in requested:
        return list(_ALLOWED_SUITES)
    ordered: list[str] = []
    for suite in requested:
        if suite not in ordered:
            ordered.append(suite)
    return ordered


def _resolve_script(root: Path, suite: str) -> tuple[Path | None, str | None]:
    for candidate in _SCRIPT_CANDIDATES[suite]:
        script = (root / candidate).resolve()
        if script.exists():
            return script, candidate
    return None, None


def run_predefined_tests(
    run_dir: Path,
    args: RunTestsArgs,
    policy: dict | None = None,
    *,
    max_output_bytes: int | None = None,
):
    workspace_root = _workspace_root(run_dir, policy)
    suites = _normalize_suites(list(args.suites))
    if not suites:
        payload = {
            "ok": False,
            "results": [],
            "result": {"results": []},
            "error": {
                "code": "tool_runner.INVALID_ARGUMENT",
                "message": "suites must include at least one supported suite",
                "details": {"suites": list(args.suites)},
            },
        }
        return JSONResponse(status_code=200, content=payload)

    output_limit = min(max_output_bytes or RUN_TESTS_OUTPUT_LIMIT, RUN_TESTS_OUTPUT_LIMIT)
    results: list[dict[str, object]] = []
    overall_ok = True
    fallback_scripts: list[str] = []

    for suite in suites:
        script_path, matched_candidate = _resolve_script(workspace_root, suite)
        if script_path is None:
            overall_ok = False
            expected = _SCRIPT_CANDIDATES[suite][0]
            results.append(
                {
                    "suite": suite,
                    "script_path": str((workspace_root / expected).resolve()),
                    "ok": False,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": f"test script not found for suite '{suite}'",
                    "timed_out": False,
                    "duration_ms": 0,
                    "truncated": False,
                }
            )
            logger.warning("run_tests missing script suite=%s workspace_root=%s", suite, workspace_root)
            continue

        if matched_candidate and matched_candidate.endswith("test.ps1"):
            fallback_scripts.append(matched_candidate)

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
        logger.info(
            "run_tests executing suite=%s script=%s timeout_s=%s",
            suite,
            script_path,
            args.timeout_seconds,
        )
        try:
            completed = run_subprocess(
                command,
                cwd=workspace_root,
                timeout_seconds=args.timeout_seconds,
                max_output_bytes=output_limit,
            )
            stderr = completed.stderr
            if completed.timed_out and not stderr:
                stderr = (
                    f"test script timed out after {args.timeout_seconds} seconds "
                    f"(source=args.timeout_seconds)"
                )
            suite_ok = not completed.timed_out and completed.exit_code == 0
            overall_ok = overall_ok and suite_ok
            results.append(
                {
                    "suite": suite,
                    "script_path": str(script_path),
                    "ok": suite_ok,
                    "exit_code": completed.exit_code,
                    "stdout": completed.stdout,
                    "stderr": stderr,
                    "timed_out": completed.timed_out,
                    "duration_ms": completed.duration_ms,
                    "truncated": completed.truncated,
                }
            )
            logger.info(
                "run_tests completed suite=%s exit_code=%s duration_ms=%s timed_out=%s",
                suite,
                completed.exit_code,
                completed.duration_ms,
                completed.timed_out,
            )
        except FileNotFoundError as exc:
            overall_ok = False
            logger.warning("run_tests powershell missing suite=%s error=%s", suite, exc)
            results.append(
                {
                    "suite": suite,
                    "script_path": str(script_path),
                    "ok": False,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": str(exc),
                    "timed_out": False,
                    "duration_ms": 0,
                    "truncated": False,
                }
            )

    payload = {
        "ok": overall_ok,
        "results": results,
        "result": {
            "results": results,
            "fallback_scripts": fallback_scripts,
        },
    }
    if not overall_ok:
        payload["error"] = {
            "code": "tool_runner.TESTS_FAILED",
            "message": "one or more requested test suites failed",
            "details": {
                "suites": suites,
                "fallback_scripts": fallback_scripts,
            },
        }
    return JSONResponse(status_code=200, content=payload)
