from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent
_WINDOWS = os.name == "nt"

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


def _resolve_python_interpreter() -> tuple[str, str]:
    configured = _env_value("TOOLRUNNER_PYTHON").strip()
    if configured:
        expanded = _expand_path(configured)
        candidate = Path(expanded)
        if candidate.is_absolute():
            return str(candidate.resolve()), "env:TOOLRUNNER_PYTHON"
        if any(sep in configured for sep in ("/", "\\")):
            resolved = (BASE_DIR / candidate).resolve()
            return str(resolved), "env:TOOLRUNNER_PYTHON"
        return configured, "env:TOOLRUNNER_PYTHON"

    candidate_paths = []
    if _WINDOWS:
        candidate_paths.extend(
            [
                BASE_DIR / ".venv" / "Scripts" / "python.exe",
                BASE_DIR.parent / ".venv" / "Scripts" / "python.exe",
            ]
        )
    else:
        candidate_paths.extend(
            [
                BASE_DIR / ".venv" / "bin" / "python",
                BASE_DIR.parent / ".venv" / "bin" / "python",
            ]
        )
    for candidate_path in candidate_paths:
        if candidate_path.exists():
            label = (
                "fallback:toolrunner_.venv"
                if candidate_path.parent.parent.parent == BASE_DIR
                else "fallback:repo_.venv"
            )
            return str(candidate_path.resolve()), label
    return ("python", "default:python")


SECRET = _env_value("TOOLRUNNER_SECRET") or "insecure-secret"
SECRET = SECRET.encode("utf-8")

_SANDBOX_ROOT_RAW = _env_value("TOOLRUNNER_SANDBOX_ROOT")
if _SANDBOX_ROOT_RAW:
    _SANDBOX_ROOT_PATH = Path(_expand_path(_SANDBOX_ROOT_RAW))
else:
    _SANDBOX_ROOT_PATH = Path(BASE_DIR, "sandbox")

SANDBOX_ROOT = _SANDBOX_ROOT_PATH.resolve()
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
TIMESTAMP_SKEW_SECONDS = int(_env_value("TOOLRUNNER_TIMESTAMP_SKEW_SECONDS") or "60")

COMMAND_TIMEOUT = int(_env_value("TOOLRUNNER_TIMEOUT") or "15")
OUTPUT_LIMIT = int(_env_value("TOOLRUNNER_OUTPUT_LIMIT") or "4096")
SAFE_COMMAND_TIMEOUT = int(_env_value("TOOLRUNNER_SAFE_COMMAND_TIMEOUT_SECONDS") or "60")
SAFE_COMMAND_OUTPUT_LIMIT = int(_env_value("TOOLRUNNER_SAFE_COMMAND_OUTPUT_LIMIT") or "262144")
RUN_TESTS_TIMEOUT = int(_env_value("TOOLRUNNER_RUN_TESTS_TIMEOUT_SECONDS") or "900")
RUN_TESTS_OUTPUT_LIMIT = int(_env_value("TOOLRUNNER_RUN_TESTS_OUTPUT_LIMIT") or "262144")
WEB_SEARCH_PROVIDER = _env_value("TOOLRUNNER_WEB_SEARCH_PROVIDER") or "brave"
BRAVE_SEARCH_API_KEY = _env_value("BRAVE_SEARCH_API_KEY")
WEB_SEARCH_TIMEOUT_SECONDS = int(_env_value("TOOLRUNNER_WEB_SEARCH_TIMEOUT_SECONDS") or "10")
WEB_FETCH_TIMEOUT_SECONDS = int(_env_value("TOOLRUNNER_FETCH_TIMEOUT_SECONDS") or "10")
WEB_FETCH_MAX_BYTES = int(_env_value("TOOLRUNNER_FETCH_MAX_BYTES") or "1048576")
ALLOWED_COMMANDS = [
    part.strip()
    for part in (_env_value("TOOLRUNNER_ALLOWED_COMMANDS") or "pytest,python,ruff,black,git,ls,cat,powershell").split(",")
    if part.strip()
]
PYTHON_INTERPRETER, PYTHON_INTERPRETER_SOURCE = _resolve_python_interpreter()


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


def policy_allowed_roots(policy: dict[str, Any] | None) -> tuple[Path, ...]:
    if not policy:
        return ()
    raw_roots = policy.get("allowed_roots")
    if not isinstance(raw_roots, (list, tuple, set)):
        return ()
    resolved: list[Path] = []
    seen: set[Path] = set()
    for value in raw_roots:
        candidate_text = str(value or "").strip()
        if not candidate_text:
            continue
        candidate = Path(candidate_text)
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        normalized = _normalize_for_allowlist(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return tuple(resolved)


def policy_runtime_root(policy: dict[str, Any] | None, key: str) -> Path | None:
    if not policy:
        return None
    raw = str(policy.get(key) or "").strip()
    if not raw:
        return None
    try:
        return normalize_search_root(Path(raw))
    except Exception:
        return None


def policy_resolution_roots(run_dir: Path, policy: dict[str, Any] | None = None) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for root in (
        policy_runtime_root(policy, "repo_root"),
        policy_runtime_root(policy, "tmp_root"),
        normalize_search_root(run_dir),
        *policy_allowed_roots(policy),
    ):
        if root is None:
            continue
        normalized = normalize_search_root(root)
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append(normalized)
    return tuple(roots)


def _format_path_resolution_error(
    *,
    requested_path: str,
    reason: str,
    resolved_path: Path | None = None,
    workspace_root: Path | None = None,
    policy_roots: Sequence[Path] | None = None,
) -> str:
    roots = policy_roots or ()
    root_text = ", ".join(os.path.normpath(str(root)) for root in roots) if roots else "none"
    parts = [reason, f"requested={requested_path!r}"]
    if resolved_path is not None:
        parts.append(f"resolved={resolved_path}")
    if workspace_root is not None:
        parts.append(f"workspace_root={workspace_root}")
    parts.append(f"allowed_roots={root_text}")
    return "; ".join(parts)


def resolve_policy_path(run_dir: Path, raw_path: str, policy: dict[str, Any] | None = None) -> Path:
    requested = Path(str(raw_path or ".").strip() or ".")
    policy_roots = policy_resolution_roots(run_dir, policy)
    if requested.is_absolute():
        resolved = requested.resolve()
        if not is_under_allowed_root(resolved, policy_roots):
            raise ValueError(
                _format_path_resolution_error(
                    requested_path=str(raw_path or "."),
                    reason="absolute path is outside allowed roots",
                    resolved_path=resolved,
                    workspace_root=run_dir.resolve(),
                    policy_roots=policy_roots,
                )
            )
        return resolved
    base_root = policy_runtime_root(policy, "repo_root") or run_dir.resolve()
    base_root = Path(base_root)
    base_resolved = base_root.resolve()
    resolved = (base_root / requested).resolve()
    try:
        if resolved != base_resolved and not resolved.is_relative_to(base_resolved):
            raise ValueError(
                _format_path_resolution_error(
                    requested_path=str(raw_path or "."),
                    reason="path traversal outside of workspace",
                    resolved_path=resolved,
                    workspace_root=base_resolved,
                    policy_roots=policy_roots,
                )
            )
    except ValueError as exc:
        raise ValueError(
            _format_path_resolution_error(
                requested_path=str(raw_path or "."),
                reason="path traversal outside of workspace",
                resolved_path=resolved,
                workspace_root=base_resolved,
                policy_roots=policy_roots,
            )
        ) from exc
    if not is_under_allowed_root(resolved, policy_roots):
        raise ValueError(
            _format_path_resolution_error(
                requested_path=str(raw_path or "."),
                reason="resolved path is outside allowed roots",
                resolved_path=resolved,
                workspace_root=base_resolved,
                policy_roots=policy_roots,
            )
        )
    return resolved


def resolve_path_from_base(base_dir: Path, raw_path: str, policy: dict[str, Any] | None = None) -> Path:
    requested = Path(str(raw_path or ".").strip() or ".")
    policy_roots = policy_resolution_roots(base_dir, policy)
    if requested.is_absolute():
        resolved = requested.resolve()
        if not is_under_allowed_root(resolved, policy_roots):
            raise ValueError(
                _format_path_resolution_error(
                    requested_path=str(raw_path or "."),
                    reason="absolute path is outside allowed roots",
                    resolved_path=resolved,
                    workspace_root=base_dir.resolve(),
                    policy_roots=policy_roots,
                )
            )
        return resolved
    base_resolved = base_dir.resolve()
    resolved = (base_resolved / requested).resolve()
    try:
        if resolved != base_resolved and not resolved.is_relative_to(base_resolved):
            raise ValueError(
                _format_path_resolution_error(
                    requested_path=str(raw_path or "."),
                    reason="path traversal outside of workspace",
                    resolved_path=resolved,
                    workspace_root=base_resolved,
                    policy_roots=policy_roots,
                )
            )
    except ValueError as exc:
        raise ValueError(
            _format_path_resolution_error(
                requested_path=str(raw_path or "."),
                reason="path traversal outside of workspace",
                resolved_path=resolved,
                workspace_root=base_resolved,
                policy_roots=policy_roots,
            )
        ) from exc
    if not is_under_allowed_root(resolved, policy_roots):
        raise ValueError(
            _format_path_resolution_error(
                requested_path=str(raw_path or "."),
                reason="resolved path is outside allowed roots",
                resolved_path=resolved,
                workspace_root=base_resolved,
                policy_roots=policy_roots,
            )
        )
    return resolved


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
