from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from ..config import (
    EXCLUDE_FROM_SEARCH_LIST,
    is_under_allowed_root,
    is_within_sandbox,
    normalize_search_root,
    policy_allowed_roots,
    policy_allows_write,
    policy_metadata,
    policy_runtime_root,
    resolve_policy_path,
)
from ..models import FileDeleteArgs
from .path_filters import first_matching_pattern, glob_candidates

FILE_DELETE_POLICY_META = policy_metadata()


def _error_response(code: str, message: str, details: dict | None = None, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {"code": f"tool_runner.{code}", "message": message, "details": details or {}},
            "meta": {"policy": FILE_DELETE_POLICY_META},
        },
    )


def _matching_exclusion(target: Path, run_dir: Path) -> str | None:
    if not EXCLUDE_FROM_SEARCH_LIST:
        return None
    candidates = glob_candidates(target, run_dir, run_dir, is_dir=target.is_dir())
    return first_matching_pattern(candidates, EXCLUDE_FROM_SEARCH_LIST)


def delete_file(run_dir: Path, args: FileDeleteArgs, policy: dict[str, Any] | None = None):
    base_dir = policy_runtime_root(policy, "repo_root") or run_dir.resolve()
    allowed_context_root = normalize_search_root(base_dir)
    policy_roots = policy_allowed_roots(policy)
    if not is_under_allowed_root(run_dir):
        return _error_response("PATH_NOT_ALLOWED", "Run directory not permitted")
    try:
        target = resolve_policy_path(run_dir, args.path, policy)
    except ValueError as exc:
        error_code = (
            "PATH_OUTSIDE_WORKSPACE"
            if "path traversal outside of workspace" in str(exc)
            else "PATH_NOT_ALLOWED"
        )
        return _error_response(error_code, str(exc))

    if not is_under_allowed_root(target, (allowed_context_root, *policy_roots)):
        return _error_response("PATH_NOT_ALLOWED", "Path not permitted")

    exclusion = _matching_exclusion(target, run_dir)
    if exclusion:
        return _error_response("PATH_EXCLUDED", "Path excluded by policy", {"pattern": exclusion})

    if (
        not is_within_sandbox(target)
        and not is_under_allowed_root(target, policy_roots)
        and not policy_allows_write(policy)
    ):
        return _error_response(
            "WRITE_NOT_PERMITTED",
            "Deletes outside the sandbox require allow_write policy",
        )

    if not target.exists():
        if args.missing_ok:
            return JSONResponse(
                status_code=200,
                content={
                    "ok": True,
                    "result": {
                        "path": args.path,
                        "requested_path": args.path,
                        "requested_root": args.absolute_root or None,
                        "resolved_path": str(target),
                        "resolved_root": str(target.parent.resolve()),
                        "deleted": False,
                        "missing": True,
                        "deleted_type": None,
                    },
                    "meta": {"policy": FILE_DELETE_POLICY_META},
                },
            )
        return _error_response("NOT_FOUND", "Path missing")

    target_type = "directory" if target.is_dir() else "file"
    try:
        if target.is_dir():
            if args.recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()
    except OSError as exc:
        return _error_response(
            "DELETE_FAILED",
            "Delete failed",
            {"cause": str(exc), "path": str(target), "recursive": args.recursive},
        )

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "path": args.path,
                "requested_path": args.path,
                "requested_root": args.absolute_root or None,
                "resolved_path": str(target.resolve()),
                "resolved_root": str(target.parent.resolve()),
                "deleted": True,
                "missing": False,
                "deleted_type": target_type,
            },
            "meta": {"policy": FILE_DELETE_POLICY_META},
        },
    )
