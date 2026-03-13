from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Any


_GIT_RECOMMENDATIONS = {
    "add": "git_add",
    "status": "git_status",
    "diff": "git_diff",
    "log": "git_log",
    "apply": "git_apply",
    "commit": "git_commit",
    "push": "git_push",
    "checkout": "git_checkout",
    "switch": "git_checkout",
}
_GIT_BRANCH_CREATE_FLAGS = {"-b", "-B", "-c", "-C", "--create", "--force-create"}
_GIT_BRANCH_ALIASES = {"branch", "checkout", "switch"}
_SIMPLE_FILE_READ_COMMANDS = {"cat", "type", "more", "get-content", "gc"}
_CMD_SHELLS = {"cmd", "cmd.exe"}
_POWERSHELL_SHELLS = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
_SHELL_FLAG_NAMES = {"/c", "/k", "-c", "-command"}
_SHELL_SPLIT_PATTERN = re.compile(r"\s*(?:&&|\|\||[|;])\s*")


@dataclass(frozen=True)
class CommandAliasRecommendation:
    tool_name: str
    reason: str


class ToolCommandGuardrailError(ValueError):
    def __init__(self, *, tool_name: str, recommended_tool: str, reason: str):
        self.tool_name = tool_name
        self.recommended_tool = recommended_tool
        self.reason = reason
        super().__init__(f"Use '{recommended_tool}' instead of '{tool_name}': {reason}")


@dataclass(frozen=True)
class _CommandView:
    raw_tokens: tuple[str, ...]
    command_tokens: tuple[str, ...]
    shell_text: str


def _basename(value: str) -> str:
    return Path(str(value or "")).name.lower()


def _split_shell_text(shell_text: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(shell_text, posix=False))
    except ValueError:
        return tuple(part for part in shell_text.strip().split() if part)


def _shell_segments(shell_text: str) -> tuple[str, ...]:
    stripped = shell_text.strip()
    if not stripped:
        return ()
    return tuple(segment.strip() for segment in _SHELL_SPLIT_PATTERN.split(stripped) if segment.strip())


def _unwrap_command(cmd: list[str]) -> _CommandView:
    tokens = tuple(str(part) for part in cmd if str(part).strip())
    if not tokens:
        return _CommandView(raw_tokens=(), command_tokens=(), shell_text="")

    program = _basename(tokens[0])
    if program in _CMD_SHELLS:
        for index, token in enumerate(tokens[1:], start=1):
            if token.lower() in {"/c", "/k"}:
                inner_tokens = tokens[index + 1:]
                return _CommandView(raw_tokens=tokens, command_tokens=inner_tokens, shell_text=" ".join(inner_tokens))
    if program in _POWERSHELL_SHELLS:
        for index, token in enumerate(tokens[1:], start=1):
            if token.lower() in _SHELL_FLAG_NAMES:
                shell_text = " ".join(tokens[index + 1:]).strip()
                return _CommandView(raw_tokens=tokens, command_tokens=_split_shell_text(shell_text), shell_text=shell_text)
    return _CommandView(raw_tokens=tokens, command_tokens=tokens, shell_text=" ".join(tokens))


def _non_flag_tokens(tokens: tuple[str, ...]) -> list[str]:
    return [token for token in tokens[1:] if token and not token.startswith("-")]


def _git_tokens_recommendation(tokens: tuple[str, ...]) -> CommandAliasRecommendation | None:
    if len(tokens) < 2 or _basename(tokens[0]) != "git":
        return None
    subcommand = tokens[1].lower()
    if subcommand in {"checkout", "switch"} and any(token in _GIT_BRANCH_CREATE_FLAGS for token in tokens[2:]):
        return CommandAliasRecommendation(
            tool_name="git_branch_create",
            reason="This command is creating a branch or switching with branch creation flags.",
        )
    if subcommand == "branch":
        if any(token in _GIT_BRANCH_CREATE_FLAGS for token in tokens[2:]) or any(token.startswith("-M") or token.startswith("-m") for token in tokens[2:]):
            return CommandAliasRecommendation(
                tool_name="git_branch_create",
                reason="Git branch operations should use dedicated git branch tooling instead of run_command.",
            )
        return CommandAliasRecommendation(
            tool_name="git_branch_create",
            reason="Git branch operations should use dedicated git branch tooling instead of run_command.",
        )
    recommended_tool = _GIT_RECOMMENDATIONS.get(subcommand)
    if recommended_tool:
        return CommandAliasRecommendation(
            tool_name=recommended_tool,
            reason=f"Git subcommand '{subcommand}' has a dedicated tool.",
        )
    return None


def _git_recommendation(view: _CommandView) -> CommandAliasRecommendation | None:
    direct = _git_tokens_recommendation(view.command_tokens)
    if direct is not None:
        return direct

    for segment in _shell_segments(view.shell_text):
        recommendation = _git_tokens_recommendation(_split_shell_text(segment))
        if recommendation is not None:
            return recommendation
    return None


def _file_read_tokens_recommendation(tokens: tuple[str, ...]) -> CommandAliasRecommendation | None:
    if not tokens:
        return None
    program = _basename(tokens[0])
    if program in _SIMPLE_FILE_READ_COMMANDS and _non_flag_tokens(tokens):
        return CommandAliasRecommendation(
            tool_name="file_read",
            reason="This command is a direct file content read.",
        )
    return None


def _file_read_recommendation(view: _CommandView) -> CommandAliasRecommendation | None:
    direct = _file_read_tokens_recommendation(view.command_tokens)
    if direct is not None:
        return direct

    for segment in _shell_segments(view.shell_text):
        recommendation = _file_read_tokens_recommendation(_split_shell_text(segment))
        if recommendation is not None:
            return recommendation
    return None


def classify_run_command_alias(args: dict[str, Any] | None) -> CommandAliasRecommendation | None:
    payload = args or {}
    cmd = payload.get("cmd")
    if not isinstance(cmd, list) or not cmd:
        return None
    tokens = [str(part) for part in cmd if str(part).strip()]
    if not tokens:
        return None
    view = _unwrap_command(tokens)
    return _git_recommendation(view) or _file_read_recommendation(view)


def validate_tool_request(tool_name: str, args: dict[str, Any] | None) -> None:
    if tool_name != "run_command":
        return
    recommendation = classify_run_command_alias(args)
    if recommendation is None:
        return
    raise ToolCommandGuardrailError(
        tool_name=tool_name,
        recommended_tool=recommendation.tool_name,
        reason=recommendation.reason,
    )
