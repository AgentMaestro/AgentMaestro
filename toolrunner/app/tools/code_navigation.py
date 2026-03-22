from __future__ import annotations

import ast
import difflib
import fnmatch
import io
import json
import os
import re
import time
import tokenize
from collections.abc import Sequence
from pathlib import Path

from fastapi.responses import JSONResponse

from ..config import (
    _format_path_resolution_error,
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
from ..models import (
    FindReferencesArgs,
    FindSymbolArgs,
    JumpToSymbolArgs,
    ListSymbolsArgs,
    SearchFilesArgs,
)
from ..sandbox import is_safe_path
from .path_filters import first_matching_pattern, glob_candidates, matches_patterns

_PYTHON_SUFFIXES = {".py", ".pyi"}
_MARKDOWN_SUFFIXES = {".md", ".rst"}


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


def _resolve_root(
    run_dir: Path,
    requested_path: str,
    absolute_root: str | None,
    policy: dict | None,
) -> tuple[Path, Path, tuple[Path, ...]]:
    policy_roots = policy_allowed_roots(policy)
    if absolute_root:
        root_path = Path(absolute_root).resolve()
        if not _path_under_allowed_root(root_path, policy_roots):
            raise ValueError(
                _format_path_resolution_error(
                    requested_path=requested_path,
                    reason="absolute path is outside allowed roots",
                    resolved_path=root_path,
                    workspace_root=run_dir.resolve(),
                    policy_roots=policy_roots,
                )
            )
        workspace_context = root_path
    else:
        candidate = Path(requested_path)
        root_path = resolve_policy_path(run_dir, requested_path, policy)
        if candidate.is_absolute():
            workspace_context = root_path if root_path.is_dir() else root_path.parent
        else:
            workspace_context = policy_runtime_root(policy, "repo_root") or run_dir.resolve()
    allowed_context_roots = (normalize_search_root(workspace_context), *policy_roots)
    if not _path_under_allowed_root(root_path, allowed_context_roots):
        raise ValueError(
            _format_path_resolution_error(
                requested_path=requested_path,
                reason="resolved path is outside allowed roots",
                resolved_path=root_path,
                workspace_root=normalize_search_root(workspace_context),
                policy_roots=policy_roots,
            )
        )
    return root_path, workspace_context, policy_roots


def _is_test_path(path: Path) -> bool:
    lowered = path.as_posix().lower()
    return (
        "/tests/" in lowered
        or lowered.startswith("tests/")
        or "/test_" in lowered
        or lowered.endswith("_test.py")
        or lowered.endswith("test.py")
    )


def _detect_language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _PYTHON_SUFFIXES:
        return "python"
    if suffix in _MARKDOWN_SUFFIXES:
        return "markdown"
    return "text"


def _read_text(file_path: Path) -> str | None:
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


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
    import bisect

    idx = bisect.bisect_right(starts, position) - 1
    return max(0, idx)


def _line_context(line_idx: int, line_texts: list[str], context_lines: int) -> dict[str, object]:
    before_start = max(0, line_idx - context_lines)
    after_end = min(len(line_texts), line_idx + 1 + context_lines)
    return {
        "line": line_idx + 1,
        "line_text": line_texts[line_idx],
        "context_before": line_texts[before_start:line_idx],
        "context_after": line_texts[line_idx + 1 : after_end],
    }


def _context_excerpt(snippet: dict[str, object]) -> str:
    lines = list(snippet.get("context_before") or [])
    line_text = str(snippet.get("line_text") or "")
    if line_text:
        lines.append(line_text)
    lines.extend(list(snippet.get("context_after") or []))
    return "\n".join(str(line) for line in lines if str(line).strip())


def _navigation_item(
    *,
    path: str,
    name: str | None = None,
    kind: str | None = None,
    line: int | None = None,
    column: int | None = None,
    container: str | None = None,
    score: float | int | None = None,
    excerpt: str | None = None,
    signature: str | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {"path": path}
    if name is not None:
        item["name"] = name
    if kind is not None:
        item["kind"] = kind
    if line is not None:
        item["line"] = line
    if column is not None:
        item["column"] = column
    if container is not None:
        item["container"] = container
    if score is not None:
        item["score"] = score
    if excerpt is not None:
        item["excerpt"] = excerpt
    if signature is not None:
        item["signature"] = signature
    return item


def _compact_navigation_result(
    *,
    tool: str,
    query: str,
    scope: dict[str, object],
    items: list[dict[str, object]],
    stats: dict[str, object],
    truncated: bool,
    returned_count: int,
    max_results_used: int,
    selection: dict[str, object] | None = None,
    selection_excerpt: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "tool": tool,
        "compact": True,
        "query": query,
        "scope": scope,
        "items": items,
        "stats": stats,
        "truncated": truncated,
        "returned_count": returned_count,
        "max_results_used": max_results_used,
        "selection": selection,
        "selection_excerpt": selection_excerpt,
    }
    return result


def _compact_symbol_item(symbol: dict[str, object], *, compact: bool) -> dict[str, object]:
    item = _navigation_item(
        path=str(symbol.get("path") or ""),
        name=str(symbol.get("name") or ""),
        kind=str(symbol.get("kind") or ""),
        line=int(symbol.get("line") or 0) or None,
        column=int(symbol.get("column") or 0) or None,
        container=str(symbol.get("container") or "") or None,
        signature=str(symbol.get("signature") or "") or None,
    )
    if symbol.get("public") is not None:
        item["public"] = bool(symbol.get("public"))
    if not compact:
        signature = str(symbol.get("signature") or "")
        docstring = str(symbol.get("docstring") or "")
        container = str(symbol.get("container") or "")
        language = str(symbol.get("language") or "")
        qualified_name = str(symbol.get("qualified_name") or "")
        if signature:
            item["signature"] = signature
        if docstring:
            item["excerpt"] = docstring
        if container:
            item["container"] = container
        if language:
            item["language"] = language
        if qualified_name:
            item["qualified_name"] = qualified_name
    return item


def _compact_selection_item(item: dict[str, object] | None) -> dict[str, object] | None:
    if not item:
        return None
    selection: dict[str, object] = {}
    for key in ("path", "name", "kind", "line", "column", "container", "signature", "score"):
        value = item.get(key)
        if value not in (None, ""):
            selection[key] = value
    return selection or None


def _compact_match_item(
    match: dict[str, object], *, compact: bool, include_excerpt: bool = False
) -> dict[str, object]:
    item = _navigation_item(
        path=str(match.get("path") or ""),
        name=str(match.get("name") or match.get("symbol") or ""),
        kind=str(match.get("kind") or ""),
        line=int(match.get("line") or 0) or None,
        column=int(match.get("column") or 0) or None,
        container=str(match.get("container") or "") or None,
        score=match.get("score"),
        signature=str(match.get("signature") or "") or None,
    )
    if not compact:
        for key in ("language", "qualified_name", "public"):
            value = match.get(key)
            if value not in (None, ""):
                item[key] = value
    if include_excerpt:
        excerpt = str(match.get("excerpt") or "")
        if excerpt:
            item["excerpt"] = excerpt
    return item


def _line_numbered_excerpt(file_path: Path, line_number: int, context_lines: int) -> str | None:
    content = _read_text(file_path)
    if content is None:
        return None
    line_texts, _ = _split_lines(content)
    line_idx = max(0, min(len(line_texts) - 1, line_number - 1))
    excerpt_start = max(0, line_idx - max(0, context_lines))
    excerpt_end = min(len(line_texts), line_idx + 1 + max(0, context_lines))
    return "\n".join(
        f"{index + 1}: {line_texts[index]}" for index in range(excerpt_start, excerpt_end)
    )


def _resolve_compact_file_path(
    run_dir: Path,
    scope: str,
    absolute_root: str | None,
    policy: dict | None,
    rel_path: str,
) -> Path:
    try:
        _, workspace_context, _ = _resolve_root(run_dir, scope or ".", absolute_root, policy)
    except ValueError:
        workspace_context = run_dir
    return (
        (workspace_context / rel_path).resolve()
        if rel_path and not Path(rel_path).is_absolute()
        else Path(rel_path)
    )


def _reference_excerpt(hit: dict[str, object]) -> str:
    before = [str(line) for line in (hit.get("context_before") or []) if str(line).strip()]
    line_text = str(hit.get("line_text") or "").strip()
    after = [str(line) for line in (hit.get("context_after") or []) if str(line).strip()]
    parts = [*before, line_text, *after]
    return "\n".join(part for part in parts if part)


def _reference_excerpt_with_line_numbers(hit: dict[str, object]) -> str:
    line_number = int(hit.get("line") or 0)
    before = [str(line) for line in (hit.get("context_before") or []) if str(line).strip()]
    line_text = str(hit.get("line_text") or "").strip()
    after = [str(line) for line in (hit.get("context_after") or []) if str(line).strip()]
    start_line = max(1, line_number - len(before))
    lines: list[str] = []
    for offset, text in enumerate(before):
        lines.append(f"{start_line + offset}: {text}")
    if line_text:
        lines.append(f"{line_number}: {line_text}")
    for offset, text in enumerate(after, start=1):
        lines.append(f"{line_number + offset}: {text}")
    return "\n".join(lines)


def _file_matches_search(
    query: str, candidate_path: str, candidate_name: str, *, is_regex: bool, case_sensitive: bool
) -> bool:
    if is_regex:
        flags = re.MULTILINE
        if not case_sensitive:
            flags |= re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error:
            return False
        return bool(pattern.search(candidate_path) or pattern.search(candidate_name))
    needle = query if case_sensitive else query.lower()
    haystack_path = candidate_path if case_sensitive else candidate_path.lower()
    haystack_name = candidate_name if case_sensitive else candidate_name.lower()
    if any(char in query for char in "*?[]"):
        pattern = query if case_sensitive else query.lower()
        return fnmatch.fnmatchcase(haystack_path, pattern) or fnmatch.fnmatchcase(
            haystack_name, pattern
        )
    return needle in haystack_path or needle in haystack_name


def _match_score(
    query: str, candidate_path: str, candidate_name: str, *, case_sensitive: bool
) -> int:
    if not case_sensitive:
        query = query.lower()
        candidate_path = candidate_path.lower()
        candidate_name = candidate_name.lower()
    if query == candidate_name:
        return 0
    if query == candidate_path:
        return 1
    if candidate_name.startswith(query):
        return 2
    if query in candidate_name:
        return 3
    if candidate_path.startswith(query):
        return 4
    if query in candidate_path:
        return 5
    return 6


def _iter_files(
    root_path: Path,
    *,
    workspace_context: Path,
    include_globs: list[str],
    exclude_globs: list[str],
    max_depth: int,
    include_tests: bool,
):
    combined_patterns = combine_exclude_patterns(exclude_globs)
    candidate_run_dir = workspace_context
    if root_path.is_file():
        if include_tests is False and _is_test_path(root_path):
            return
        yield from [(root_path, 0)]
        return
    for current_root, dirs, files in os.walk(root_path, topdown=True):
        current_root_path = Path(current_root)
        try:
            current_depth = len(
                [part for part in current_root_path.relative_to(root_path).parts if part != "."]
            )
        except ValueError:
            current_depth = 0
        if current_depth >= max_depth:
            dirs[:] = []
        dirs.sort()
        next_dirs: list[str] = []
        for directory in dirs:
            dir_path = current_root_path / directory
            if not is_safe_path(candidate_run_dir, dir_path):
                continue
            if include_tests is False and _is_test_path(dir_path):
                continue
            if combined_patterns and first_matching_pattern(
                glob_candidates(dir_path, root_path, candidate_run_dir, True), combined_patterns
            ):
                continue
            next_dirs.append(directory)
        dirs[:] = next_dirs
        files.sort()
        for filename in files:
            file_path = current_root_path / filename
            if not is_safe_path(candidate_run_dir, file_path):
                continue
            if include_tests is False and _is_test_path(file_path):
                continue
            if combined_patterns and first_matching_pattern(
                glob_candidates(file_path, root_path, candidate_run_dir), combined_patterns
            ):
                continue
            if include_globs and not matches_patterns(
                glob_candidates(file_path, root_path, candidate_run_dir), include_globs
            ):
                continue
            yield file_path, current_depth + 1


def _collect_python_symbols(
    content: str,
    *,
    rel_path: str,
    include_private: bool,
    include_docstrings: bool,
) -> list[dict[str, object]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    module_name = Path(rel_path).stem or rel_path
    records: list[dict[str, object]] = []

    def add_record(name: str, kind: str, node: ast.AST, container: str) -> None:
        if not include_private and name.startswith("_"):
            return
        signature = ""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            try:
                signature = ast.unparse(node)
            except Exception:
                signature = f"{kind} {name}"
        docstring = ""
        if include_docstrings and isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            raw_docstring = ast.get_docstring(node) or ""
            docstring = " ".join(raw_docstring.strip().split())[:300]
        records.append(
            {
                "name": name,
                "qualified_name": f"{module_name}.{name}"
                if not container
                else f"{container}.{name}",
                "kind": kind,
                "path": rel_path,
                "line": getattr(node, "lineno", 1),
                "column": getattr(node, "col_offset", 0) + 1,
                "language": "python",
                "container": container or module_name,
                "signature": signature,
                "docstring": docstring,
                "public": not name.startswith("_"),
            }
        )

    def walk(node: ast.AST, parents: list[str], inside_class: bool = False) -> None:
        container = ".".join(parents) if parents else module_name
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                add_record(child.name, "class", child, container)
                walk(child, [*parents, child.name], inside_class=True)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add_record(child.name, "method" if inside_class else "function", child, container)
                walk(child, [*parents, child.name], inside_class=inside_class)
            elif isinstance(child, ast.Assign) and not parents:
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        add_record(
                            target.id,
                            "constant" if target.id.isupper() else "variable",
                            target,
                            container,
                        )
            elif (
                isinstance(child, ast.AnnAssign)
                and not parents
                and isinstance(child.target, ast.Name)
            ):
                add_record(
                    child.target.id,
                    "constant" if child.target.id.isupper() else "variable",
                    child.target,
                    container,
                )
            else:
                walk(child, parents, inside_class=inside_class)

    walk(tree, [])
    return records


def _collect_generic_symbols(
    content: str,
    *,
    rel_path: str,
    include_private: bool,
    include_docstrings: bool,
) -> list[dict[str, object]]:
    suffix = Path(rel_path).suffix.lower()
    line_texts, _ = _split_lines(content)
    records: list[dict[str, object]] = []
    if suffix in _MARKDOWN_SUFFIXES:
        for line_number, line in enumerate(line_texts, start=1):
            stripped = line.lstrip()
            if not stripped.startswith("#"):
                continue
            heading = stripped.lstrip("#").strip()
            if not heading or (not include_private and heading.startswith("_")):
                continue
            records.append(
                {
                    "name": heading,
                    "qualified_name": f"{Path(rel_path).stem}.{heading}",
                    "kind": "heading",
                    "path": rel_path,
                    "line": line_number,
                    "column": line.index("#") + 1,
                    "language": "markdown",
                    "container": Path(rel_path).stem,
                    "signature": heading,
                    "docstring": heading if include_docstrings else "",
                    "public": True,
                }
            )
        return records
    patterns = (
        (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
        (re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)"), "function"),
        (re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)"), "function"),
        (
            re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*="),
            "variable",
        ),
    )
    for line_number, line in enumerate(line_texts, start=1):
        for pattern, kind in patterns:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group(1)
            if not include_private and name.startswith("_"):
                continue
            records.append(
                {
                    "name": name,
                    "qualified_name": f"{Path(rel_path).stem}.{name}",
                    "kind": kind,
                    "path": rel_path,
                    "line": line_number,
                    "column": match.start(1) + 1,
                    "language": "text",
                    "container": Path(rel_path).stem,
                    "signature": name,
                    "docstring": line.strip() if include_docstrings else "",
                    "public": not name.startswith("_"),
                }
            )
            break
    return records


def _collect_symbols_for_file(
    file_path: Path,
    *,
    rel_path: str,
    include_private: bool,
    include_docstrings: bool,
    language_filter: str | None,
) -> list[dict[str, object]]:
    language = _detect_language(file_path)
    if language_filter:
        normalized = language_filter.strip().lower()
        if normalized in {"py", "python"}:
            normalized = "python"
        elif normalized in {"md", "markdown"}:
            normalized = "markdown"
        if normalized != language:
            return []
    content = _read_text(file_path)
    if content is None:
        return []
    if language == "python":
        return _collect_python_symbols(
            content,
            rel_path=rel_path,
            include_private=include_private,
            include_docstrings=include_docstrings,
        )
    return _collect_generic_symbols(
        content,
        rel_path=rel_path,
        include_private=include_private,
        include_docstrings=include_docstrings,
    )


def search_files(run_dir: Path, args: SearchFilesArgs, policy: dict | None = None):
    requested_path = args.scope or "."
    try:
        root_path, workspace_context, _ = _resolve_root(
            run_dir, requested_path, args.absolute_root, policy
        )
    except ValueError as exc:
        error_code = (
            "PATH_OUTSIDE_WORKSPACE"
            if "path traversal outside of workspace" in str(exc)
            else "PATH_NOT_ALLOWED"
        )
        return _error(error_code, str(exc), extra_patterns=args.exclude_globs)
    if not root_path.exists():
        return _error("NOT_FOUND", "root path missing", extra_patterns=args.exclude_globs)

    include_patterns = normalize_globs(args.include_globs)
    exclude_patterns = combine_exclude_patterns(args.exclude_globs)
    candidate_run_dir = workspace_context
    entries: list[dict[str, object]] = []
    files_scanned = 0
    dirs_scanned = 0
    excluded_count = 0
    excluded_pattern_counts: dict[str, int] = {pattern: 0 for pattern in exclude_patterns}
    truncated = False
    deadline = time.monotonic() + (args.timeout_ms / 1000 if args.timeout_ms > 0 else 0)

    def _timed_out() -> bool:
        return args.timeout_ms > 0 and time.monotonic() > deadline

    def _should_exclude(path: Path, is_dir: bool) -> bool:
        nonlocal excluded_count
        if not exclude_patterns:
            return False
        pattern = first_matching_pattern(
            glob_candidates(path, root_path, candidate_run_dir, is_dir), exclude_patterns
        )
        if not pattern:
            return False
        excluded_count += 1
        excluded_pattern_counts[pattern] += 1
        return True

    def _passes_include(path: Path, is_dir: bool) -> bool:
        if not include_patterns:
            return True
        if is_dir:
            return True
        return matches_patterns(
            glob_candidates(path, root_path, candidate_run_dir, is_dir), include_patterns
        )

    def _append(path: Path, entry_type: str) -> None:
        relative_path = path.relative_to(candidate_run_dir).as_posix()
        entries.append(
            {
                "type": entry_type,
                "path": relative_path,
                "name": path.name,
                "score": _match_score(
                    args.query, relative_path, path.name, case_sensitive=args.case_sensitive
                ),
            }
        )

    if root_path.is_file():
        if (
            is_safe_path(candidate_run_dir, root_path)
            and not _should_exclude(root_path, False)
            and _passes_include(root_path, False)
        ):
            if not (args.include_tests is False and _is_test_path(root_path)):
                files_scanned += 1
                if _file_matches_search(
                    args.query,
                    root_path.relative_to(candidate_run_dir).as_posix(),
                    root_path.name,
                    is_regex=args.is_regex,
                    case_sensitive=args.case_sensitive,
                ):
                    _append(root_path, "file")
    else:
        max_depth = max(0, int(args.max_depth))
        for current_root, dirs, files in os.walk(root_path, topdown=True):
            if _timed_out() or len(entries) >= args.max_results:
                truncated = True
                break
            current_root_path = Path(current_root)
            try:
                current_depth = len(
                    [part for part in current_root_path.relative_to(root_path).parts if part != "."]
                )
            except ValueError:
                current_depth = 0
            if current_depth >= max_depth:
                dirs[:] = []
            dirs.sort()
            next_dirs: list[str] = []
            for directory in dirs:
                dir_path = current_root_path / directory
                if not is_safe_path(candidate_run_dir, dir_path):
                    continue
                if args.include_tests is False and _is_test_path(dir_path):
                    continue
                if _should_exclude(dir_path, True) or not _passes_include(dir_path, True):
                    continue
                dirs_scanned += 1
                next_dirs.append(directory)
                if args.include_dirs and _file_matches_search(
                    args.query,
                    dir_path.relative_to(candidate_run_dir).as_posix(),
                    dir_path.name,
                    is_regex=args.is_regex,
                    case_sensitive=args.case_sensitive,
                ):
                    _append(dir_path, "dir")
            dirs[:] = next_dirs
            for filename in files:
                if _timed_out() or len(entries) >= args.max_results:
                    truncated = True
                    break
                file_path = current_root_path / filename
                if not is_safe_path(candidate_run_dir, file_path):
                    continue
                if _should_exclude(file_path, False) or not _passes_include(file_path, False):
                    continue
                if args.include_tests is False and _is_test_path(file_path):
                    continue
                files_scanned += 1
                if args.include_files and _file_matches_search(
                    args.query,
                    file_path.relative_to(candidate_run_dir).as_posix(),
                    file_path.name,
                    is_regex=args.is_regex,
                    case_sensitive=args.case_sensitive,
                ):
                    _append(file_path, "file")
            if truncated:
                break

    entries.sort(
        key=lambda entry: (
            int(entry.get("score") or 0),
            0 if entry.get("type") == "file" else 1,
            str(entry.get("path") or ""),
        )
    )
    items = [
        _navigation_item(
            path=str(entry.get("path") or ""),
            name=str(entry.get("name") or ""),
            kind=str(entry.get("type") or ""),
            score=entry.get("score"),
        )
        for entry in entries[: args.max_results]
    ]
    result = {
        "query": args.query,
        "scope": args.scope,
        "compact": args.compact,
        "is_regex": args.is_regex,
        "case_sensitive": args.case_sensitive,
        "truncated": truncated,
        "returned_count": len(items),
        "max_results_used": args.max_results,
        "matches": entries[: args.max_results],
        "items": items,
        "stats": {
            "files_scanned": files_scanned,
            "dirs_scanned": dirs_scanned,
            "total_matches": min(len(entries), args.max_results),
            "excluded": excluded_count,
            "excluded_patterns": list(exclude_patterns),
            "excluded_matches_by_pattern": dict(excluded_pattern_counts),
            "allowed_roots": list(allowed_root_strings()),
        },
    }
    if args.compact:
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": _compact_navigation_result(
                    tool="search_files",
                    query=args.query,
                    scope={"scope": args.scope},
                    items=items,
                    stats=result["stats"],
                    truncated=truncated,
                    returned_count=len(items),
                    max_results_used=args.max_results,
                ),
                "meta": {"policy": policy_metadata(args.exclude_globs)},
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": result,
            "meta": {"policy": policy_metadata(args.exclude_globs)},
        },
    )


def list_symbols(run_dir: Path, args: ListSymbolsArgs, policy: dict | None = None):
    requested_path = args.scope or "."
    try:
        root_path, workspace_context, _ = _resolve_root(
            run_dir, requested_path, args.absolute_root, policy
        )
    except ValueError as exc:
        error_code = (
            "PATH_OUTSIDE_WORKSPACE"
            if "path traversal outside of workspace" in str(exc)
            else "PATH_NOT_ALLOWED"
        )
        return _error(error_code, str(exc), extra_patterns=args.exclude_globs)
    if not root_path.exists():
        return _error("NOT_FOUND", "root path missing", extra_patterns=args.exclude_globs)

    include_patterns = normalize_globs(args.include_globs)
    exclude_patterns = combine_exclude_patterns(args.exclude_globs)
    candidate_run_dir = workspace_context
    entries: list[dict[str, object]] = []
    files_scanned = 0
    files_with_symbols = 0
    total_symbols = 0
    excluded_count = 0
    excluded_pattern_counts: dict[str, int] = {pattern: 0 for pattern in exclude_patterns}
    truncated = False
    deadline = time.monotonic() + (args.timeout_ms / 1000 if args.timeout_ms > 0 else 0)

    def _timed_out() -> bool:
        return args.timeout_ms > 0 and time.monotonic() > deadline

    def _should_exclude(path: Path, is_dir: bool) -> bool:
        nonlocal excluded_count
        if not exclude_patterns:
            return False
        pattern = first_matching_pattern(
            glob_candidates(path, root_path, candidate_run_dir, is_dir), exclude_patterns
        )
        if not pattern:
            return False
        excluded_count += 1
        excluded_pattern_counts[pattern] += 1
        return True

    def _passes_include(path: Path, is_dir: bool) -> bool:
        if not include_patterns:
            return True
        if is_dir:
            return True
        return matches_patterns(
            glob_candidates(path, root_path, candidate_run_dir, is_dir), include_patterns
        )

    def _append_file(path: Path, symbols: list[dict[str, object]]) -> None:
        nonlocal files_with_symbols, total_symbols
        rel_path = path.relative_to(candidate_run_dir).as_posix()
        if symbols:
            files_with_symbols += 1
            total_symbols += len(symbols)
        entries.append(
            {
                "path": rel_path,
                "language": _detect_language(path),
                "symbol_count": len(symbols),
                "symbols": symbols[: args.max_results],
            }
        )

    if root_path.is_file():
        if (
            is_safe_path(candidate_run_dir, root_path)
            and not _should_exclude(root_path, False)
            and _passes_include(root_path, False)
        ):
            if not (args.include_tests is False and _is_test_path(root_path)):
                files_scanned += 1
                _append_file(
                    root_path,
                    _collect_symbols_for_file(
                        root_path,
                        rel_path=root_path.relative_to(candidate_run_dir).as_posix(),
                        include_private=args.include_private,
                        include_docstrings=args.include_docstrings,
                        language_filter=args.language,
                    ),
                )
    else:
        max_depth = max(0, int(args.max_depth))
        for current_root, dirs, files in os.walk(root_path, topdown=True):
            if _timed_out():
                truncated = True
                break
            current_root_path = Path(current_root)
            try:
                current_depth = len(
                    [part for part in current_root_path.relative_to(root_path).parts if part != "."]
                )
            except ValueError:
                current_depth = 0
            if current_depth >= max_depth:
                dirs[:] = []
            dirs.sort()
            next_dirs: list[str] = []
            for directory in dirs:
                dir_path = current_root_path / directory
                if not is_safe_path(candidate_run_dir, dir_path):
                    continue
                if _should_exclude(dir_path, True) or not _passes_include(dir_path, True):
                    continue
                next_dirs.append(directory)
            dirs[:] = next_dirs
            files.sort()
            for filename in files:
                if _timed_out() or len(entries) >= args.max_results:
                    truncated = True
                    break
                file_path = current_root_path / filename
                if not is_safe_path(candidate_run_dir, file_path):
                    continue
                if _should_exclude(file_path, False) or not _passes_include(file_path, False):
                    continue
                if args.include_tests is False and _is_test_path(file_path):
                    continue
                files_scanned += 1
                _append_file(
                    file_path,
                    _collect_symbols_for_file(
                        file_path,
                        rel_path=file_path.relative_to(candidate_run_dir).as_posix(),
                        include_private=args.include_private,
                        include_docstrings=args.include_docstrings,
                        language_filter=args.language,
                    ),
                )
            if truncated:
                break

    sorted_entries = sorted(entries, key=lambda entry: str(entry.get("path") or ""))
    result_items = [
        _compact_symbol_item(symbol, compact=args.compact)
        for entry in sorted_entries
        for symbol in list(entry.get("symbols") or [])
    ][: args.max_results]
    result = {
        "scope": args.scope,
        "compact": args.compact,
        "truncated": truncated,
        "returned_count": len(result_items),
        "max_results_used": args.max_results,
        "entries": sorted_entries,
        "items": result_items,
        "stats": {
            "files_scanned": files_scanned,
            "files_with_symbols": files_with_symbols,
            "total_symbols": total_symbols,
            "excluded": excluded_count,
            "excluded_patterns": list(exclude_patterns),
            "excluded_matches_by_pattern": dict(excluded_pattern_counts),
            "allowed_roots": list(allowed_root_strings()),
        },
    }
    if args.compact:
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": _compact_navigation_result(
                    tool="list_symbols",
                    query=args.scope,
                    scope={"scope": args.scope},
                    items=result_items,
                    stats=result["stats"],
                    truncated=truncated,
                    returned_count=len(result_items),
                    max_results_used=args.max_results,
                ),
                "meta": {"policy": policy_metadata(args.exclude_globs)},
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": result,
            "meta": {"policy": policy_metadata(args.exclude_globs)},
        },
    )


def _symbol_name_variants(name: str) -> list[str]:
    raw = str(name or "").strip()
    if not raw:
        return []
    parts = [part for part in re.split(r"[^A-Za-z0-9_]+", raw) if part]
    variants = [raw, *parts]
    if parts:
        variants.append(parts[-1])
    seen: set[str] = set()
    result: list[str] = []
    for variant in variants:
        candidate = variant.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _collect_symbols_under_root(
    root_path: Path,
    *,
    workspace_context: Path,
    include_patterns: list[str],
    exclude_patterns: list[str],
    include_private: bool,
    include_docstrings: bool,
    include_tests: bool,
    language_filter: str | None,
    max_depth: int,
    timeout_ms: int,
) -> list[dict[str, object]]:
    candidate_run_dir = workspace_context
    symbols: list[dict[str, object]] = []
    deadline = time.monotonic() + (timeout_ms / 1000 if timeout_ms > 0 else 0)
    for file_path, _depth in _iter_files(
        root_path,
        workspace_context=workspace_context,
        include_globs=include_patterns,
        exclude_globs=exclude_patterns,
        max_depth=max_depth,
        include_tests=include_tests,
    ):
        if timeout_ms > 0 and time.monotonic() > deadline:
            break
        rel_path = file_path.relative_to(candidate_run_dir).as_posix()
        symbols.extend(
            _collect_symbols_for_file(
                file_path,
                rel_path=rel_path,
                include_private=include_private,
                include_docstrings=include_docstrings,
                language_filter=language_filter,
            )
        )
    return symbols


def find_symbol(run_dir: Path, args: FindSymbolArgs, policy: dict | None = None):
    requested_path = args.scope or "."
    try:
        root_path, workspace_context, _ = _resolve_root(
            run_dir, requested_path, args.absolute_root, policy
        )
    except ValueError as exc:
        error_code = (
            "PATH_OUTSIDE_WORKSPACE"
            if "path traversal outside of workspace" in str(exc)
            else "PATH_NOT_ALLOWED"
        )
        return _error(error_code, str(exc), extra_patterns=args.exclude_globs)
    if not root_path.exists():
        return _error("NOT_FOUND", "root path missing", extra_patterns=args.exclude_globs)

    include_patterns = normalize_globs(args.include_globs)
    exclude_patterns = combine_exclude_patterns(args.exclude_globs)
    all_symbols = _collect_symbols_under_root(
        root_path,
        workspace_context=workspace_context,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        include_private=args.include_private,
        include_docstrings=True,
        include_tests=args.include_tests,
        language_filter=args.language,
        max_depth=max(0, args.max_depth),
        timeout_ms=args.timeout_ms,
    )
    variants = _symbol_name_variants(args.symbol_name)
    matches: list[dict[str, object]] = []
    for symbol in all_symbols:
        if not args.include_private and not bool(symbol.get("public", True)):
            continue
        if args.kind and str(symbol.get("kind") or "").lower() != str(args.kind).strip().lower():
            continue
        if args.scope and str(args.scope).replace("\\", "/") not in str(symbol.get("path") or ""):
            continue
        score = 0.0
        qualified = str(symbol.get("qualified_name") or "")
        name = str(symbol.get("name") or "")
        for variant in variants:
            if variant == qualified:
                score = max(score, 1.0)
            if variant == name:
                score = max(score, 0.98)
            if qualified.endswith(f".{variant}"):
                score = max(score, 0.94)
            if args.fuzzy:
                score = max(
                    score, difflib.SequenceMatcher(None, variant.lower(), name.lower()).ratio()
                )
        if score <= 0:
            continue
        enriched = dict(symbol)
        enriched["score"] = round(float(score), 4)
        matches.append(enriched)
    matches.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            str(item.get("path") or ""),
            int(item.get("line") or 0),
        )
    )
    truncated = len(matches) > args.max_results
    result = {
        "query": args.symbol_name,
        "kind": args.kind or "",
        "fuzzy": args.fuzzy,
        "scope": args.scope,
        "compact": args.compact,
        "truncated": truncated,
        "returned_count": len(matches[: args.max_results]),
        "max_results_used": args.max_results,
        "matches": matches[: args.max_results],
        "items": [
            _compact_match_item(match, compact=args.compact)
            for match in matches[: args.max_results]
        ],
        "stats": {
            "total_symbols": len(all_symbols),
            "total_matches": min(len(matches), args.max_results),
            "allowed_roots": list(allowed_root_strings()),
        },
    }
    if args.compact:
        selection = _compact_selection_item(result["items"][0] if result["items"] else None)
        selection_excerpt = None
        if matches:
            file_path = _resolve_compact_file_path(
                run_dir,
                args.scope,
                args.absolute_root,
                policy,
                str(matches[0].get("path") or ""),
            )
            selection_excerpt = _line_numbered_excerpt(
                file_path, int(matches[0].get("line") or 1), 4
            )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": _compact_navigation_result(
                    tool="find_symbol",
                    query=args.symbol_name,
                    scope={"scope": args.scope},
                    items=result["items"],
                    stats=result["stats"],
                    truncated=truncated,
                    returned_count=len(result["items"]),
                    max_results_used=args.max_results,
                    selection=selection,
                    selection_excerpt=selection_excerpt,
                ),
                "meta": {"policy": policy_metadata(args.exclude_globs)},
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": result,
            "meta": {"policy": policy_metadata(args.exclude_globs)},
        },
    )


def _reference_hits_for_text(
    content: str,
    *,
    path: str,
    query_variants: list[str],
    include_declarations: bool,
    include_comments: bool,
    include_strings: bool,
    language: str,
    context_lines: int,
) -> list[dict[str, object]]:
    line_texts, _ = _split_lines(content)
    hits: list[dict[str, object]] = []
    if language == "python":
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(content).readline))
        except tokenize.TokenError:
            tokens = []
        for index, token in enumerate(tokens):
            if token.type == tokenize.NAME and token.string in query_variants:
                prev_sig = None
                for prev_index in range(index - 1, -1, -1):
                    prev = tokens[prev_index]
                    if prev.type in {
                        tokenize.NL,
                        tokenize.NEWLINE,
                        tokenize.INDENT,
                        tokenize.DEDENT,
                        tokenize.COMMENT,
                    }:
                        continue
                    prev_sig = prev
                    break
                next_sig = None
                for next_index in range(index + 1, len(tokens)):
                    nxt = tokens[next_index]
                    if nxt.type in {
                        tokenize.NL,
                        tokenize.NEWLINE,
                        tokenize.INDENT,
                        tokenize.DEDENT,
                        tokenize.COMMENT,
                    }:
                        continue
                    next_sig = nxt
                    break
                prev_value = prev_sig.string if prev_sig else ""
                next_value = next_sig.string if next_sig else ""
                kind = "usage"
                if prev_value in {"def", "class", "import", "from"}:
                    kind = "declaration"
                elif next_value == "(":
                    kind = "call"
                elif prev_value == "as":
                    kind = "import"
                if kind == "declaration" and not include_declarations:
                    continue
                line_idx = token.start[0] - 1
                snippet = _line_context(line_idx, line_texts, context_lines)
                hits.append(
                    {
                        "path": path,
                        "line": snippet["line"],
                        "column": token.start[1] + 1,
                        "kind": kind,
                        "name": token.string,
                        "line_text": snippet["line_text"],
                        "context_before": snippet["context_before"],
                        "context_after": snippet["context_after"],
                        "language": "python",
                    }
                )
            elif include_strings and token.type == tokenize.STRING:
                if any(variant and variant in token.string for variant in query_variants):
                    line_idx = token.start[0] - 1
                    snippet = _line_context(line_idx, line_texts, context_lines)
                    hits.append(
                        {
                            "path": path,
                            "line": snippet["line"],
                            "column": token.start[1] + 1,
                            "kind": "string",
                            "name": next(
                                variant for variant in query_variants if variant in token.string
                            ),
                            "line_text": snippet["line_text"],
                            "context_before": snippet["context_before"],
                            "context_after": snippet["context_after"],
                            "language": "python",
                        }
                    )
            elif include_comments and token.type == tokenize.COMMENT:
                if any(variant and variant in token.string for variant in query_variants):
                    line_idx = token.start[0] - 1
                    snippet = _line_context(line_idx, line_texts, context_lines)
                    hits.append(
                        {
                            "path": path,
                            "line": snippet["line"],
                            "column": token.start[1] + 1,
                            "kind": "comment",
                            "name": next(
                                variant for variant in query_variants if variant in token.string
                            ),
                            "line_text": snippet["line_text"],
                            "context_before": snippet["context_before"],
                            "context_after": snippet["context_after"],
                            "language": "python",
                        }
                    )
        return hits
    patterns = [re.compile(rf"\b{re.escape(variant)}\b") for variant in query_variants if variant]
    if not patterns:
        return hits
    for line_number, line in enumerate(line_texts, start=1):
        if not include_comments and line.lstrip().startswith("#"):
            continue
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            snippet = _line_context(line_number - 1, line_texts, context_lines)
            hits.append(
                {
                    "path": path,
                    "line": snippet["line"],
                    "column": match.start() + 1,
                    "kind": "usage",
                    "name": match.group(0),
                    "line_text": snippet["line_text"],
                    "context_before": snippet["context_before"],
                    "context_after": snippet["context_after"],
                    "language": language,
                }
            )
            break
    return hits


def find_references(run_dir: Path, args: FindReferencesArgs, policy: dict | None = None):
    requested_path = args.scope or "."
    try:
        root_path, workspace_context, _ = _resolve_root(
            run_dir, requested_path, args.absolute_root, policy
        )
    except ValueError as exc:
        error_code = (
            "PATH_OUTSIDE_WORKSPACE"
            if "path traversal outside of workspace" in str(exc)
            else "PATH_NOT_ALLOWED"
        )
        return _error(error_code, str(exc), extra_patterns=args.exclude_globs)
    if not root_path.exists():
        return _error("NOT_FOUND", "root path missing", extra_patterns=args.exclude_globs)

    include_patterns = normalize_globs(args.include_globs)
    exclude_patterns = combine_exclude_patterns(args.exclude_globs)
    candidate_run_dir = workspace_context
    variants = _symbol_name_variants(args.symbol)
    entries: list[dict[str, object]] = []
    files_scanned = 0
    excluded_count = 0
    excluded_pattern_counts: dict[str, int] = {pattern: 0 for pattern in exclude_patterns}
    truncated = False
    deadline = time.monotonic() + (args.timeout_ms / 1000 if args.timeout_ms > 0 else 0)

    def _timed_out() -> bool:
        return args.timeout_ms > 0 and time.monotonic() > deadline

    def _should_exclude(path: Path, is_dir: bool) -> bool:
        nonlocal excluded_count
        if not exclude_patterns:
            return False
        pattern = first_matching_pattern(
            glob_candidates(path, root_path, candidate_run_dir, is_dir), exclude_patterns
        )
        if not pattern:
            return False
        excluded_count += 1
        excluded_pattern_counts[pattern] += 1
        return True

    def _passes_include(path: Path, is_dir: bool) -> bool:
        if not include_patterns:
            return True
        if is_dir:
            return True
        return matches_patterns(
            glob_candidates(path, root_path, candidate_run_dir, is_dir), include_patterns
        )

    def _scan_file(file_path: Path) -> None:
        nonlocal files_scanned, truncated
        if _timed_out() or len(entries) >= args.max_results:
            truncated = True
            return
        if not is_safe_path(candidate_run_dir, file_path):
            return
        if _should_exclude(file_path, False) or not _passes_include(file_path, False):
            return
        if args.include_tests is False and _is_test_path(file_path):
            return
        content = _read_text(file_path)
        if content is None:
            return
        files_scanned += 1
        hits = _reference_hits_for_text(
            content,
            path=file_path.relative_to(candidate_run_dir).as_posix(),
            query_variants=variants,
            include_declarations=args.include_declarations,
            include_comments=args.include_comments,
            include_strings=args.include_strings,
            language=_detect_language(file_path),
            context_lines=args.context_lines,
        )
        for hit in hits:
            if len(entries) >= args.max_results:
                truncated = True
                return
            entries.append(hit)

    if root_path.is_file():
        _scan_file(root_path)
    else:
        max_depth = max(0, int(args.max_depth))
        for current_root, dirs, files in os.walk(root_path, topdown=True):
            if _timed_out() or len(entries) >= args.max_results:
                truncated = True
                break
            current_root_path = Path(current_root)
            try:
                current_depth = len(
                    [part for part in current_root_path.relative_to(root_path).parts if part != "."]
                )
            except ValueError:
                current_depth = 0
            if current_depth >= max_depth:
                dirs[:] = []
            dirs.sort()
            next_dirs: list[str] = []
            for directory in dirs:
                dir_path = current_root_path / directory
                if not is_safe_path(candidate_run_dir, dir_path):
                    continue
                if args.include_tests is False and _is_test_path(dir_path):
                    continue
                if _should_exclude(dir_path, True) or not _passes_include(dir_path, True):
                    continue
                next_dirs.append(directory)
            dirs[:] = next_dirs
            files.sort()
            for filename in files:
                if _timed_out() or len(entries) >= args.max_results:
                    truncated = True
                    break
                _scan_file(current_root_path / filename)
            if truncated:
                break

    result = {
        "symbol": args.symbol,
        "scope": args.scope,
        "kind": args.kind or "",
        "compact": args.compact,
        "truncated": truncated,
        "returned_count": len(entries[: args.max_results]),
        "max_results_used": args.max_results,
        "matches": entries[: args.max_results],
        "items": [
            {
                **_navigation_item(
                    path=str(hit.get("path") or ""),
                    name=str(hit.get("name") or ""),
                    kind=str(hit.get("kind") or ""),
                    line=int(hit.get("line") or 0) or None,
                    column=int(hit.get("column") or 0) or None,
                ),
                **(
                    {}
                    if args.compact
                    else {"language": hit.get("language"), "line_text": hit.get("line_text")}
                ),
                "excerpt": _reference_excerpt_with_line_numbers(hit)
                if args.compact
                else _reference_excerpt(hit),
            }
            for hit in entries[: args.max_results]
        ],
        "stats": {
            "files_scanned": files_scanned,
            "total_references": min(len(entries), args.max_results),
            "excluded": excluded_count,
            "excluded_patterns": list(exclude_patterns),
            "excluded_matches_by_pattern": dict(excluded_pattern_counts),
            "allowed_roots": list(allowed_root_strings()),
        },
    }
    if args.compact:
        selection = _compact_selection_item(result["items"][0] if result["items"] else None)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": _compact_navigation_result(
                    tool="find_references",
                    query=args.symbol,
                    scope={"scope": args.scope},
                    items=result["items"],
                    stats=result["stats"],
                    truncated=truncated,
                    returned_count=len(result["items"]),
                    max_results_used=args.max_results,
                    selection=selection,
                    selection_excerpt=result["items"][0]["excerpt"] if result["items"] else None,
                ),
                "meta": {"policy": policy_metadata(args.exclude_globs)},
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": result,
            "meta": {"policy": policy_metadata(args.exclude_globs)},
        },
    )


def jump_to_symbol(run_dir: Path, args: JumpToSymbolArgs, policy: dict | None = None):
    find_args = FindSymbolArgs(
        symbol_name=args.symbol,
        absolute_root=args.absolute_root,
        scope=args.scope,
        kind=args.kind,
        fuzzy=args.fuzzy,
        include_private=args.include_private,
        include_tests=args.include_tests,
        max_results=max(1, args.max_results),
        max_depth=max(0, args.max_depth),
        timeout_ms=args.timeout_ms,
        exclude_globs=args.exclude_globs,
        include_globs=args.include_globs,
    )
    find_response = find_symbol(run_dir, find_args, policy)
    payload = json.loads(find_response.body)
    if not payload.get("ok"):
        return find_response
    matches = list(payload.get("result", {}).get("matches") or [])
    if not matches:
        if args.compact:
            return JSONResponse(
                status_code=200,
                content={
                    "ok": True,
                    "result": _compact_navigation_result(
                        tool="jump_to_symbol",
                        query=args.symbol,
                        scope={"scope": args.scope},
                        items=[],
                        stats={"matches": 0, "allowed_roots": list(allowed_root_strings())},
                        truncated=False,
                        returned_count=0,
                        max_results_used=args.max_results,
                        selection=None,
                        selection_excerpt=None,
                    ),
                    "meta": {"policy": policy_metadata(args.exclude_globs)},
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {"symbol": args.symbol, "match": None, "excerpt": None, "matches": []},
                "meta": {"policy": policy_metadata(args.exclude_globs)},
            },
        )
    match = dict(matches[0])
    rel_path = str(match.get("path") or "")
    try:
        root_path, workspace_context, _ = _resolve_root(
            run_dir, args.scope or ".", args.absolute_root, policy
        )
    except ValueError:
        workspace_context = run_dir
    file_path = (
        (workspace_context / rel_path).resolve()
        if rel_path and not Path(rel_path).is_absolute()
        else Path(rel_path)
    )
    content = _read_text(file_path)
    if content is None:
        excerpt = None
        excerpt_text = ""
    else:
        line_texts, _ = _split_lines(content)
        line_number = int(match.get("line") or 1)
        line_idx = max(0, min(len(line_texts) - 1, line_number - 1))
        excerpt_start = max(0, line_idx - max(0, args.context_lines))
        excerpt_end = min(len(line_texts), line_idx + 1 + max(0, args.context_lines))
        excerpt_text = "\n".join(
            f"{index + 1}: {line_texts[index]}" for index in range(excerpt_start, excerpt_end)
        )
        excerpt = {
            "path": rel_path,
            "line": line_number,
            "column": match.get("column", 1),
            "lines": [
                {"line": index + 1, "text": line_texts[index]}
                for index in range(excerpt_start, excerpt_end)
            ],
        }
    result = {
        "symbol": args.symbol,
        "compact": args.compact,
        "match": match,
        "excerpt": excerpt,
        "returned_count": len(matches[: args.max_results]),
        "max_results_used": args.max_results,
        "matches": matches,
        "items": [
            {
                **_compact_match_item(candidate, compact=args.compact),
                "excerpt": excerpt_text if index == 0 else "",
            }
            for index, candidate in enumerate(matches[: args.max_results])
        ],
    }
    if args.compact:
        selection = _compact_selection_item(result["items"][0] if result["items"] else None)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": _compact_navigation_result(
                    tool="jump_to_symbol",
                    query=args.symbol,
                    scope={"scope": args.scope},
                    items=result["items"],
                    stats={"matches": len(matches), "allowed_roots": list(allowed_root_strings())},
                    truncated=len(matches) > args.max_results,
                    returned_count=len(result["items"]),
                    max_results_used=args.max_results,
                    selection=selection,
                    selection_excerpt=excerpt_text or None,
                ),
                "meta": {"policy": policy_metadata(args.exclude_globs)},
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": result,
            "meta": {"policy": policy_metadata(args.exclude_globs)},
        },
    )
