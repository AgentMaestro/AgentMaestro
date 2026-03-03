
from __future__ import annotations

import base64
from hashlib import sha256
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from ..config import (
    EXCLUDE_FROM_SEARCH_LIST,
    is_under_allowed_root,
    is_within_sandbox,
    normalize_search_root,
    policy_allows_write,
    policy_metadata,
)
from ..models import FileWriteArgs
from ..sandbox import safe_join
from .path_filters import first_matching_pattern, glob_candidates


FILE_WRITE_POLICY_META = policy_metadata()


def _error_response(code: str, message: str, details: dict | None = None, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {"code": f"tool_runner.{code}", "message": message, "details": details or {}},
            "meta": {"policy": FILE_WRITE_POLICY_META},
        },
    )


def _sha256_bytes(data: bytes) -> str:
    hasher = sha256()
    hasher.update(data)
    return hasher.hexdigest()


def _read_existing_sha(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_file(run_dir: Path, args: FileWriteArgs, policy: dict[str, Any] | None = None):
    allowed_context_root = normalize_search_root(run_dir)
    if not is_under_allowed_root(run_dir):
        return _error_response("PATH_NOT_ALLOWED", "Run directory not permitted")
    try:
        target = safe_join(run_dir, args.path)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

    if not is_under_allowed_root(target, (allowed_context_root,)):
        return _error_response("PATH_NOT_ALLOWED", "Path not permitted")

    exclusion = _matching_exclusion(target, run_dir)
    if exclusion:
        return _error_response("PATH_EXCLUDED", "Path excluded by policy", {"pattern": exclusion})

    if not is_within_sandbox(target) and not policy_allows_write(policy):
        return _error_response(
            "WRITE_NOT_PERMITTED",
            "Writes outside the sandbox require allow_write policy",
        )

    parent = target.parent
    if args.make_dirs:
        parent.mkdir(parents=True, exist_ok=True)
    elif not parent.exists():
        return _error_response("INVALID_ARGUMENT", "Parent directory missing")

    existed = target.exists()
    if existed and not args.overwrite:
        return _error_response("ALREADY_EXISTS", "File already exists")

    if args.expected_sha256 and existed:
        current_sha = _read_existing_sha(target)
        if current_sha != args.expected_sha256:
            return _error_response("CONFLICT", "Existing file checksum mismatch")

    if args.mode == "text":
        try:
            content_bytes = args.content.encode(args.encoding)
        except LookupError as exc:
            return _error_response("UNSUPPORTED_ENCODING", str(exc))
        except UnicodeEncodeError as exc:
            return _error_response("INVALID_ARGUMENT", "text encoding failed", {"err": str(exc)})
    else:
        try:
            content_bytes = base64.b64decode(args.content_base64, validate=True)
        except Exception as exc:
            return _error_response("INVALID_ARGUMENT", "invalid base64", {"err": str(exc)})

    def _write(path: Path):
        with path.open("wb") as handle:
            handle.write(content_bytes)

    temp_path: Path | None = None
    try:
        if args.atomic:
            temp = tempfile.NamedTemporaryFile(dir=parent, delete=False)
            temp_path = Path(temp.name)
            temp.close()
            _write(temp_path)
            if existed:
                mode = target.stat().st_mode
                os.chmod(temp_path, mode)
            os.replace(temp_path, target)
        else:
            _write(target)
    except OSError as exc:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return _error_response("INTERNAL", "write failed", {"cause": str(exc)})

    bytes_written = len(content_bytes)
    sha = _sha256_bytes(content_bytes)
    resolved_path = str(target.resolve())
    workspace_dir = str(run_dir.resolve())
    sandbox_root = str(run_dir.parent.parent.resolve())

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "path": args.path,
                "bytes_written": bytes_written,
                "sha256": sha,
                "resolved_path": resolved_path,
                "created": not existed,
                "overwritten": existed,
            },
            "meta": {
                "policy": FILE_WRITE_POLICY_META,
                "sandbox": {
                    "workspace_dir": workspace_dir,
                    "sandbox_root": sandbox_root,
                    "resolved_path": resolved_path,
                },
            },
        },
    )


def _matching_exclusion(target: Path, run_dir: Path) -> str | None:
    if not EXCLUDE_FROM_SEARCH_LIST:
        return None
    candidates = glob_candidates(target, run_dir, run_dir, is_dir=target.is_dir())
    return first_matching_pattern(candidates, EXCLUDE_FROM_SEARCH_LIST)
