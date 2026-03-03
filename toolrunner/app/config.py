from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent

_ENV_PATH = BASE_DIR / ".env"


def _load_env_file() -> dict[str, str]:
    if not _ENV_PATH.exists():
        return {}
    data: dict[str, str] = {}
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        maybe = line.split("#", 1)[0].strip()
        if not maybe:
            continue
        if "=" not in maybe:
            continue
        key, _, value = maybe.partition("=")
        data[key.strip()] = value.strip()
    return data


_ENV_FILE_VALUES = _load_env_file()


def _env_value(env_key: str) -> str:
    explicit = os.environ.get(env_key)
    if explicit is not None:
        return explicit
    return _ENV_FILE_VALUES.get(env_key, "")


def _split_env_list(env_key: str) -> list[str]:
    raw = _env_value(env_key)
    if not raw:
        return []
    values = []
    for part in re.split(r"[,\n]+", raw):
        candidate = part.strip()
        if candidate:
            values.append(candidate)
    return values


def _expand_path(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value.strip()))


SECRET = os.environ.get("TOOLRUNNER_SECRET", "insecure-secret").encode("utf-8")

_SANDBOX_ROOT_RAW = _env_value("TOOLRUNNER_SANDBOX_ROOT")
if _SANDBOX_ROOT_RAW:
    _SANDBOX_ROOT_PATH = Path(_expand_path(_SANDBOX_ROOT_RAW))
else:
    _SANDBOX_ROOT_PATH = Path(BASE_DIR, "sandbox")

SANDBOX_ROOT = _SANDBOX_ROOT_PATH.resolve()
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
TIMESTAMP_SKEW_SECONDS = int(os.environ.get("TOOLRUNNER_TIMESTAMP_SKEW_SECONDS", "60"))

COMMAND_TIMEOUT = int(os.environ.get("TOOLRUNNER_COMMAND_TIMEOUT", "30"))
OUTPUT_LIMIT = int(os.environ.get("TOOLRUNNER_OUTPUT_LIMIT", "4096"))
ALLOWED_COMMANDS = [
    part.strip()
    for part in os.environ.get(
        "TOOLRUNNER_ALLOWED_COMMANDS", "pytest,python,ruff,black,git,ls,cat,powershell"
    ).split(",")
    if part.strip()
]
PYTHON_INTERPRETER = os.environ.get("TOOLRUNNER_PYTHON", "python")


_WINDOWS = os.name == "nt"


def _normalize_for_allowlist(path: Path) -> Path:
    resolved = path.resolve()
    as_str = resolved.as_posix()
    if _WINDOWS:
        as_str = as_str.lower()
    return Path(as_str)


def normalize_globs(patterns: Sequence[str] | None) -> tuple[str, ...]:
    if not patterns:
        return ()
    normalized: list[str] = []
    for pattern in patterns:
        candidate = pattern.strip()
        if not candidate:
            continue
        normalized.append(candidate.replace("\\", "/"))
    return tuple(normalized)


def _resolve_paths(values: list[str]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        normalized = _normalize_for_allowlist(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return tuple(resolved)


def normalize_search_root(path: Path) -> Path:
    return _normalize_for_allowlist(path)


_NORMALIZED_SANDBOX_ROOT = _normalize_for_allowlist(SANDBOX_ROOT)


def is_under_allowed_root(path: Path, extra_roots: Sequence[Path] | None = None) -> bool:
    normalized = normalize_search_root(path)
    candidates: list[Path] = list(ALLOW_TO_SEARCH_LIST)
    if extra_roots:
        for extra in extra_roots:
            candidates.append(normalize_search_root(extra))
    for allowed_root in candidates:
        try:
            if normalized == allowed_root or normalized.is_relative_to(allowed_root):
                return True
        except ValueError:
            continue
    return False


def is_within_sandbox(path: Path) -> bool:
    normalized = normalize_search_root(path)
    try:
        return normalized == _NORMALIZED_SANDBOX_ROOT or normalized.is_relative_to(_NORMALIZED_SANDBOX_ROOT)
    except ValueError:
        return False


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    try:
        return float(text) != 0
    except ValueError:
        return True


def policy_allows_write(policy: dict[str, Any] | None) -> bool:
    if not policy:
        return False
    return _truthy_flag(policy.get("allow_write"))


DEFAULT_SEARCH_EXCLUDE_GLOBS = (
    "**/.git/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/media/**",
    "**/staticfiles/**",
    "**/*.pyc",
    "**/*.sqlite3",
    ".env",
)

ALLOW_TO_SEARCH_LIST = _resolve_paths(_split_env_list("TOOLRUNNER_ALLOW_TO_SEARCH_LIST"))
_SANDBOX_PARENT_NORMALIZED = _normalize_for_allowlist(SANDBOX_ROOT.parent)
if _SANDBOX_PARENT_NORMALIZED not in ALLOW_TO_SEARCH_LIST:
    ALLOW_TO_SEARCH_LIST = ALLOW_TO_SEARCH_LIST + (_SANDBOX_PARENT_NORMALIZED,)
EXCLUDE_FROM_SEARCH_LIST = normalize_globs(
    _split_env_list("TOOLRUNNER_EXCLUDE_FROM_SEARCH_LIST") or list(DEFAULT_SEARCH_EXCLUDE_GLOBS)
)

def combine_exclude_patterns(*extra_patterns: Sequence[str] | None) -> tuple[str, ...]:
    combined: list[str] = list(EXCLUDE_FROM_SEARCH_LIST)
    for patterns in extra_patterns:
        combined.extend(normalize_globs(patterns))
    seen: set[str] = set()
    unique: list[str] = []
    for pattern in combined:
        if pattern in seen:
            continue
        seen.add(pattern)
        unique.append(pattern)
    return tuple(unique)

def allowed_root_strings() -> tuple[str, ...]:
    return tuple(os.path.normpath(str(root)) for root in ALLOW_TO_SEARCH_LIST)


def policy_metadata(*extra_patterns: Sequence[str] | None) -> dict[str, list[str]]:
    combined_patterns = combine_exclude_patterns(*extra_patterns)
    return {
        "allowed_roots": list(allowed_root_strings()),
        "excluded_patterns": list(combined_patterns),
        "exclusion_priority": "exclude over allow",
    }
