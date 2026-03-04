from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable, Sequence


def glob_candidates(
    entry_path: Path,
    root_path: Path,
    run_dir: Path,
    is_dir: bool = False,
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


def matches_patterns(candidates: Iterable[str], patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        for candidate in candidates:
            for candidate_variant in _candidate_variants(candidate):
                for variant in _pattern_variants(pattern):
                    if fnmatch.fnmatchcase(candidate_variant, variant):
                        return True
    return False


def first_matching_pattern(candidates: Iterable[str], patterns: Sequence[str]) -> str | None:
    for pattern in patterns:
        for candidate in candidates:
            for candidate_variant in _candidate_variants(candidate):
                for variant in _pattern_variants(pattern):
                    if fnmatch.fnmatchcase(candidate_variant, variant):
                        return pattern
    return None


def _pattern_variants(pattern: str) -> Iterable[str]:
    yield pattern
    if pattern.startswith("**/"):
        remainder = pattern[3:]
        if remainder:
            yield remainder


def _candidate_variants(candidate: str) -> Iterable[str]:
    if not candidate:
        return
    variants: list[str] = [candidate]
    if candidate.startswith("./"):
        remainder = candidate[2:]
        if remainder:
            variants.append(remainder)
    else:
        variants.append(f"./{candidate}")
    seen: set[str] = set()
    for variant in variants:
        if variant and variant not in seen:
            seen.add(variant)
            yield variant
