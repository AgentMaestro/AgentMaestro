from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings

from tools.models import ToolApprovalGrant, ToolCall

GRANT_MODE_ONCE = "once"
GRANT_MODE_EXACT_PATH = "grant_exact_path"
GRANT_MODE_PATH_PREFIX = "grant_path_prefix"
GRANT_MODE_REPOSITORY = "grant_repository"

_FILE_TOOLS = {"file_read", "file_write"}
_ROOT_TOOLS = {"repo_tree", "search_code"}
_REPO_TOOLS = {"git_status", "git_diff", "git_log"}


@dataclass(frozen=True)
class GrantSpec:
    mode: str
    scope_type: str
    scope_path: str
    label: str
    scope_display: str


def _repo_root() -> Path:
    return Path(settings.BASE_DIR).resolve().parent


def _resolve_scope_path(raw_value: object | None) -> Path | None:
    candidate_text = str(raw_value or "").strip()
    if not candidate_text:
        return None
    try:
        candidate = Path(candidate_text).expanduser()
    except Exception:
        return None
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    try:
        return candidate.resolve()
    except Exception:
        return None


def _display_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(_repo_root())
        return relative.as_posix() or "."
    except Exception:
        return str(path)


def _path_spec(tool_name: str, args: dict[str, Any], mode: str) -> GrantSpec | None:
    path = _resolve_scope_path(args.get("path"))
    if path is None:
        return None
    if mode == GRANT_MODE_EXACT_PATH:
        return GrantSpec(
            mode=mode,
            scope_type=ToolApprovalGrant.ScopeType.EXACT_PATH,
            scope_path=str(path),
            label=f"{tool_name} for file {_display_path(path)}",
            scope_display=_display_path(path),
        )
    if mode == GRANT_MODE_PATH_PREFIX:
        directory = path.parent
        return GrantSpec(
            mode=mode,
            scope_type=ToolApprovalGrant.ScopeType.PATH_PREFIX,
            scope_path=str(directory),
            label=f"{tool_name} in directory {_display_path(directory)}",
            scope_display=_display_path(directory),
        )
    return None


def _root_spec(tool_name: str, args: dict[str, Any], mode: str) -> GrantSpec | None:
    root_value = args.get("path") if tool_name == "repo_tree" else args.get("root")
    root_path = _resolve_scope_path(root_value or ".")
    if root_path is None or mode != GRANT_MODE_PATH_PREFIX:
        return None
    return GrantSpec(
        mode=mode,
        scope_type=ToolApprovalGrant.ScopeType.PATH_PREFIX,
        scope_path=str(root_path),
        label=f"{tool_name} under root {_display_path(root_path)}",
        scope_display=_display_path(root_path),
    )


def _repo_spec(tool_name: str, args: dict[str, Any], mode: str) -> GrantSpec | None:
    repo_path = _resolve_scope_path(args.get("repo_dir") or ".")
    if repo_path is None or mode != GRANT_MODE_REPOSITORY:
        return None
    return GrantSpec(
        mode=mode,
        scope_type=ToolApprovalGrant.ScopeType.REPO_EXACT,
        scope_path=str(repo_path),
        label=f"{tool_name} in repository {_display_path(repo_path)}",
        scope_display=_display_path(repo_path),
    )


def build_grant_spec(tool_name: str, args: dict[str, Any], mode: str) -> GrantSpec | None:
    if mode == GRANT_MODE_ONCE:
        return None
    if tool_name in _FILE_TOOLS:
        return _path_spec(tool_name, args, mode)
    if tool_name in _ROOT_TOOLS:
        return _root_spec(tool_name, args, mode)
    if tool_name in _REPO_TOOLS:
        return _repo_spec(tool_name, args, mode)
    return None


def available_grant_options(tool_name: str, args: dict[str, Any]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = [{"mode": GRANT_MODE_ONCE, "label": "Approve once"}]
    if tool_name in _FILE_TOOLS:
        exact = build_grant_spec(tool_name, args, GRANT_MODE_EXACT_PATH)
        prefix = build_grant_spec(tool_name, args, GRANT_MODE_PATH_PREFIX)
        if exact:
            options.append({"mode": GRANT_MODE_EXACT_PATH, "label": f"Approve this file for this run ({exact.scope_display})"})
        if prefix:
            options.append({"mode": GRANT_MODE_PATH_PREFIX, "label": f"Approve this directory for this run ({prefix.scope_display})"})
        return options
    if tool_name in _ROOT_TOOLS:
        root = build_grant_spec(tool_name, args, GRANT_MODE_PATH_PREFIX)
        if root:
            options.append({"mode": GRANT_MODE_PATH_PREFIX, "label": f"Approve this root for this run ({root.scope_display})"})
        return options
    if tool_name in _REPO_TOOLS:
        repo = build_grant_spec(tool_name, args, GRANT_MODE_REPOSITORY)
        if repo:
            options.append({"mode": GRANT_MODE_REPOSITORY, "label": f"Approve this repository for this run ({repo.scope_display})"})
        return options
    return options


def serialize_grant(grant: ToolApprovalGrant) -> dict[str, str | None]:
    created_by = getattr(grant.created_by, "username", None)
    return {
        "id": str(grant.id),
        "tool_name": grant.tool_name,
        "scope_type": grant.scope_type,
        "scope_path": grant.scope_path,
        "scope_display": str(grant.metadata.get("scope_display") or _display_path(Path(grant.scope_path))),
        "label": str(grant.metadata.get("label") or f"{grant.tool_name}"),
        "created_by": created_by,
        "created_at": grant.created_at.isoformat(),
    }


def active_grants_for_run(run_id: str) -> list[dict[str, str | None]]:
    grants = (
        ToolApprovalGrant.objects
        .filter(run_id=run_id, revoked_at__isnull=True)
        .select_related("created_by")
        .order_by("tool_name", "scope_path", "created_at")
    )
    return [serialize_grant(grant) for grant in grants]


def create_grant_from_tool_call(tool_call: ToolCall, user, mode: str) -> ToolApprovalGrant | None:
    spec = build_grant_spec(tool_call.tool_name, tool_call.args or {}, mode)
    if spec is None:
        return None
    return ToolApprovalGrant.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_name=tool_call.tool_name,
        scope_type=spec.scope_type,
        scope_path=spec.scope_path,
        created_by=user,
        source_tool_call=tool_call,
        metadata={
            "mode": spec.mode,
            "label": spec.label,
            "scope_display": spec.scope_display,
        },
    )


def _path_matches(grant: ToolApprovalGrant, candidate: Path) -> bool:
    scope = Path(grant.scope_path)
    if grant.scope_type == ToolApprovalGrant.ScopeType.EXACT_PATH:
        return candidate == scope
    if grant.scope_type == ToolApprovalGrant.ScopeType.PATH_PREFIX:
        return candidate == scope or candidate.is_relative_to(scope)
    return False


def matches_grant(grant: ToolApprovalGrant, *, tool_name: str, args: dict[str, Any]) -> bool:
    if grant.tool_name != tool_name or grant.revoked_at is not None:
        return False
    if tool_name in _FILE_TOOLS:
        candidate = _resolve_scope_path(args.get("path"))
        return candidate is not None and _path_matches(grant, candidate)
    if tool_name in _ROOT_TOOLS:
        root_value = args.get("path") if tool_name == "repo_tree" else args.get("root")
        candidate = _resolve_scope_path(root_value or ".")
        return candidate is not None and _path_matches(grant, candidate)
    if tool_name in _REPO_TOOLS:
        candidate = _resolve_scope_path(args.get("repo_dir") or ".")
        return candidate is not None and grant.scope_type == ToolApprovalGrant.ScopeType.REPO_EXACT and candidate == Path(grant.scope_path)
    return False


def find_matching_grant(*, run_id: str, tool_name: str, args: dict[str, Any]) -> ToolApprovalGrant | None:
    grants = (
        ToolApprovalGrant.objects
        .filter(run_id=run_id, tool_name=tool_name, revoked_at__isnull=True)
        .select_related("created_by")
        .order_by("-created_at")
    )
    for grant in grants:
        if matches_grant(grant, tool_name=tool_name, args=args):
            return grant
    return None
