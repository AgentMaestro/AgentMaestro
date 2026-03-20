from __future__ import annotations

import re
from pathlib import Path

from . import config


def _normalize_component(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError("path component is required")
    # Keep sandbox path creation Windows-safe even when callers pass scoped ids.
    return re.sub(r'[<>:"/\\|?*]+', "_", cleaned)


def get_run_dir(workspace_id: str, run_id: str) -> Path:
    path = config.SANDBOX_ROOT / _normalize_component(workspace_id) / _normalize_component(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_join(base: Path, subpath: str | Path) -> Path:
    candidate = Path(subpath)
    if candidate.is_absolute():
        raise ValueError("absolute paths not allowed")
    base_resolved = base.resolve()
    target = (base / candidate).resolve()
    if base_resolved == target or base_resolved in target.parents:
        return target
    raise ValueError("path traversal outside of sandbox")


def ensure_file_within_workspace(base: Path, subpath: str | Path) -> Path:
    target = safe_join(base, subpath)
    if not target.exists():
        raise FileNotFoundError("file not found")
    return target


def is_safe_path(base: Path, candidate: Path) -> bool:
    try:
        return str(candidate.resolve()).startswith(str(base.resolve()))
    except (RuntimeError, OSError):
        return False
