from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils import timezone

from logging_utils import get_app_logger

from finance.models import FinanceDataCacheEntry, FinanceResearchSnapshot

from .market_hours import next_equity_quote_purge_cutoff


logger = get_app_logger("finance")


def _delete_limited_queryset(queryset, *, limit: int) -> int:
    if limit <= 0:
        return 0
    ids = list(queryset.values_list("id", flat=True)[:limit])
    if not ids:
        return 0
    deleted, _ = queryset.model.objects.filter(id__in=ids).delete()
    return int(deleted or 0)


def purge_expired_finance_cache(*, limit: int | None = None, now=None) -> dict[str, Any]:
    current_time = now or timezone.now()
    batch_limit = max(1, int(limit or getattr(settings, "FINANCE_CACHE_PURGE_LIMIT", 1000)))
    quote_cutoff = next_equity_quote_purge_cutoff(current_time)
    quote_purge_allowed = current_time >= quote_cutoff

    non_quote_queryset = FinanceDataCacheEntry.objects.filter(
        expires_at__isnull=False,
        expires_at__lte=current_time,
    ).exclude(data_kind=FinanceDataCacheEntry.DataKind.QUOTE)
    non_quote_deleted = _delete_limited_queryset(non_quote_queryset, limit=batch_limit)

    quote_deleted = 0
    if quote_purge_allowed:
        quote_queryset = FinanceDataCacheEntry.objects.filter(
            data_kind=FinanceDataCacheEntry.DataKind.QUOTE,
            expires_at__isnull=False,
            expires_at__lte=current_time,
        )
        quote_deleted = _delete_limited_queryset(quote_queryset, limit=batch_limit)

    snapshot_queryset = FinanceResearchSnapshot.objects.filter(
        expires_at__isnull=False,
        expires_at__lte=current_time,
    )
    snapshot_deleted = _delete_limited_queryset(snapshot_queryset, limit=batch_limit)

    logger.info(
        "finance cache purge finished quote_deleted=%s non_quote_deleted=%s snapshot_deleted=%s quote_cutoff=%s batch_limit=%s",
        quote_deleted,
        non_quote_deleted,
        snapshot_deleted,
        quote_cutoff.isoformat(),
        batch_limit,
    )
    return {
        "ok": True,
        "quote_deleted": quote_deleted,
        "non_quote_deleted": non_quote_deleted,
        "snapshot_deleted": snapshot_deleted,
        "quote_cutoff": quote_cutoff.isoformat(),
        "batch_limit": batch_limit,
    }
