from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable, Sequence

from fastapi.responses import JSONResponse

from ..models import RepoTreeArgs
from ..sandbox import is_safe_path, safe_join


def _error(code: str, message: str, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": f"tool_runner.{code}",
                "message": message,
                "details": {},
            },
        },
    )


def _glob_candidates(
    entry_path: Path,
    root_path: Path,
    run_dir: Path,
    is_dir: bool,
) -> list[str]:
    candidates: list[str] = []
    try:
        relative_to_root = entry_path.relative_to(root_path)
    except ValueError:
        return candidates

    relative_root_str = relative_to_root.as_posix()
    if relative_root_str and relative_root_str != ".":
        candidates.append(relative_root_str)
        if is_dir:
            candidates.append(f"{relative_root_str}/")

    try:
        relative_to_run = entry_path.relative_to(run_dir).as_posix()
    except ValueError:
        return candidates
    if relative_to_run:
        candidates.append(relative_to_run)
    if is_dir and relative_to_run:
        candidates.append(f"{relative_to_run}/")
    return candidates


def _matches_patterns(candidates: Iterable[str], patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        for candidate in candidates:
            if fnmatch.fnmatchcase(candidate, pattern):
                return True
    return False


def _collect_metadata(target: Path, follow_symlinks: bool) -> dict[str, int | None]:
    try:
        stat_fn = target.stat if follow_symlinks else target.lstat
        stats = stat_fn()
    except OSError:
        return {"size_bytes": None, "mtime_epoch": None}
    return {
        "size_bytes": stats.st_size,
        "mtime_epoch": int(stats.st_mtime),
    }


def list_repo_tree(run_dir: Path, args: RepoTreeArgs):
    try:
        root_path = safe_join(run_dir, args.root)
    except ValueError as exc:
        return _error("PATH_OUTSIDE_WORKSPACE", str(exc))

    if not root_path.exists():
        return _error("NOT_FOUND", "root path missing")

    entries: list[dict] = []
    files_count = 0
    dirs_count = 0
    truncated = False

    def _append_entry(path: Path, entry_type: str, depth: int) -> bool:
        nonlocal truncated, files_count, dirs_count
        if len(entries) >= args.max_entries:
            truncated = True
            return False
        try:
            actual = path.relative_to(run_dir).as_posix()
        except ValueError:
            return True
        entry: dict[str, object] = {
            "type": entry_type,
            "path": actual,
            "depth": depth,
        }
        if args.include_metadata:
            entry.update(_collect_metadata(path, args.follow_symlinks))
        entries.append(entry)
        if entry_type == "file":
            files_count += 1
        else:
            dirs_count += 1
        return True

    def _should_exclude(path: Path, is_dir: bool) -> bool:
        if not args.exclude_globs:
            return False
        candidates = _glob_candidates(path, root_path, run_dir, is_dir)
        return bool(candidates) and _matches_patterns(candidates, args.exclude_globs)

    def _passes_include(path: Path, is_dir: bool) -> bool:
        if not args.include_globs:
            return True
        candidates = _glob_candidates(path, root_path, run_dir, is_dir)
        return bool(candidates) and _matches_patterns(candidates, args.include_globs)

    def _depth_for_entry(path: Path) -> int:
        try:
            relative_depth = path.relative_to(root_path)
        except ValueError:
            return 0
        return len([part for part in relative_depth.parts if part != "."])

    if root_path.is_file():
        if args.include_files and not _should_exclude(root_path, False) and _passes_include(root_path, False):
            depth = _depth_for_entry(root_path)
            _append_entry(root_path, "file", depth)
        result = {
            "root": args.root,
            "max_depth": args.max_depth,
            "truncated": truncated,
            "entries": sorted(entries, key=lambda entry: entry["path"]),
            "stats": {
                "files": files_count,
                "dirs": dirs_count,
                "entries": files_count + dirs_count,
            },
        }
        return JSONResponse(status_code=200, content={"ok": True, "result": result})

    for current_root, dirs, files in os.walk(
        root_path,
        topdown=True,
        followlinks=args.follow_symlinks,
    ):
        if truncated:
            break
        current_root_path = Path(current_root)
        try:
            current_depth = len([part for part in current_root_path.relative_to(root_path).parts if part != "."])
        except ValueError:
            current_depth = 0
        if args.max_depth >= 0 and current_depth >= args.max_depth:
            dirs[:] = []
        dirs.sort()
        next_dirs: list[str] = []
        for directory in dirs:
            dir_path = current_root_path / directory
            if not is_safe_path(run_dir, dir_path):
                continue
            dir_depth = _depth_for_entry(dir_path)
            if args.max_depth >= 0 and dir_depth > args.max_depth:
                continue
            if _should_exclude(dir_path, True):
                continue
            next_dirs.append(directory)
            if args.include_dirs and _passes_include(dir_path, True):
                if not _append_entry(dir_path, "dir", dir_depth):
                    break
        dirs[:] = next_dirs
        if truncated:
            break
        files.sort()
        for filename in files:
            file_path = current_root_path / filename
            file_depth = _depth_for_entry(file_path)
            if args.max_depth >= 0 and file_depth > args.max_depth:
                continue
            if _should_exclude(file_path, False):
                continue
            if not is_safe_path(run_dir, file_path):
                continue
            if args.include_files and _passes_include(file_path, False):
                if not _append_entry(file_path, "file", file_depth):
                    truncated = True
                    break
        if truncated:
            break

    result = {
        "root": args.root,
        "max_depth": args.max_depth,
        "truncated": truncated,
        "entries": sorted(entries, key=lambda entry: entry["path"]),
        "stats": {
            "files": files_count,
            "dirs": dirs_count,
            "entries": files_count + dirs_count,
        },
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": result})
