
from __future__ import annotations

import base64
from hashlib import sha256
import os
import tempfile
from pathlib import Path

from fastapi.responses import JSONResponse

from ..models import FileWriteArgs
from ..sandbox import safe_join


def _error_response(code: str, message: str, details: dict | None = None, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {"code": f"tool_runner.{code}", "message": message, "details": details or {}},
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


def write_file(run_dir: Path, args: FileWriteArgs):
    try:
        target = safe_join(run_dir, args.path)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))

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

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "path": args.path,
                "bytes_written": bytes_written,
                "sha256": sha,
                "created": not existed,
                "overwritten": existed,
            },
        },
    )
