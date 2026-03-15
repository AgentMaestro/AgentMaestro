from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from agents.models import Agent
from core.models import Workspace
from memory.models import MemoryRecord


@dataclass(frozen=True)
class ResolvedMemoryScope:
    scope_type: str
    scope_id: str
    label: str = ""


_SCOPE_ALIASES = {
    "sandbox": MemoryRecord.ScopeType.SANDBOX,
    "workspace": MemoryRecord.ScopeType.SANDBOX,
    "agent": MemoryRecord.ScopeType.AGENT,
    "user": MemoryRecord.ScopeType.USER,
}


def resolve_memory_scope(
    scope_type: str | None = None,
    scope_id: object | None = None,
    *,
    agent: Agent | str | None = None,
    workspace: Workspace | str | None = None,
    user: Any | None = None,
) -> ResolvedMemoryScope:
    explicit = scope_type is not None or scope_id is not None
    object_targets = [value for value in (agent, workspace, user) if value is not None]
    if explicit and object_targets:
        raise ValueError("Provide either an explicit scope_type/scope_id pair or a model scope object, not both.")
    if len(object_targets) > 1:
        raise ValueError("Provide only one scope object when resolving memory scope.")

    if agent is not None:
        instance = _resolve_agent(agent)
        return ResolvedMemoryScope(
            scope_type=MemoryRecord.ScopeType.AGENT,
            scope_id=str(instance.id),
            label=instance.name,
        )
    if workspace is not None:
        instance = _resolve_workspace(workspace, strict=True)
        return ResolvedMemoryScope(
            scope_type=MemoryRecord.ScopeType.SANDBOX,
            scope_id=str(instance.id),
            label=instance.name,
        )
    if user is not None:
        instance = _resolve_user(user)
        label = getattr(instance, "username", "") or getattr(instance, "email", "") or str(instance.pk)
        return ResolvedMemoryScope(
            scope_type=MemoryRecord.ScopeType.USER,
            scope_id=str(instance.pk),
            label=str(label),
        )

    normalized_type = _normalize_scope_type(scope_type)
    normalized_id = _normalize_scope_id(scope_id)
    if normalized_type == MemoryRecord.ScopeType.AGENT:
        instance = _resolve_agent(normalized_id)
        return ResolvedMemoryScope(scope_type=normalized_type, scope_id=str(instance.id), label=instance.name)
    if normalized_type == MemoryRecord.ScopeType.SANDBOX:
        instance = _resolve_workspace(normalized_id, strict=False)
        if instance is not None:
            return ResolvedMemoryScope(scope_type=normalized_type, scope_id=str(instance.id), label=instance.name)
        return ResolvedMemoryScope(scope_type=normalized_type, scope_id=normalized_id, label=normalized_id)
    if normalized_type == MemoryRecord.ScopeType.USER:
        instance = _resolve_user(normalized_id)
        label = getattr(instance, "username", "") or getattr(instance, "email", "") or str(instance.pk)
        return ResolvedMemoryScope(scope_type=normalized_type, scope_id=str(instance.pk), label=str(label))
    raise ValueError(f"Unsupported memory scope type '{scope_type}'.")


def validate_memory_scope(scope_type: str, scope_id: object) -> ResolvedMemoryScope:
    return resolve_memory_scope(scope_type=scope_type, scope_id=scope_id)


def _normalize_scope_type(scope_type: str | None) -> str:
    candidate = str(scope_type or "").strip().lower()
    if candidate not in _SCOPE_ALIASES:
        raise ValueError(f"Unsupported memory scope type '{scope_type}'.")
    return _SCOPE_ALIASES[candidate]


def _normalize_scope_id(scope_id: object | None) -> str:
    candidate = str(scope_id or "").strip()
    if not candidate:
        raise ValueError("Memory scope_id cannot be blank.")
    return candidate


def _resolve_agent(value: Agent | str) -> Agent:
    if isinstance(value, Agent):
        return value
    candidate = _normalize_scope_id(value)
    instance = _safe_get_by_pk(Agent, candidate)
    if instance is None:
        instance = Agent.objects.filter(slug=candidate).first() or Agent.objects.filter(name=candidate).first()
    if instance is None:
        raise ValueError(f"Unknown agent memory scope '{candidate}'.")
    return instance


def _resolve_workspace(value: Workspace | str, *, strict: bool) -> Workspace | None:
    if isinstance(value, Workspace):
        return value
    candidate = _normalize_scope_id(value)
    instance = _safe_get_by_pk(Workspace, candidate)
    if instance is None:
        instance = Workspace.objects.filter(name=candidate).first()
    if instance is None and strict:
        raise ValueError(f"Unknown workspace memory scope '{candidate}'.")
    return instance


def _resolve_user(value: Any):
    User = get_user_model()
    if isinstance(value, User):
        return value
    candidate = _normalize_scope_id(value)
    instance = _safe_get_by_pk(User, candidate)
    if instance is None:
        username_field = getattr(User, "USERNAME_FIELD", "username")
        instance = User.objects.filter(**{username_field: candidate}).first()
    if instance is None:
        raise ValueError(f"Unknown user memory scope '{candidate}'.")
    return instance


def _safe_get_by_pk(model, value: str):
    field = model._meta.pk
    try:
        prepared = field.get_prep_value(value)
    except (ValidationError, ValueError, TypeError):
        return None
    if prepared in (None, ""):
        return None
    return model.objects.filter(pk=prepared).first()
