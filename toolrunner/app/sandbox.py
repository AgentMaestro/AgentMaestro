from __future__ import annotations

from pathlib import Path

from .config import SANDBOX_ROOT


def get_run_dir(workspace_id: str, run_id: str) -> Path:
    path = SANDBOX_ROOT / workspace_id / run_id
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
