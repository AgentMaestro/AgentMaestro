from __future__ import annotations

from typing import List

from .config import ALLOWED_COMMANDS, COMMAND_TIMEOUT, OUTPUT_LIMIT, PYTHON_INTERPRETER


def validate_command(name: str) -> str:
    if name not in ALLOWED_COMMANDS:
        raise ValueError("command not allowed")
    return name


def truncate_output(payload: bytes | str, max_bytes: int) -> str:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="ignore")
    else:
        text = payload
    if max_bytes <= 0:
        return ""
    if len(text) <= max_bytes:
        return text
    return text[:max_bytes] + "…"


def build_python_command(mode: str, path: str | None = None, code: str | None = None) -> List[str]:
    if mode == "snippet":
        if not code:
            raise ValueError("code required")
        return [PYTHON_INTERPRETER, "-c", code]
    if mode == "file":
        if not path:
            raise ValueError("path required")
        return [PYTHON_INTERPRETER, path]
    raise ValueError("invalid mode")
