from __future__ import annotations

import base64
from pathlib import Path

from fastapi.responses import JSONResponse

from ..models import FileReadArgs
from ..sandbox import safe_join

DEFAULT_MAX_BYTES = 262144
HARD_SIZE_LIMIT = 4 * 1024 * 1024
MAX_LINE_RANGE = 200_000


class FileReadError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}


def _error_response(code: str, message: str, details: dict | None = None, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {"code": f"tool_runner.{code}", "message": message, "details": details or {}},
        },
    )


def _read_text(target: Path, args: FileReadArgs) -> dict:
    start = args.start_line or 1
    end = args.end_line
    total_lines = 0
    collected: list[str] = []
    truncated = False
    bytes_accum = 0
    try:
        handle = target.open("r", encoding=args.encoding, errors="replace")
    except (LookupError, UnicodeError) as exc:
        raise FileReadError("UNSUPPORTED_ENCODING", str(exc))
    with handle:
        for lineno, line in enumerate(handle, start=1):
            total_lines += 1
            if lineno < start:
                continue
            if end and lineno > end:
                total_lines = end
                break
            encoded = line.encode(args.encoding, errors="replace")
            if bytes_accum + len(encoded) > args.max_bytes:
                truncated = True
                # finish counting total lines
                for _ in handle:
                    total_lines += 1
                break
            collected.append(line)
            bytes_accum += len(encoded)
    return {
        "path": args.path,
        "mode": "text",
        "encoding": args.encoding,
        "content": "".join(collected),
        "start_line": start,
        "end_line": end or total_lines,
        "total_lines": total_lines,
        "truncated": truncated,
    }


def _read_binary(target: Path, args: FileReadArgs) -> dict:
    with target.open("rb") as handle:
        payload = handle.read(args.max_bytes + 1)
    truncated = len(payload) > args.max_bytes
    data = payload[: args.max_bytes]
    return {
        "path": args.path,
        "mode": "binary",
        "content_base64": base64.b64encode(data).decode("ascii"),
        "byte_length": len(data),
        "truncated": truncated,
    }


def read_file(run_dir: Path, args: FileReadArgs):
    try:
        target = safe_join(run_dir, args.path)
    except ValueError as exc:
        return _error_response("PATH_OUTSIDE_WORKSPACE", str(exc))
    if target.is_dir():
        return _error_response("IS_DIRECTORY", "Path is a directory")
    if not target.exists():
        return _error_response("NOT_FOUND", "File missing")
    try:
        size = target.stat().st_size
    except OSError as exc:
        return _error_response("INVALID_ARGUMENT", str(exc))
    if args.end_line:
        start_line = args.start_line or 1
        if args.end_line - start_line + 1 > MAX_LINE_RANGE:
            return _error_response("TOO_LARGE", "Requested line range exceeds maximum permitted number of lines")
    if not args.start_line and not args.end_line and size > HARD_SIZE_LIMIT:
        return _error_response("TOO_LARGE", "File exceeds maximum permitted size")
    if args.mode == "text":
        try:
            result = _read_text(target, args)
        except FileReadError as exc:
            return _error_response(exc.code, exc.message, exc.details)
    else:
        result = _read_binary(target, args)
    return JSONResponse(status_code=200, content={"ok": True, "result": result})
