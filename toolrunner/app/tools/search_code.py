from __future__ import annotations

import bisect
import os
import re
import time
from collections.abc import Sequence
from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import (
    allowed_root_strings,
    combine_exclude_patterns,
    is_under_allowed_root,
    normalize_globs,
    normalize_search_root,
    policy_allowed_roots,
    policy_metadata,
    policy_runtime_root,
    resolve_policy_path,
)
from ..models import SearchCodeArgs
from ..sandbox import is_safe_path
from .path_filters import first_matching_pattern, glob_candidates, matches_patterns


def _error(
    code: str,
    message: str,
    details: dict | None = None,
    status_code: int = 400,
    *,
    extra_patterns: Sequence[str] | None = None,
):
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": f"tool_runner.{code}",
                "message": message,
                "details": details or {},
            },
            "meta": {"policy": policy_metadata(extra_patterns)},
        },
    )


def _path_under_allowed_root(path: Path, extra_roots: tuple[Path, ...] | None = None) -> bool:
    return is_under_allowed_root(path, extra_roots)


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


def list_search_code(run_dir: Path, args: SearchCodeArgs, policy: dict | None = None):
    policy_roots = policy_allowed_roots(policy)
    if args.absolute_root:
        root_path = Path(args.absolute_root).resolve()
        if not _path_under_allowed_root(root_path, policy_roots):
            return _error(
                "PATH_NOT_ALLOWED",
                "absolute_root not permitted",
                extra_patterns=args.exclude_globs,
            )
        workspace_context = root_path
    else:
        try:
            root_path = resolve_policy_path(run_dir, args.root, policy)
        except ValueError as exc:
            error_code = (
                "PATH_OUTSIDE_WORKSPACE"
                if "path traversal outside of workspace" in str(exc)
                else "PATH_NOT_ALLOWED"
            )
            return _error(error_code, str(exc), extra_patterns=args.exclude_globs)
        requested_root = Path(args.root)
        if requested_root.is_absolute():
            workspace_context = root_path if root_path.is_dir() else root_path.parent
        else:
            workspace_context = policy_runtime_root(policy, "repo_root") or run_dir.resolve()

    allowed_context_roots = (normalize_search_root(workspace_context), *policy_roots)
    if not _path_under_allowed_root(root_path, allowed_context_roots):
        return _error(
            "PATH_NOT_ALLOWED",
            "root path not permitted",
            extra_patterns=args.exclude_globs,
        )

    if not root_path.exists():
        return _error(
            "NOT_FOUND",
            "root path missing",
            extra_patterns=args.exclude_globs,
        )

    pattern, compile_error = _prepare_pattern(args)
    if compile_error:
        return _error(
            "INVALID_ARGUMENT",
            "query pattern could not be compiled",
            compile_error,
            extra_patterns=args.exclude_globs,
        )

    entries: list[dict] = []
    files_scanned = 0
    files_with_matches = 0
    total_matches = 0
    truncated = False
    excluded_count = 0
    include_patterns = normalize_globs(args.include_globs)
    combined_patterns = combine_exclude_patterns(args.exclude_globs)
    excluded_pattern_counts: dict[str, int] = {pattern: 0 for pattern in combined_patterns}
    candidate_run_dir = workspace_context

    start_time = time.monotonic()
    deadline = start_time + args.timeout_ms / 1000 if args.timeout_ms > 0 else None
    stop = False

    def _timed_out() -> bool:
        if deadline is None:
            return False
        return time.monotonic() > deadline

    def _should_exclude(path: Path) -> bool:
        if not combined_patterns:
            return False
        candidates = glob_candidates(path, root_path, candidate_run_dir)
        pattern = first_matching_pattern(candidates, combined_patterns)
        if not pattern:
            return False
        nonlocal excluded_count
        excluded_count += 1
        excluded_pattern_counts[pattern] += 1
        return True

    def _passes_include(path: Path) -> bool:
        if not include_patterns:
            return True
        candidates = glob_candidates(path, root_path, candidate_run_dir)
        return bool(candidates) and matches_patterns(candidates, include_patterns)

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
        if not is_safe_path(candidate_run_dir, root_path):
            return
        files_scanned += 1
        match_count, snippets = _process_file(root_path)
        if match_count:
            files_with_matches += 1
            _add_match_entry(
                root_path.relative_to(candidate_run_dir).as_posix(), match_count, snippets
            )
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
                if not is_safe_path(candidate_run_dir, dir_path):
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
                if not is_safe_path(candidate_run_dir, file_path):
                    continue
                if _should_exclude(file_path):
                    continue
                if not _passes_include(file_path):
                    continue
                files_scanned += 1
                match_count, snippets = _process_file(file_path)
                if match_count:
                    files_with_matches += 1
                    rel_path = file_path.relative_to(candidate_run_dir).as_posix()
                    _add_match_entry(rel_path, match_count, snippets)
            if stop and not _timed_out():
                break
    if entries:
        entries.sort(key=lambda entry: entry["path"])
    policy_meta = policy_metadata(args.exclude_globs)
    stats: dict[str, object] = {
        "query": args.query,
        "is_regex": args.is_regex,
        "case_sensitive": args.case_sensitive,
        "truncated": truncated,
        "matches": entries,
        "stats": {
            "files_scanned": files_scanned,
            "files_with_matches": files_with_matches,
            "total_matches": total_matches,
            "excluded": excluded_count,
            "excluded_patterns": list(combined_patterns),
            "excluded_matches_by_pattern": dict(excluded_pattern_counts),
            "allowed_roots": list(allowed_root_strings()),
        },
    }
    return JSONResponse(
        status_code=200,
        content={"ok": True, "result": stats, "meta": {"policy": policy_meta}},
    )
