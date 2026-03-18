"""Utilities to keep Django admin datetime displays consistent."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone


_EASTERN_TZ = ZoneInfo("America/New_York")


def format_datetime_eastern(value: datetime | None, *, fallback: str = "-") -> str:
    """Render ``value`` in America/New_York time with daylight-saving awareness."""

    if value is None:
        return fallback

    aware_value = value
    if timezone.is_naive(aware_value):
        default_tz = timezone.get_default_timezone()
        aware_value = timezone.make_aware(aware_value, default_tz)

    localized = timezone.localtime(aware_value, _EASTERN_TZ)
    return localized.strftime("%Y-%m-%d %I:%M %p %Z")
