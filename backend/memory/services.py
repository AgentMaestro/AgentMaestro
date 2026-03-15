from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db.models import Case, F, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from memory.models import MemoryRecord
from memory.scope import resolve_memory_scope

MAX_CONTENT_CHARS = 4000
MAX_SUMMARY_CHARS = 600
MAX_TAGS = 12
MAX_DEDUPE_KEY_CHARS = 255
MAX_SOURCE_KIND_CHARS = 64
MAX_SOURCE_REF_CHARS = 255
_DEDUPE_MODES = {"auto", "key", "exact", "none"}
_PLACEHOLDER_TEXT = {"todo", "tbd", "placeholder", "unknown", "n/a", "na"}


def remember(
    scope_type: str | None = None,
    scope_id: str | None = None,
    memory_kind: str = MemoryRecord.MemoryKind.SEMANTIC,
    content: str = "",
    tags: list[str] | None = None,
    importance: float | str | Decimal = Decimal("0.50"),
    summary: str | None = None,
    pinned: bool = False,
    expires_at: datetime | None = None,
    dedupe_key: str | None = None,
    dedupe_mode: str = "auto",
    source_kind: str | None = None,
    source_ref: str | None = None,
    *,
    agent=None,
    workspace=None,
    user=None,
) -> MemoryRecord:
    scope = resolve_memory_scope(scope_type=scope_type, scope_id=scope_id, agent=agent, workspace=workspace, user=user)
    normalized_memory_kind = _normalize_memory_kind(memory_kind)
    normalized_content = _trim_text(content, MAX_CONTENT_CHARS)
    normalized_summary = _trim_text(summary or "", MAX_SUMMARY_CHARS)
    normalized_tags = _normalize_tags(tags or [])
    normalized_importance = _normalize_importance(importance)
    normalized_dedupe_key = normalize_dedupe_key(dedupe_key)
    normalized_dedupe_mode = _normalize_dedupe_mode(dedupe_mode, has_dedupe_key=bool(normalized_dedupe_key))
    normalized_source_kind = _normalize_source_kind(source_kind)
    normalized_source_ref = _trim_text(source_ref or "", MAX_SOURCE_REF_CHARS)
    now = timezone.now()

    existing = _find_existing_memory(
        scope_type=scope.scope_type,
        scope_id=scope.scope_id,
        memory_kind=normalized_memory_kind,
        content=normalized_content,
        dedupe_key=normalized_dedupe_key,
        dedupe_mode=normalized_dedupe_mode,
    )
    if existing:
        merged_tags = _merge_tags(existing.tags, normalized_tags)
        updated_fields = ["access_count", "last_accessed_at", "updated_at"]
        existing.access_count = int(existing.access_count or 0) + 1
        existing.last_accessed_at = now

        if normalized_summary and existing.summary != normalized_summary:
            existing.summary = normalized_summary
            updated_fields.append("summary")
        if merged_tags != list(existing.tags or []):
            existing.tags = merged_tags
            updated_fields.append("tags")
        if normalized_importance > existing.importance:
            existing.importance = normalized_importance
            updated_fields.append("importance")
        if _should_replace_content(existing.content, normalized_content):
            existing.content = normalized_content
            updated_fields.append("content")
        if normalized_dedupe_key and existing.dedupe_key != normalized_dedupe_key:
            existing.dedupe_key = normalized_dedupe_key
            updated_fields.append("dedupe_key")
        if normalized_source_kind and not existing.source_kind:
            existing.source_kind = normalized_source_kind
            updated_fields.append("source_kind")
        if normalized_source_ref and not existing.source_ref:
            existing.source_ref = normalized_source_ref
            updated_fields.append("source_ref")
        if pinned and not existing.pinned:
            existing.pinned = True
            updated_fields.append("pinned")
        if expires_at is not None and existing.expires_at != expires_at:
            existing.expires_at = expires_at
            updated_fields.append("expires_at")
        elif expires_at is None and existing.expires_at is not None and existing.expires_at <= now:
            existing.expires_at = None
            updated_fields.append("expires_at")

        existing.save(update_fields=list(dict.fromkeys(updated_fields)))
        return existing

    return MemoryRecord.objects.create(
        scope_type=scope.scope_type,
        scope_id=scope.scope_id,
        memory_kind=normalized_memory_kind,
        dedupe_key=normalized_dedupe_key,
        source_kind=normalized_source_kind,
        source_ref=normalized_source_ref,
        content=normalized_content,
        summary=normalized_summary,
        tags=normalized_tags,
        importance=normalized_importance,
        pinned=bool(pinned),
        expires_at=expires_at,
        access_count=1,
        last_accessed_at=now,
    )


def search_memory(
    query: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
    memory_kind: str | None = None,
    limit: int = 5,
    *,
    agent=None,
    workspace=None,
    user=None,
):
    queryset = _active_queryset(MemoryRecord.objects.all())
    if scope_type is not None or scope_id is not None or agent is not None or workspace is not None or user is not None:
        scope = resolve_memory_scope(scope_type=scope_type, scope_id=scope_id, agent=agent, workspace=workspace, user=user)
        queryset = queryset.filter(scope_type=scope.scope_type, scope_id=scope.scope_id)
    normalized_memory_kind = _normalize_optional_memory_kind(memory_kind)
    if normalized_memory_kind:
        queryset = queryset.filter(memory_kind=normalized_memory_kind)

    normalized_query = str(query or "").strip()
    if normalized_query:
        normalized_tag = normalized_query.lower()
        tag_filter = Q(tags__contains=[normalized_tag])
        queryset = queryset.filter(
            Q(content__icontains=normalized_query)
            | Q(summary__icontains=normalized_query)
            | tag_filter
        ).annotate(
            relevance=Case(
                When(content__icontains=normalized_query, then=Value(2)),
                When(summary__icontains=normalized_query, then=Value(1)),
                When(tag_filter, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        if normalized_memory_kind == MemoryRecord.MemoryKind.EPISODIC:
            queryset = queryset.order_by("-relevance", "-updated_at", "-importance")
        else:
            queryset = queryset.order_by("-relevance", "-importance", "-updated_at")
    else:
        if normalized_memory_kind == MemoryRecord.MemoryKind.EPISODIC:
            queryset = queryset.order_by("-updated_at", "-importance")
        else:
            queryset = queryset.order_by("-importance", "-updated_at")

    results = list(queryset[: max(1, min(int(limit or 5), 20))])
    if results:
        now = timezone.now()
        MemoryRecord.objects.filter(id__in=[record.id for record in results]).update(
            last_accessed_at=now,
            access_count=F("access_count") + 1,
        )
        for record in results:
            record.last_accessed_at = now
            record.access_count = int(record.access_count or 0) + 1
    return results


def get_recent_memory(
    scope_type: str | None = None,
    scope_id: str | None = None,
    memory_kind: str | None = None,
    limit: int = 5,
    *,
    agent=None,
    workspace=None,
    user=None,
):
    queryset = _active_queryset(MemoryRecord.objects.all())
    if scope_type is not None or scope_id is not None or agent is not None or workspace is not None or user is not None:
        scope = resolve_memory_scope(scope_type=scope_type, scope_id=scope_id, agent=agent, workspace=workspace, user=user)
        queryset = queryset.filter(scope_type=scope.scope_type, scope_id=scope.scope_id)
    normalized_memory_kind = _normalize_optional_memory_kind(memory_kind)
    if normalized_memory_kind:
        queryset = queryset.filter(memory_kind=normalized_memory_kind)
    queryset = queryset.annotate(recent_touch=Coalesce("last_accessed_at", "updated_at"))
    return list(queryset.order_by("-pinned", "-recent_touch", "-updated_at")[: max(1, min(int(limit or 5), 20))])


def normalize_dedupe_key(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return ""
    if len(candidate) <= MAX_DEDUPE_KEY_CHARS:
        return candidate
    return candidate[:MAX_DEDUPE_KEY_CHARS].rstrip("-_: ")


def _active_queryset(queryset):
    now = timezone.now()
    return queryset.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def _find_existing_memory(*, scope_type: str, scope_id: str, memory_kind: str, content: str, dedupe_key: str, dedupe_mode: str):
    if dedupe_mode == "none":
        return None
    queryset = MemoryRecord.objects.filter(
        scope_type=scope_type,
        scope_id=scope_id,
        memory_kind=memory_kind,
    )
    if dedupe_mode == "key" and dedupe_key:
        queryset = queryset.filter(dedupe_key=dedupe_key)
    else:
        queryset = queryset.filter(content=content)
    return queryset.order_by("-pinned", "-updated_at").first()


def _merge_tags(existing_tags: list[str] | None, incoming_tags: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for collection in (existing_tags or [], incoming_tags):
        for tag in collection:
            candidate = str(tag or "").strip().lower()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            merged.append(candidate)
            if len(merged) >= MAX_TAGS:
                return merged
    return merged


def _normalize_tags(tags: list[str]) -> list[str]:
    return _merge_tags([], tags)


def _normalize_importance(value: float | str | Decimal) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.50")
    if normalized < Decimal("0.00"):
        return Decimal("0.00")
    if normalized > Decimal("1.00"):
        return Decimal("1.00")
    return normalized.quantize(Decimal("0.01"))


def _normalize_memory_kind(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    valid = {choice for choice, _label in MemoryRecord.MemoryKind.choices}
    if candidate not in valid:
        raise ValueError(f"Unsupported memory_kind '{value}'.")
    return candidate


def _normalize_optional_memory_kind(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return _normalize_memory_kind(value)


def _normalize_dedupe_mode(value: str, *, has_dedupe_key: bool) -> str:
    candidate = str(value or "auto").strip().lower()
    if candidate not in _DEDUPE_MODES:
        raise ValueError(f"Unsupported dedupe_mode '{value}'.")
    if candidate == "auto":
        return "key" if has_dedupe_key else "exact"
    if candidate == "key" and not has_dedupe_key:
        return "exact"
    return candidate


def _normalize_source_kind(value: str | None) -> str:
    candidate = str(value or "").strip().lower().replace(" ", "_")
    if not candidate:
        return ""
    if len(candidate) <= MAX_SOURCE_KIND_CHARS:
        return candidate
    return candidate[:MAX_SOURCE_KIND_CHARS].rstrip("_-")


def _should_replace_content(existing_content: str, incoming_content: str) -> bool:
    current = str(existing_content or "").strip()
    candidate = str(incoming_content or "").strip()
    if not candidate or candidate == current:
        return False
    if not current:
        return True
    if _looks_placeholder(current) and not _looks_placeholder(candidate):
        return True
    if _looks_truncated(current) and len(candidate) > len(current) + 20:
        return True
    return False


def _looks_placeholder(text: str) -> bool:
    candidate = str(text or "").strip().lower()
    return candidate in _PLACEHOLDER_TEXT


def _looks_truncated(text: str) -> bool:
    return str(text or "").strip().endswith("...")


def _trim_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    suffix = "..."
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: max(limit - len(suffix), 0)].rstrip() + suffix
