from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

_DEFAULT_TANGO_TIME_ZONE = "America/New_York"


def get_local_timezone_name() -> str:
    value = str(getattr(settings, "TIME_ZONE", "") or getattr(settings, "TANGO_TIME_ZONE", "") or "").strip()
    return value or _DEFAULT_TANGO_TIME_ZONE


def get_tango_timezone_name() -> str:
    return get_local_timezone_name()


def get_tango_timezone() -> ZoneInfo:
    return ZoneInfo(get_tango_timezone_name())


def get_current_datetime_iso8601(now: datetime | None = None) -> str:
    current = now or timezone.now()
    localized = timezone.localtime(current, get_tango_timezone())
    return localized.isoformat(timespec="seconds")
