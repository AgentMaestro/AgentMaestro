from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from memory.models import MemoryRecord
from memory.services import remember

logger = logging.getLogger(__name__)

DISTILLED_SOURCE_KIND = "distilled_memory"
DEFAULT_LOW_IMPORTANCE_THRESHOLD = Decimal("0.40")
DEFAULT_PROTECTED_IMPORTANCE_THRESHOLD = Decimal("0.80")
DEFAULT_RETENTION_DAYS = 30
DEFAULT_RETENTION_BATCH_SIZE = 500
DEFAULT_DISTILL_GROUP_LIMIT = 50
MAX_DISTILLED_SUMMARY_CHARS = 160
MAX_DISTILLED_CONTENT_CHARS = 600
MAX_DISTILLED_TAGS = 8


@dataclass
class RetentionGroup:
    bucket_key: str
    records: list[MemoryRecord]


@transaction.atomic
def run_memory_retention(
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    retention_days: int | None = None,
    batch_size: int | None = None,
    group_limit: int | None = None,
) -> dict[str, object]:
    current_time = now or timezone.now()
    cutoff = current_time - timedelta(days=int(retention_days or getattr(settings, "MEMORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)))
    effective_batch_size = int(batch_size or getattr(settings, "MEMORY_RETENTION_BATCH_SIZE", DEFAULT_RETENTION_BATCH_SIZE))
    effective_group_limit = int(
        group_limit or getattr(settings, "MEMORY_EPISODIC_DISTILL_GROUP_LIMIT", DEFAULT_DISTILL_GROUP_LIMIT)
    )
    low_importance_threshold = _decimal_setting(
        "MEMORY_RETENTION_LOW_IMPORTANCE_THRESHOLD",
        DEFAULT_LOW_IMPORTANCE_THRESHOLD,
    )
    protected_importance_threshold = _decimal_setting(
        "MEMORY_RETENTION_PROTECTED_IMPORTANCE_THRESHOLD",
        DEFAULT_PROTECTED_IMPORTANCE_THRESHOLD,
    )

    aged_queryset = MemoryRecord.objects.filter(_age_cutoff_query(cutoff=cutoff, now=current_time)).order_by("created_at")
    examined_records = list(aged_queryset[: max(1, effective_batch_size)])
    candidate_records = [
        record
        for record in examined_records
        if _is_retention_candidate(
            record,
            cutoff=cutoff,
            now=current_time,
            low_importance_threshold=low_importance_threshold,
            protected_importance_threshold=protected_importance_threshold,
        )
    ]
    preserved = len(examined_records) - len(candidate_records)

    grouped_candidates = _build_retention_groups(candidate_records)
    distill_groups = grouped_candidates[: max(0, effective_group_limit)]
    deferred_groups = grouped_candidates[max(0, effective_group_limit) :]
    deferred_ids = {record.id for group in deferred_groups for record in group.records}
    distill_group_ids = {record.id for group in distill_groups for record in group.records}
    direct_purge_records = [record for record in candidate_records if record.id not in distill_group_ids and record.id not in deferred_ids]

    report: dict[str, object] = {
        "dry_run": dry_run,
        "cutoff": cutoff.isoformat(),
        "examined": len(examined_records),
        "candidates": len(candidate_records),
        "preserved": preserved + len(deferred_ids),
        "groups_distilled": 0,
        "raw_records_purged": 0,
        "directly_purged": 0,
        "deferred_groups": len(deferred_groups),
        "errors": [],
    }

    for group in distill_groups:
        payload = _build_distilled_payload(group)
        report["groups_distilled"] = int(report["groups_distilled"]) + 1
        report["raw_records_purged"] = int(report["raw_records_purged"]) + len(group.records)
        if dry_run:
            continue
        try:
            _upsert_distilled_memory(group, payload)
            MemoryRecord.objects.filter(id__in=[record.id for record in group.records]).delete()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Memory retention distillation failed bucket=%s", group.bucket_key)
            report["errors"].append(f"distill:{group.bucket_key}:{exc}")
            report["groups_distilled"] = int(report["groups_distilled"]) - 1
            report["raw_records_purged"] = int(report["raw_records_purged"]) - len(group.records)

    report["directly_purged"] = len(direct_purge_records)
    if not dry_run and direct_purge_records:
        MemoryRecord.objects.filter(id__in=[record.id for record in direct_purge_records]).delete()

    return report


def get_retention_cutoff(*, now: datetime | None = None, retention_days: int | None = None) -> datetime:
    current_time = now or timezone.now()
    effective_days = int(retention_days or getattr(settings, "MEMORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    return current_time - timedelta(days=effective_days)


def get_memory_retention_health(*, now: datetime | None = None, retention_days: int | None = None) -> dict[str, object]:
    current_time = now or timezone.now()
    cutoff = get_retention_cutoff(now=current_time, retention_days=retention_days)
    low_importance_threshold = _decimal_setting(
        "MEMORY_RETENTION_LOW_IMPORTANCE_THRESHOLD",
        DEFAULT_LOW_IMPORTANCE_THRESHOLD,
    )
    protected_importance_threshold = _decimal_setting(
        "MEMORY_RETENTION_PROTECTED_IMPORTANCE_THRESHOLD",
        DEFAULT_PROTECTED_IMPORTANCE_THRESHOLD,
    )

    eligible_base = MemoryRecord.objects.filter(
        memory_kind=MemoryRecord.MemoryKind.EPISODIC,
        pinned=False,
        importance__lt=low_importance_threshold,
    ).exclude(source_kind=DISTILLED_SOURCE_KIND)
    expired_fast_path = eligible_base.filter(expires_at__isnull=False, expires_at__lte=current_time)
    aged_low_value = eligible_base.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=current_time),
        updated_at__lt=cutoff,
    ).filter(Q(last_accessed_at__isnull=True) | Q(last_accessed_at__lt=cutoff))
    eligible_records = list((expired_fast_path | aged_low_value).distinct().order_by("created_at"))
    distillable_groups = _build_retention_groups(eligible_records)
    distillable_record_ids = {record.id for group in distillable_groups for record in group.records}

    return {
        "cutoff": cutoff.isoformat(),
        "low_importance_threshold": str(low_importance_threshold),
        "protected_importance_threshold": str(protected_importance_threshold),
        "eligible_records": len(eligible_records),
        "expired_fast_path_records": expired_fast_path.count(),
        "aged_low_value_records": aged_low_value.count(),
        "distillable_groups": len(distillable_groups),
        "distillable_raw_records": len(distillable_record_ids),
        "direct_purge_candidates": len(eligible_records) - len(distillable_record_ids),
    }


def _age_cutoff_query(*, cutoff: datetime, now: datetime):
    return (
        (models_q("expires_at__isnull", False) & models_q("expires_at__lte", now))
        | (
            models_q("updated_at__lt", cutoff)
            & (models_q("last_accessed_at__isnull", True) | models_q("last_accessed_at__lt", cutoff))
        )
    )


def _is_retention_candidate(
    record: MemoryRecord,
    *,
    cutoff: datetime,
    now: datetime,
    low_importance_threshold: Decimal,
    protected_importance_threshold: Decimal,
) -> bool:
    if record.pinned:
        return False
    if record.memory_kind != MemoryRecord.MemoryKind.EPISODIC:
        return False
    if record.source_kind == DISTILLED_SOURCE_KIND:
        return False
    if record.importance >= protected_importance_threshold:
        return False
    if record.importance >= low_importance_threshold:
        return False
    if record.expires_at is not None and record.expires_at <= now:
        return True
    if record.updated_at >= cutoff:
        return False
    if record.last_accessed_at is not None and record.last_accessed_at >= cutoff:
        return False
    return True


def _build_retention_groups(records: list[MemoryRecord]) -> list[RetentionGroup]:
    grouped: dict[str, list[MemoryRecord]] = defaultdict(list)
    for record in records:
        bucket_key = build_retention_bucket_key(record)
        if not bucket_key:
            continue
        grouped[bucket_key].append(record)
    groups = [RetentionGroup(bucket_key=key, records=value) for key, value in grouped.items() if len(value) > 1]
    groups.sort(key=lambda group: min(record.created_at for record in group.records))
    return groups


def build_retention_bucket_key(record: MemoryRecord) -> str:
    if record.memory_kind != MemoryRecord.MemoryKind.EPISODIC:
        return ""
    if record.source_kind == DISTILLED_SOURCE_KIND:
        return ""
    if record.dedupe_key:
        identity = f"key:{record.dedupe_key}"
    elif record.source_kind and record.source_ref:
        identity = f"ref:{_normalize_bucket_fragment(record.source_ref)}"
    elif record.source_kind and record.summary:
        identity = f"summary:{_normalize_bucket_fragment(record.summary)}"
    else:
        return ""
    return ":".join(
        [
            record.scope_type,
            record.scope_id,
            record.memory_kind,
            record.source_kind or "unknown",
            identity,
        ]
    )


def _build_distilled_payload(group: RetentionGroup) -> dict[str, object]:
    records = sorted(group.records, key=lambda item: item.created_at)
    first = records[0]
    count = len(records)
    start = records[0].created_at.date().isoformat()
    end = max(record.updated_at for record in records).date().isoformat()
    representative = _representative_summary(records)
    content = _trim_text(
        f"{representative} recorded {count} times between {start} and {end} for {first.scope_type}:{first.scope_id}.",
        MAX_DISTILLED_CONTENT_CHARS,
    )
    summary = _trim_text(f"Distilled {representative}", MAX_DISTILLED_SUMMARY_CHARS)
    tags = _merge_tags([["distilled", first.source_kind or "episodic"], *(record.tags or [] for record in records)])
    importance = max(Decimal("0.45"), max(record.importance for record in records))
    return {
        "scope_type": first.scope_type,
        "scope_id": first.scope_id,
        "memory_kind": MemoryRecord.MemoryKind.EPISODIC,
        "content": content,
        "summary": summary,
        "tags": tags,
        "importance": importance,
        "source_kind": DISTILLED_SOURCE_KIND,
        "source_ref": group.bucket_key,
        "dedupe_key": f"distilled:{group.bucket_key}",
    }


def _upsert_distilled_memory(group: RetentionGroup, payload: dict[str, object]) -> MemoryRecord:
    record = remember(
        scope_type=str(payload["scope_type"]),
        scope_id=str(payload["scope_id"]),
        memory_kind=str(payload["memory_kind"]),
        content=str(payload["content"]),
        tags=list(payload["tags"]),
        importance=payload["importance"],
        summary=str(payload["summary"]),
        source_kind=str(payload["source_kind"]),
        source_ref=str(payload["source_ref"]),
        dedupe_key=str(payload["dedupe_key"]),
        dedupe_mode="key",
    )
    updated_fields: list[str] = []
    if record.content != payload["content"]:
        record.content = str(payload["content"])
        updated_fields.append("content")
    if record.summary != payload["summary"]:
        record.summary = str(payload["summary"])
        updated_fields.append("summary")
    if list(record.tags or []) != list(payload["tags"]):
        record.tags = list(payload["tags"])
        updated_fields.append("tags")
    if record.source_kind != payload["source_kind"]:
        record.source_kind = str(payload["source_kind"])
        updated_fields.append("source_kind")
    if record.source_ref != payload["source_ref"]:
        record.source_ref = str(payload["source_ref"])
        updated_fields.append("source_ref")
    if updated_fields:
        record.save(update_fields=updated_fields + ["updated_at"])
    return record


def _representative_summary(records: list[MemoryRecord]) -> str:
    for record in reversed(records):
        summary = str(record.summary or "").strip()
        if summary:
            return summary
    return _trim_text(str(records[-1].content or "episodic memory").splitlines()[0], MAX_DISTILLED_SUMMARY_CHARS)


def _merge_tags(tag_groups: list[list[str]]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in tag_groups:
        for tag in group:
            candidate = str(tag or "").strip().lower().replace(" ", "-")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            merged.append(candidate)
            if len(merged) >= MAX_DISTILLED_TAGS:
                return merged
    return merged


def _normalize_bucket_fragment(value: str) -> str:
    candidate = str(value or "").strip().lower()
    normalized = []
    for char in candidate:
        if char.isalnum() or char in {"-", ":"}:
            normalized.append(char)
        elif char.isspace() or char in {"_", "/", "\\", "."}:
            normalized.append("-")
    collapsed = "".join(normalized).strip("-:")
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return collapsed[:80]


def _trim_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    suffix = "..."
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: max(limit - len(suffix), 0)].rstrip() + suffix


def _decimal_setting(name: str, default: Decimal) -> Decimal:
    raw_value = getattr(settings, name, default)
    try:
        value = Decimal(str(raw_value))
    except Exception:  # noqa: BLE001
        return default
    if value < Decimal("0.00"):
        return Decimal("0.00")
    if value > Decimal("1.00"):
        return Decimal("1.00")
    return value.quantize(Decimal("0.01"))


def models_q(field: str, value):
    from django.db.models import Q

    return Q(**{field: value})
