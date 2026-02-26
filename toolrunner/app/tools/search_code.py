from __future__ import annotations

import bisect
import fnmatch
import os
import re
import time
from pathlib import Path
from typing import Iterable, Sequence

from fastapi.responses import JSONResponse

from ..models import SearchCodeArgs
from ..sandbox import is_safe_path, safe_join


def _error(code: str, message: str, details: dict | None = None, status_code: int = 400):
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


def _glob_candidates(entry_path: Path, root_path: Path, run_dir: Path) -> list[str]:
    candidates: list[str] = []
    try:
        relative_to_root = entry_path.relative_to(root_path)
    except ValueError:
        return candidates
    relative_root_str = relative_to_root.as_posix()
    if relative_root_str and relative_root_str != ".":
        candidates.append(relative_root_str)
        candidates.append(f"./{relative_root_str}")
    try:
        relative_to_run = entry_path.relative_to(run_dir).as_posix()
    except ValueError:
        return candidates
    if relative_to_run:
        candidates.append(relative_to_run)
        candidates.append(f"./{relative_to_run}")
    return candidates


def _matches_patterns(candidates: Iterable[str], patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        for candidate in candidates:
            if fnmatch.fnmatchcase(candidate, pattern):
                return True
    return False


def _prepare_pattern(args: SearchCodeArgs) -> tuple[re.Pattern, dict | None]:
    flags = re.MULTILINE
    if not args.case_sensitive:
        flags |= re.IGNORECASE
    if args.is_regex:
        try:
            pattern = re.compile(args.query, flags)
        except re.error as exc:
            return None, {"query": args.query, "error": str(exc)}
    else:
        pattern = re.compile(re.escape(args.query), flags)
    return pattern, None


def _split_lines(text: str) -> tuple[list[str], list[int]]:
    raw_lines = text.splitlines(keepends=True)
    if not raw_lines:
        return [""], [0]
    line_starts: list[int] = []
    line_texts: list[str] = []
    offset = 0
    for raw_line in raw_lines:
        line_starts.append(offset)
        line_texts.append(raw_line.rstrip("\r\n"))
        offset += len(raw_line)
    return line_texts, line_starts


def _line_index_for_position(position: int, starts: list[int]) -> int:
    idx = bisect.bisect_right(starts, position) - 1
    if idx < 0:
        return 0
    return idx


def list_search_code(run_dir: Path, args: SearchCodeArgs):
    try:
        root_path = safe_join(run_dir, args.root)
    except ValueError as exc:
        return _error("PATH_OUTSIDE_WORKSPACE", str(exc))

    if not root_path.exists():
        return _error("NOT_FOUND", "root path missing")

    pattern, compile_error = _prepare_pattern(args)
    if compile_error:
        return _error("INVALID_ARGUMENT", "query pattern could not be compiled", compile_error)

    entries: list[dict] = []
    files_scanned = 0
    files_with_matches = 0
    total_matches = 0
    truncated = False

    start_time = time.monotonic()
    deadline = start_time + args.timeout_ms / 1000 if args.timeout_ms > 0 else None
    stop = False

    def _timed_out() -> bool:
        if deadline is None:
            return False
        return time.monotonic() > deadline

    def _should_exclude(path: Path) -> bool:
        if not args.exclude_globs:
            return False
        candidates = _glob_candidates(path, root_path, run_dir)
        return bool(candidates) and _matches_patterns(candidates, args.exclude_globs)

    def _passes_include(path: Path) -> bool:
        if args.include_globs is None:
            return True
        candidates = _glob_candidates(path, root_path, run_dir)
        return bool(candidates) and _matches_patterns(candidates, args.include_globs)

    def _collect_snippet(
        match: re.Match,
        line_texts: list[str],
        line_starts: list[int],
    ) -> dict[str, object]:
        line_idx = _line_index_for_position(match.start(), line_starts)
        col = match.start() - line_starts[line_idx] + 1
        before_start = max(0, line_idx - args.context_lines)
        after_end = min(len(line_texts), line_idx + 1 + args.context_lines)
        return {
            "line": line_idx + 1,
            "col": col,
            "line_text": line_texts[line_idx],
            "context_before": line_texts[before_start:line_idx],
            "context_after": line_texts[line_idx + 1 : after_end],
        }

    def _process_file(file_path: Path) -> tuple[int, list[dict]]:
        nonlocal total_matches, truncated, stop
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0, []
        line_texts, line_starts = _split_lines(content)
        local_snippets: list[dict] = []
        local_matches = 0
        for match in pattern.finditer(content):
            if _timed_out():
                truncated = True
                stop = True
                break
            local_matches += 1
            total_matches += 1
            if len(local_snippets) < args.max_matches_per_file:
                snippet = _collect_snippet(match, line_texts, line_starts)
                local_snippets.append(snippet)
            else:
                truncated = True
            if total_matches >= args.max_results:
                truncated = True
                stop = True
                break
        return local_matches, local_snippets

    def _add_match_entry(relative_path: str, match_count: int, snippets: list[dict]):
        entries.append(
            {
                "path": relative_path,
                "match_count": match_count,
                "snippets": snippets,
            }
        )

    def _maybe_break():
        return stop or _timed_out() or total_matches >= args.max_results

    def _handle_root_file():
        nonlocal files_scanned, files_with_matches, truncated, stop
        if _should_exclude(root_path) or not _passes_include(root_path):
            return
        if not is_safe_path(run_dir, root_path):
            return
        files_scanned += 1
        match_count, snippets = _process_file(root_path)
        if match_count:
            files_with_matches += 1
            _add_match_entry(root_path.relative_to(run_dir).as_posix(), match_count, snippets)
        if _timed_out() or total_matches >= args.max_results:
            truncated = True
            stop = True

    if root_path.is_file():
        _handle_root_file()
    else:
        for current_root, dirs, files in os.walk(root_path, topdown=True):
            if stop or _timed_out() or total_matches >= args.max_results:
                truncated = True
                break
            dirs.sort()
            pruned_dirs: list[str] = []
            for directory in dirs:
                dir_path = Path(current_root) / directory
                if not is_safe_path(run_dir, dir_path):
                    continue
                if _should_exclude(dir_path):
                    continue
                pruned_dirs.append(directory)
            dirs[:] = pruned_dirs
            files.sort()
            for filename in files:
                if stop or _timed_out() or total_matches >= args.max_results:
                    truncated = True
                    break
                file_path = Path(current_root) / filename
                if not is_safe_path(run_dir, file_path):
                    continue
                if _should_exclude(file_path):
                    continue
                if not _passes_include(file_path):
                    continue
                files_scanned += 1
                match_count, snippets = _process_file(file_path)
                if match_count:
                    files_with_matches += 1
                    rel_path = file_path.relative_to(run_dir).as_posix()
                    _add_match_entry(rel_path, match_count, snippets)
            if stop and not _timed_out():
                break
    if entries:
        entries.sort(key=lambda entry: entry["path"])
    result = {
        "query": args.query,
        "is_regex": args.is_regex,
        "case_sensitive": args.case_sensitive,
        "truncated": truncated,
        "matches": entries,
        "stats": {
            "files_scanned": files_scanned,
            "files_with_matches": files_with_matches,
            "total_matches": total_matches,
        },
    }
    return JSONResponse(status_code=200, content={"ok": True, "result": result})
