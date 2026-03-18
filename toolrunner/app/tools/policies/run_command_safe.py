from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ...config import resolve_path_from_base, resolve_policy_path

_ALLOWED_EXECUTABLES = {"python", "pytest", "ruff", "mypy", "uv", "django-admin"}
_EXPLICIT_GIT_MESSAGE = "git commands are not allowed via run_command_safe. Use git_* tools instead."
_SHELL_TOKENS = ("&&", "||", ";", "|", ">>", ">", "<")
_SENSITIVE_ARG_NAMES = {
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "api-key",
    "api_key",
    "access-key",
    "access_key",
}
_BANNED_ARG_TOKENS = {
    "runserver",
    "uvicorn",
    "gunicorn",
    "hypercorn",
    "http.server",
    "serve",
    "server",
    "watch",
    "shell",
    "dbshell",
    "migrate",
    "makemigrations",
    "createsuperuser",
    "collectstatic",
    "startapp",
    "startproject",
    "pip",
    "poetry",
    "npm",
    "pnpm",
    "yarn",
    "docker",
    "compose",
    "kubectl",
    "install",
    "add",
    "remove",
    "uninstall",
    "delete",
    "del",
    "rm",
    "move",
    "mv",
    "rename",
}


@dataclass(slots=True)
class SafeCommandDecision:
    allowed: bool
    normalized_command: list[str]
    redacted_command: list[str]
    resolved_cwd: Path | None
    workspace_root: Path | None
    policy_reason: str | None = None


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for raw in argv:
        value = str(raw or "").strip()
        if not value:
            raise ValueError("argv must not contain empty command elements")
        normalized.append(value)
    if not normalized:
        raise ValueError("argv must include at least one element")
    return normalized


def _workspace_root(run_dir: Path, policy: dict | None) -> Path:
    repo_root = str((policy or {}).get("repo_root") or "").strip()
    if repo_root:
        return Path(repo_root).resolve()
    return run_dir.resolve()


def _within_root(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except ValueError:
        return False


def _normalize_executable_name(value: str) -> str:
    lowered = value.lower()
    if lowered.endswith(".exe"):
        lowered = lowered[:-4]
    return lowered


def _contains_shell_composition(argv: Sequence[str]) -> bool:
    for arg in argv:
        lowered = arg.lower()
        if any(token in arg for token in _SHELL_TOKENS):
            return True
        if lowered.startswith("http://") or lowered.startswith("https://"):
            return True
    return False


def _contains_wrapper(argv: Sequence[str]) -> bool:
    if len(argv) < 2:
        return False
    executable = _normalize_executable_name(argv[0])
    switch = argv[1].lower()
    if executable == "cmd" and switch == "/c":
        return True
    if executable in {"powershell", "pwsh"} and switch == "-command":
        return True
    if executable == "bash" and switch == "-c":
        return True
    return False


def _redact_command(argv: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for arg in argv:
        lowered = arg.lower()
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        if "=" in arg:
            name, _, _ = arg.partition("=")
            normalized_name = name.lstrip("-/").lower()
            if normalized_name in _SENSITIVE_ARG_NAMES or any(token in normalized_name for token in _SENSITIVE_ARG_NAMES):
                redacted.append(f"{name}=***")
                continue
        normalized = lowered.lstrip("-/")
        if normalized in _SENSITIVE_ARG_NAMES:
            redacted.append(arg)
            hide_next = True
            continue
        redacted.append(arg)
    return redacted


def _reject(decision: SafeCommandDecision, reason: str) -> SafeCommandDecision:
    decision.allowed = False
    decision.policy_reason = reason
    return decision


def _has_banned_tokens(args: Sequence[str]) -> bool:
    for arg in args:
        lowered = arg.lower()
        normalized = lowered.lstrip("-/")
        if normalized in _BANNED_ARG_TOKENS:
            return True
    return False


def _validate_pytest_args(args: Sequence[str]) -> str | None:
    for arg in args:
        lowered = arg.lower()
        if lowered in {"--pdb"}:
            return f"pytest argument '{arg}' is not allowed via run_command_safe"
    return None


def _validate_ruff_args(args: Sequence[str]) -> str | None:
    subcommand = None
    for arg in args:
        if arg.startswith("-"):
            continue
        subcommand = arg.lower()
        break
    if subcommand not in {"check", "format"}:
        return "ruff only supports 'check' and 'format' via run_command_safe"
    if any(arg.lower() == "--watch" for arg in args):
        return "watch mode is not allowed via run_command_safe"
    return None


def _validate_mypy_args(args: Sequence[str]) -> str | None:
    if any(arg.lower() == "--install-types" for arg in args):
        return "mypy install operations are not allowed via run_command_safe"
    return None


def _validate_django_admin_args(args: Sequence[str]) -> str | None:
    if not args or args[0].lower() != "check":
        return "django-admin only supports 'check' via run_command_safe"
    return None


def _validate_python_args(
    args: Sequence[str],
    *,
    resolved_cwd: Path,
    workspace_root: Path,
    policy: dict | None,
) -> str | None:
    if not args:
        return "python requires a module or script via run_command_safe"
    first = args[0].lower()
    if first in {"-c", "-i"}:
        return f"python flag '{args[0]}' is not allowed via run_command_safe"
    if first == "-m":
        if len(args) < 2:
            return "python -m requires a module name"
        module = args[1].lower()
        if module == "pytest":
            return _validate_pytest_args(args[2:])
        if module == "ruff":
            return _validate_ruff_args(args[2:])
        if module == "mypy":
            return _validate_mypy_args(args[2:])
        return f"python -m {module} is not allowed via run_command_safe"

    try:
        script_path = resolve_path_from_base(resolved_cwd, args[0], policy)
    except ValueError as exc:
        return str(exc)
    if not _within_root(script_path, workspace_root):
        return "python scripts must stay inside the workspace root"
    if script_path.suffix.lower() != ".py":
        return "python only allows .py scripts via run_command_safe"
    if script_path.name.lower() == "manage.py":
        if len(args) < 2 or args[1].lower() != "check":
            return "python manage.py only supports 'check' via run_command_safe"
        return None
    if _has_banned_tokens(args[1:]):
        return "python script arguments include a disallowed operation"
    return None


def _validate_uv_args(
    args: Sequence[str],
    *,
    resolved_cwd: Path,
    workspace_root: Path,
    policy: dict | None,
) -> str | None:
    if not args or args[0].lower() != "run":
        return "uv only supports 'uv run ...' via run_command_safe"
    nested = list(args[1:])
    if nested and nested[0] == "--":
        nested = nested[1:]
    if not nested:
        return "uv run requires a nested command"
    nested_decision = evaluate_run_command_safe(
        run_dir=workspace_root,
        argv=nested,
        cwd=str(resolved_cwd),
        policy=policy,
    )
    if not nested_decision.allowed:
        return nested_decision.policy_reason
    return None


def _validate_allowed_command(
    executable: str,
    argv: Sequence[str],
    *,
    resolved_cwd: Path,
    workspace_root: Path,
    policy: dict | None,
) -> str | None:
    args = list(argv[1:])
    if executable == "pytest":
        return _validate_pytest_args(args)
    if executable == "ruff":
        return _validate_ruff_args(args)
    if executable == "mypy":
        return _validate_mypy_args(args)
    if executable == "django-admin":
        return _validate_django_admin_args(args)
    if executable == "python":
        return _validate_python_args(
            args,
            resolved_cwd=resolved_cwd,
            workspace_root=workspace_root,
            policy=policy,
        )
    if executable == "uv":
        return _validate_uv_args(
            args,
            resolved_cwd=resolved_cwd,
            workspace_root=workspace_root,
            policy=policy,
        )
    return None


def evaluate_run_command_safe(
    *,
    run_dir: Path,
    argv: Sequence[str],
    cwd: str,
    policy: dict | None = None,
) -> SafeCommandDecision:
    normalized_command = _normalize_argv(argv)
    decision = SafeCommandDecision(
        allowed=True,
        normalized_command=normalized_command,
        redacted_command=_redact_command(normalized_command),
        resolved_cwd=None,
        workspace_root=None,
        policy_reason=None,
    )

    workspace_root = _workspace_root(run_dir, policy)
    decision.workspace_root = workspace_root
    try:
        resolved_cwd = resolve_policy_path(run_dir, cwd or ".", policy)
    except ValueError as exc:
        return _reject(decision, str(exc))
    decision.resolved_cwd = resolved_cwd
    if not _within_root(resolved_cwd, workspace_root):
        return _reject(decision, "cwd must stay inside the workspace root")

    raw_executable = normalized_command[0]
    if "/" in raw_executable or "\\" in raw_executable or ":" in raw_executable:
        return _reject(decision, "run_command_safe only allows bare allowlisted executables")
    executable = _normalize_executable_name(raw_executable)
    if executable == "git":
        return _reject(decision, _EXPLICIT_GIT_MESSAGE)
    if _contains_wrapper(normalized_command):
        return _reject(decision, "shell wrapper execution is not allowed via run_command_safe")
    if _contains_shell_composition(normalized_command):
        return _reject(decision, "shell composition and redirection are not allowed via run_command_safe")
    if executable not in _ALLOWED_EXECUTABLES:
        return _reject(decision, f"executable '{raw_executable}' is not allowed via run_command_safe")
    if _has_banned_tokens(normalized_command[1:]):
        return _reject(decision, "command arguments include a disallowed operation")

    reason = _validate_allowed_command(
        executable,
        normalized_command,
        resolved_cwd=resolved_cwd,
        workspace_root=workspace_root,
        policy=policy,
    )
    if reason:
        return _reject(decision, reason)
    return decision

