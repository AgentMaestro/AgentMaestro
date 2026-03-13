from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi.responses import JSONResponse

import pypatch.patch as patch_parser

from ..config import (
    EXCLUDE_FROM_SEARCH_LIST,
    is_under_allowed_root,
    is_within_sandbox,
    normalize_search_root,
    policy_allowed_roots,
    policy_allows_write,
    policy_metadata,
    policy_runtime_root,
    resolve_policy_path,
)
from ..models import FilePatchArgs
from .path_filters import first_matching_pattern, glob_candidates

BACKUP_DIR = ".toolrunner_backups"
REJECT_DIR = ".toolrunner_rejects"
_EXPLICIT_HUNK_HEADER_RE = re.compile(r"^@@ -\d+,\d+ \+\d+,\d+ @@(?: .*)?$")


class PatchApplicationError(Exception):
    """Raised when a single patch hunk cannot be applied."""


@dataclass
class PatchHunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[str]


FILE_PATCH_POLICY_META = policy_metadata()


def _error(code: str, message: str, details: dict | None = None, status: int = 400):
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "error": {
                "code": f"tool_runner.{code}",
                "message": message,
                "details": details or {},
            },
            "meta": {"policy": FILE_PATCH_POLICY_META},
        },
    )


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _ensure_backup(target: Path, run_dir: Path) -> Path:
    backup_dir = run_dir / BACKUP_DIR / _storage_subdir(run_dir, target)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{target.name}.{ts}.bak"
    shutil.copy2(target, backup_path)
    return backup_path


def _write_rejects(run_dir: Path, target: Path, patch_text: str) -> Path:
    rejects_dir = run_dir / REJECT_DIR / _storage_subdir(run_dir, target)
    rejects_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rejects_path = rejects_dir / f"{target.name}.{ts}.rej"
    rejects_path.write_text(patch_text)
    return rejects_path


def _storage_subdir(run_dir: Path, target: Path) -> Path:
    try:
        return target.parent.relative_to(run_dir)
    except ValueError:
        drive = target.drive.rstrip(":\\/")
        parts = [part for part in target.parent.parts if part not in {target.anchor, "\\", "/"}]
        if drive:
            return Path("_external", drive, *parts)
        return Path("_external", *parts)


def _ensure_diff_header(patch_text: str, path: str) -> str:
    normalized = path.replace("\\", "/")
    lines = patch_text.splitlines()
    has_diff = any(line.startswith("diff ") for line in lines)
    has_from = any(line.startswith("--- ") for line in lines)
    has_to = any(line.startswith("+++ ") for line in lines)
    header_lines: list[str] = []
    if not has_diff:
        header_lines.append(f"diff --git a/{normalized} b/{normalized}")
    if not has_from:
        header_lines.append(f"--- a/{normalized}")
    if not has_to:
        header_lines.append(f"+++ b/{normalized}")
    if not header_lines:
        return patch_text
    prefix = "\n".join(header_lines)
    suffix = "\n".join(lines)
    return f"{prefix}\n{suffix}" if suffix else f"{prefix}\n"


def _split_path_suffix(value: str) -> tuple[str, str]:
    if "\t" in value:
        head, tail = value.split("\t", 1)
        return head, "\t" + tail
    return value, ""


def _split_path_parts(path: str) -> list[str]:
    normalized = path.replace("\\", "/")
    return [
        part
        for part in PurePosixPath(normalized).parts
        if part and part != "."
    ]


def _strip_path_components(path: str, strip_prefix: int) -> str:
    if strip_prefix <= 0 or not path:
        return path
    normalized = path.replace("\\", "/")
    if normalized == "/dev/null":
        return path
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return path
    if strip_prefix >= len(parts):
        return parts[-1]
    return "/".join(parts[strip_prefix:])


def _rewrite_diff_line(line: str, strip_prefix: int) -> str:
    if strip_prefix <= 0 or not line.startswith("diff --git "):
        return line
    parts = line.split()
    if len(parts) < 4 or parts[0] != "diff" or parts[1] != "--git":
        return line
    parts[2] = _strip_path_components(parts[2], strip_prefix)
    parts[3] = _strip_path_components(parts[3], strip_prefix)
    return " ".join(parts)


def _rewrite_patch_paths(patch_text: str, strip_prefix: int) -> str:
    if strip_prefix <= 0:
        return patch_text
    rewritten: list[str] = []
    for line in patch_text.splitlines(keepends=True):
        newline = ""
        content = line
        if content.endswith("\r\n"):
            newline = "\r\n"
            content = content[:-2]
        elif content.endswith("\n"):
            newline = "\n"
            content = content[:-1]
        elif content.endswith("\r"):
            newline = "\r"
            content = content[:-1]
        if content.startswith("diff --git "):
            content = _rewrite_diff_line(content, strip_prefix)
        elif content.startswith("--- ") or content.startswith("+++ "):
            prefix = content[:4]
            remainder = content[4:]
            remainder_path, suffix = _split_path_suffix(remainder)
            stripped_path = _strip_path_components(remainder_path, strip_prefix)
            content = f"{prefix}{stripped_path}{suffix}"
        rewritten.append(content + newline)
    return "".join(rewritten)


def _detect_strip_prefix(target_path: str, patch_text: str) -> int:
    target_parts = _split_path_parts(target_path)
    if not target_parts:
        return 0
    for line in patch_text.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        candidate, _ = _split_path_suffix(line[4:])
        candidate = candidate.strip()
        if not candidate or candidate == "/dev/null":
            continue
        candidate_parts = _split_path_parts(candidate)
        if len(candidate_parts) >= len(target_parts) and candidate_parts[-len(target_parts) :] == target_parts:
            prefix = len(candidate_parts) - len(target_parts)
            if prefix > 0:
                return prefix
            return 0
        if len(candidate_parts) < len(target_parts) and target_parts[-len(candidate_parts) :] == candidate_parts:
            return 0
    return 0


def _normalize_path_for_patch(path: str) -> str:
    candidate = PurePosixPath(path.replace("\\", "/"))
    return candidate.as_posix()


def _paths_match(candidate_path: str, target_path: str) -> bool:
    normalized_candidate = _normalize_path_for_patch(candidate_path)
    normalized_target = _normalize_path_for_patch(target_path)
    if normalized_candidate == normalized_target:
        return True
    candidate_parts = _split_path_parts(normalized_candidate)
    target_parts = _split_path_parts(normalized_target)
    if not candidate_parts or not target_parts:
        return False
    if len(candidate_parts) >= len(target_parts):
        return candidate_parts[-len(target_parts) :] == target_parts
    return target_parts[-len(candidate_parts) :] == candidate_parts


def _parse_patch_hunks(patch_text: str, path: str) -> list[PatchHunk]:
    patchset = patch_parser.fromstring(patch_text)
    if not patchset:
        raise ValueError("patch could not be parsed")
    if not patchset.items:
        raise ValueError("patch does not contain any files")
    hunks: list[PatchHunk] = []
    filtered_items = [
        item
        for item in patchset.items
        if item.target and _paths_match(item.target, path)
    ]
    if not filtered_items:
        raise ValueError("patch does not contain hunks for the requested file")
    for item in filtered_items:
        for hunk in item.hunks:
            lines = [line for line in hunk.text]
            old_len = hunk.linessrc or sum(1 for line in lines if line.startswith(" ") or line.startswith("-"))
            new_len = hunk.linestgt or sum(1 for line in lines if line.startswith(" ") or line.startswith("+"))
            hunks.append(
                PatchHunk(
                    hunk.startsrc or 1,
                    old_len,
                    hunk.starttgt or 1,
                    new_len,
                    lines,
                )
            )
    return hunks


def _validate_hunk_headers(patch_text: str) -> str | None:
    saw_header = False
    for raw_line in patch_text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.startswith("@@"):
            continue
        saw_header = True
        if _EXPLICIT_HUNK_HEADER_RE.match(line):
            continue
        return (
            "invalid hunk header; use explicit ranges like "
            "`@@ -1,3 +1,4 @@` or `@@ -0,0 +1,2 @@`"
        )
    if not saw_header:
        return "patch does not contain any @@ hunk headers"
    return None


def _apply_hunk(lines: list[str], hunk: PatchHunk, offset: int) -> tuple[list[str], int]:
    start = hunk.old_start - 1 + offset
    if start < 0 or start > len(lines):
        raise PatchApplicationError("hunk start is outside the file")
    scan_idx = start
    result_lines: list[str] = []
    for patch_line in hunk.lines:
        if not patch_line:
            continue
        if patch_line.startswith("\\"):
            # metadata such as \"\\ No newline at end of file\" – ignore.
            continue
        prefix = patch_line[0]
        body = patch_line[1:]
        body_content = body.rstrip("\r\n")
        if prefix == " ":
            if scan_idx >= len(lines):
                raise PatchApplicationError("context mismatch for hunk")
            line_value = lines[scan_idx]
            if line_value.rstrip("\r\n") != body_content:
                raise PatchApplicationError("context mismatch for hunk")
            result_lines.append(line_value)
            scan_idx += 1
        elif prefix == "-":
            if scan_idx >= len(lines):
                raise PatchApplicationError("removal did not match file")
            line_value = lines[scan_idx]
            if line_value.rstrip("\r\n") != body_content:
                raise PatchApplicationError("removal did not match file")
            scan_idx += 1
        elif prefix == "+":
            result_lines.append(body)
        else:
            raise PatchApplicationError("unexpected patch line prefix")
    consumed = scan_idx - start
    if consumed != hunk.old_len:
        raise PatchApplicationError("hunk consumed unexpected number of lines")
    new_lines = lines[:start] + result_lines + lines[scan_idx:]
    delta = len(result_lines) - hunk.old_len
    return new_lines, delta


def apply_patch(run_dir: Path, args: FilePatchArgs, policy: dict[str, Any] | None = None):
    base_dir = policy_runtime_root(policy, "repo_root") or run_dir.resolve()
    allowed_context_root = normalize_search_root(base_dir)
    policy_roots = policy_allowed_roots(policy)
    if not is_under_allowed_root(run_dir):
        return _error(
            "PATH_NOT_ALLOWED",
            "Run directory not permitted",
        )
    try:
        run_dir = run_dir.resolve()
        target = resolve_policy_path(run_dir, args.path, policy)
    except ValueError as exc:
        error_code = "PATH_OUTSIDE_WORKSPACE" if "path traversal outside of workspace" in str(exc) else "PATH_NOT_ALLOWED"
        return _error(error_code, str(exc))

    if not is_under_allowed_root(target, (allowed_context_root, *policy_roots)):
        return _error("PATH_NOT_ALLOWED", "Target path not permitted")

    exclusion = _matching_exclusion(target, run_dir)
    if exclusion:
        return _error("PATH_EXCLUDED", "Path excluded by policy", {"pattern": exclusion})

    if (
        not is_within_sandbox(target)
        and not is_under_allowed_root(target, policy_roots)
        and not policy_allows_write(policy)
    ):
        return _error(
            "WRITE_NOT_PERMITTED",
            "Writes outside the sandbox require allow_write policy",
        )

    if not target.exists():
        if not args.create_if_missing:
            return _error("NOT_FOUND", "Target file missing")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")

    if args.expected_sha256:
        current_sha = _sha256(target)
        if current_sha != args.expected_sha256:
            return _error("CONFLICT", "checksum mismatch")

    sha_before = _sha256(target)
    backup_path: Path | None = None
    if args.backup:
        backup_path = _ensure_backup(target, run_dir)

    original_patch = args.patch_unified
    patch_text = _ensure_diff_header(original_patch, args.path)
    strip_prefix = args.strip_prefix
    if strip_prefix == 0:
        strip_prefix = _detect_strip_prefix(args.path, patch_text)
    if strip_prefix > 0:
        patch_text = _rewrite_patch_paths(patch_text, strip_prefix)
    header_error = _validate_hunk_headers(patch_text)
    if header_error:
        return _error("PATCH_FAILED", header_error)
    try:
        hunks = _parse_patch_hunks(patch_text, args.path)
    except ValueError as exc:
        return _error("PATCH_FAILED", str(exc))

    working_lines = target.read_text().splitlines(keepends=True)
    failed_hunks: list[int] = []
    rejects_path: Path | None = None
    offset = 0

    applied_hunks = 0
    stop_processing = False

    for idx, hunk in enumerate(hunks, start=1):
        if stop_processing:
            break
        try:
            working_lines, delta = _apply_hunk(working_lines, hunk, offset)
        except PatchApplicationError:
            failed_hunks.append(idx)
            if rejects_path is None:
                rejects_path = _write_rejects(run_dir, target, original_patch)
            if args.fail_on_reject:
                stop_processing = True
            continue
        applied_hunks += 1
        offset += delta

    if failed_hunks and args.fail_on_reject:
        details = {
            "hunks_total": len(hunks),
            "hunks_applied": applied_hunks,
            "failed_hunks": failed_hunks,
            "rejects_path": str(rejects_path) if rejects_path else None,
        }
        return _error("PATCH_FAILED", "hunk(s) failed", details)

    if failed_hunks and rejects_path is None:
        rejects_path = _write_rejects(run_dir, target, original_patch)

    target.write_text("".join(working_lines))
    sha_after = _sha256(target)

    applied = not failed_hunks
    applied_partially = bool(failed_hunks) and not args.fail_on_reject

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "path": args.path,
                "applied": applied,
                "applied_partially": applied_partially,
                "hunks_total": len(hunks),
                "hunks_applied": applied_hunks,
                "hunks_failed": len(failed_hunks),
                "failed_hunks": failed_hunks,
                "sha256_before": sha_before,
                "sha256_after": sha_after,
                "backup_path": str(backup_path) if backup_path else None,
                "rejects_path": str(rejects_path) if rejects_path else None,
            },
            "meta": {"policy": FILE_PATCH_POLICY_META},
        },
    )


def _matching_exclusion(target: Path, run_dir: Path) -> str | None:
    if not EXCLUDE_FROM_SEARCH_LIST:
        return None
    candidates = glob_candidates(target, run_dir, run_dir, is_dir=target.is_dir())
    return first_matching_pattern(candidates, EXCLUDE_FROM_SEARCH_LIST)
