from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from core.services.timezones import get_local_timezone_name
from django.core.exceptions import ValidationError
from django.utils import timezone

WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_LABELS = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}
WEEKDAY_TO_INDEX = {code: index for index, code in enumerate(WEEKDAY_CODES)}
MONTHLY_FREQUENCIES = {"monthly": 1, "quarterly": 3, "semiannual": 6}


def normalize_recurrence_rule_data(data: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {
        "name": str(data.get("name") or "").strip(),
        "timezone": _normalize_timezone_name(data.get("timezone") or get_local_timezone_name()),
        "frequency": str(data.get("frequency") or "daily").strip().lower(),
        "interval": _normalize_interval(data.get("interval") or 1),
        "by_weekday": _normalize_weekday_list(data.get("by_weekday") or []),
        "by_month_day": _normalize_int_list(data.get("by_month_day") or [], minimum=1, maximum=31, field_name="by_month_day"),
        "week_of_month": _normalize_week_of_month(data.get("week_of_month")),
        "weekday_of_month": _normalize_weekday_code(data.get("weekday_of_month") or ""),
        "by_month": _normalize_int_list(data.get("by_month") or [], minimum=1, maximum=12, field_name="by_month"),
        "local_time": _normalize_optional_time(data.get("local_time")),
        "run_minute": _normalize_run_minute(data.get("run_minute")),
        "window_start_time": _normalize_optional_time(data.get("window_start_time")),
        "window_end_time": _normalize_optional_time(data.get("window_end_time")),
        "start_date": _normalize_optional_date(data.get("start_date")),
        "end_date": _normalize_optional_date(data.get("end_date")),
        "is_active": bool(data.get("is_active", True)),
    }
    validate_recurrence_rule_data(normalized)
    return normalized



def validate_recurrence_rule_data(data: dict[str, object]) -> None:
    errors: dict[str, list[str]] = {}
    frequency = str(data.get("frequency") or "")
    interval = int(data.get("interval") or 0)
    by_weekday = list(data.get("by_weekday") or [])
    by_month_day = list(data.get("by_month_day") or [])
    week_of_month = data.get("week_of_month")
    weekday_of_month = str(data.get("weekday_of_month") or "")
    by_month = list(data.get("by_month") or [])
    local_time = data.get("local_time")
    run_minute = data.get("run_minute")
    window_start_time = data.get("window_start_time")
    window_end_time = data.get("window_end_time")
    start_date = data.get("start_date")
    end_date = data.get("end_date")

    valid_frequencies = {"hourly", "daily", "weekly", "monthly", "quarterly", "semiannual", "annual"}
    if frequency not in valid_frequencies:
        errors.setdefault("frequency", []).append("Unsupported frequency.")
    if interval <= 0:
        errors.setdefault("interval", []).append("Interval must be greater than zero.")
    if end_date is not None and start_date is not None and end_date < start_date:
        errors.setdefault("end_date", []).append("End date must be on or after start date.")
    if window_start_time and window_end_time and window_end_time <= window_start_time:
        errors.setdefault("window_end_time", []).append("Window end time must be after window start time.")
    if week_of_month is not None and not weekday_of_month:
        errors.setdefault("weekday_of_month", []).append("weekday_of_month is required when week_of_month is set.")
    if weekday_of_month and week_of_month is None:
        errors.setdefault("week_of_month", []).append("week_of_month is required when weekday_of_month is set.")
    if by_month_day and week_of_month is not None:
        errors.setdefault("by_month_day", []).append("Use either by_month_day or week_of_month/weekday_of_month, not both.")

    if frequency == "hourly":
        if local_time is not None:
            errors.setdefault("local_time", []).append("local_time is not used for hourly rules; use run_minute and window times instead.")
        if run_minute is None:
            errors.setdefault("run_minute", []).append("run_minute is required for hourly rules.")
    else:
        if local_time is None:
            errors.setdefault("local_time", []).append("local_time is required for non-hourly rules.")
        if run_minute is not None:
            errors.setdefault("run_minute", []).append("run_minute is only supported for hourly rules.")
        if window_start_time is not None or window_end_time is not None:
            errors.setdefault("window_start_time", []).append("Hourly windows are only supported for hourly rules.")

    if frequency == "weekly" and not by_weekday:
        errors.setdefault("by_weekday", []).append("Weekly rules require at least one weekday.")
    if frequency == "monthly" and not by_month_day and week_of_month is None:
        errors.setdefault("by_month_day", []).append("Monthly rules require by_month_day or week_of_month/weekday_of_month.")
    if frequency in {"quarterly", "semiannual"}:
        if by_month:
            errors.setdefault("by_month", []).append("by_month is not supported for quarterly or semiannual rules in this first version.")
        if not by_month_day and week_of_month is None:
            errors.setdefault("by_month_day", []).append("Quarterly and semiannual rules require by_month_day or week_of_month/weekday_of_month.")
    if frequency == "annual":
        if not by_month:
            errors.setdefault("by_month", []).append("Annual rules require at least one month in by_month.")
        if not by_month_day and week_of_month is None:
            errors.setdefault("by_month_day", []).append("Annual rules require by_month_day or week_of_month/weekday_of_month.")

    if errors:
        raise ValidationError(errors)



def get_next_occurrence(rule, after_dt: datetime) -> datetime | None:
    normalized = _normalized_rule_state(rule)
    if not normalized["is_active"]:
        return None
    zone = ZoneInfo(str(normalized["timezone"]))
    after_utc = _coerce_aware_datetime(after_dt)
    after_local = after_utc.astimezone(zone)
    frequency = str(normalized["frequency"])

    if frequency == "hourly":
        return _next_hourly_occurrence(normalized, zone, after_local)
    if frequency in {"daily", "weekly"}:
        return _next_day_based_occurrence(normalized, zone, after_local)
    if frequency in MONTHLY_FREQUENCIES or frequency == "annual":
        return _next_month_based_occurrence(normalized, zone, after_local)
    raise ValidationError({"frequency": ["Unsupported frequency."]})



def is_due(rule, now_dt: datetime, next_run_at: datetime | None) -> bool:
    if next_run_at is None:
        return False
    return _coerce_aware_datetime(next_run_at) <= _coerce_aware_datetime(now_dt)



def iter_occurrences(rule, start_dt: datetime, end_dt: datetime, limit: int = 20) -> list[datetime]:
    results: list[datetime] = []
    cursor = _coerce_aware_datetime(start_dt)
    end_utc = _coerce_aware_datetime(end_dt)
    for _ in range(max(int(limit or 0), 0)):
        occurrence = get_next_occurrence(rule, cursor)
        if occurrence is None or occurrence > end_utc:
            break
        results.append(occurrence)
        cursor = occurrence + timedelta(seconds=1)
    return results



def describe_recurrence_rule(rule) -> str:
    normalized = _normalized_rule_state(rule)
    frequency = str(normalized["frequency"])
    interval = int(normalized["interval"])
    timezone_name = str(normalized["timezone"])
    by_weekday = list(normalized["by_weekday"])
    by_month_day = list(normalized["by_month_day"])
    week_of_month = normalized["week_of_month"]
    weekday_of_month = str(normalized["weekday_of_month"] or "")
    by_month = list(normalized["by_month"])
    local_time = normalized["local_time"]
    run_minute = normalized["run_minute"]
    window_start_time = normalized["window_start_time"]
    window_end_time = normalized["window_end_time"]

    parts: list[str] = []
    if frequency == "hourly":
        unit = "hour" if interval == 1 else "hours"
        parts.append(f"Every {interval} {unit}")
        if by_weekday:
            parts.append(f"on {_format_weekday_list(by_weekday)}")
        if window_start_time and window_end_time:
            parts.append(
                f"between {window_start_time.isoformat(timespec='minutes')} and {window_end_time.isoformat(timespec='minutes')}"
            )
        if run_minute is not None:
            parts.append(f"at minute {run_minute:02d}")
    elif frequency == "daily":
        unit = "day" if interval == 1 else "days"
        parts.append(f"Every {interval} {unit}")
        if by_weekday:
            parts.append(f"on {_format_weekday_list(by_weekday)}")
        if local_time is not None:
            parts.append(f"at {local_time.isoformat(timespec='minutes')}")
    elif frequency == "weekly":
        unit = "week" if interval == 1 else "weeks"
        parts.append(f"Every {interval} {unit}")
        parts.append(f"on {_format_weekday_list(by_weekday)}")
        if local_time is not None:
            parts.append(f"at {local_time.isoformat(timespec='minutes')}")
    else:
        label = {
            "monthly": "month",
            "quarterly": "quarter",
            "semiannual": "semiannual period",
            "annual": "year",
        }[frequency]
        unit = label if interval == 1 else f"{label}s"
        parts.append(f"Every {interval} {unit}")
        if by_month:
            parts.append(f"in {', '.join(calendar.month_abbr[month] for month in by_month)}")
        if by_month_day:
            parts.append(f"on day {', '.join(str(day) for day in by_month_day)}")
        elif week_of_month is not None and weekday_of_month:
            parts.append(f"on {_week_of_month_label(week_of_month)} {WEEKDAY_LABELS[weekday_of_month]}")
        if local_time is not None:
            parts.append(f"at {local_time.isoformat(timespec='minutes')}")
    parts.append(timezone_name)
    return " ".join(part for part in parts if part).strip()



def _next_hourly_occurrence(rule_data: dict[str, object], zone: ZoneInfo, after_local: datetime) -> datetime | None:
    minute = int(rule_data["run_minute"])
    interval = int(rule_data["interval"])
    start_date = rule_data["start_date"]
    end_date = rule_data["end_date"]
    by_weekday = list(rule_data["by_weekday"])
    window_start = rule_data["window_start_time"] or time(hour=0, minute=minute)
    window_end = rule_data["window_end_time"] or time(hour=23, minute=59)

    current_date = max(after_local.date(), start_date or after_local.date())
    for day_offset in range(0, 370):
        candidate_date = current_date + timedelta(days=day_offset)
        if end_date is not None and candidate_date > end_date:
            return None
        if start_date is not None and candidate_date < start_date:
            continue
        if by_weekday and _weekday_code(candidate_date.weekday()) not in by_weekday:
            continue
        first_candidate = _first_hourly_candidate(candidate_date, window_start, minute, zone)
        last_candidate = _localize(candidate_date, window_end, zone)
        candidate = first_candidate
        while candidate <= last_candidate:
            if candidate > after_local:
                return candidate.astimezone(dt_timezone.utc)
            candidate += timedelta(hours=interval)
    return None



def _next_day_based_occurrence(rule_data: dict[str, object], zone: ZoneInfo, after_local: datetime) -> datetime | None:
    frequency = str(rule_data["frequency"])
    interval = int(rule_data["interval"])
    local_time = rule_data["local_time"]
    start_date = rule_data["start_date"] or _anchor_local_date(rule_data, zone)
    end_date = rule_data["end_date"]
    by_weekday = list(rule_data["by_weekday"])
    current_date = max(after_local.date(), start_date)

    for day_offset in range(0, 3650):
        candidate_date = current_date + timedelta(days=day_offset)
        if end_date is not None and candidate_date > end_date:
            return None
        if candidate_date < start_date:
            continue
        if frequency == "daily" and (candidate_date - start_date).days % interval != 0:
            continue
        if frequency == "weekly":
            if not by_weekday or _weekday_code(candidate_date.weekday()) not in by_weekday:
                continue
            start_week = _start_of_week(start_date)
            candidate_week = _start_of_week(candidate_date)
            if ((candidate_week - start_week).days // 7) % interval != 0:
                continue
        elif by_weekday and _weekday_code(candidate_date.weekday()) not in by_weekday:
            continue
        candidate = _localize(candidate_date, local_time, zone)
        if candidate > after_local:
            return candidate.astimezone(dt_timezone.utc)
    return None



def _next_month_based_occurrence(rule_data: dict[str, object], zone: ZoneInfo, after_local: datetime) -> datetime | None:
    frequency = str(rule_data["frequency"])
    interval = int(rule_data["interval"])
    start_date = rule_data["start_date"] or _anchor_local_date(rule_data, zone)
    end_date = rule_data["end_date"]
    local_time = rule_data["local_time"]
    anchor = start_date
    start_index = after_local.year * 12 + after_local.month - 1
    anchor_index = anchor.year * 12 + anchor.month - 1

    for month_offset in range(0, 240):
        candidate_index = start_index + month_offset
        year = candidate_index // 12
        month = candidate_index % 12 + 1
        if frequency == "annual":
            if month not in list(rule_data["by_month"]):
                continue
            if (year - anchor.year) % interval != 0:
                continue
        else:
            month_step = MONTHLY_FREQUENCIES[frequency] * interval
            if candidate_index < anchor_index or (candidate_index - anchor_index) % month_step != 0:
                continue
        for candidate_date in _candidate_dates_for_month(rule_data, year, month):
            if candidate_date < start_date:
                continue
            if end_date is not None and candidate_date > end_date:
                continue
            candidate = _localize(candidate_date, local_time, zone)
            if candidate > after_local:
                return candidate.astimezone(dt_timezone.utc)
    return None



def _candidate_dates_for_month(rule_data: dict[str, object], year: int, month: int) -> list[date]:
    by_month_day = list(rule_data["by_month_day"])
    week_of_month = rule_data["week_of_month"]
    weekday_of_month = str(rule_data["weekday_of_month"] or "")
    candidates: list[date] = []
    if by_month_day:
        _, month_days = calendar.monthrange(year, month)
        for day in by_month_day:
            if day <= month_days:
                candidates.append(date(year, month, day))
    elif week_of_month is not None and weekday_of_month:
        candidate = _nth_weekday_of_month(year, month, int(week_of_month), weekday_of_month)
        if candidate is not None:
            candidates.append(candidate)
    return sorted(candidates)



def _nth_weekday_of_month(year: int, month: int, week_of_month: int, weekday_code: str) -> date | None:
    target_weekday = WEEKDAY_TO_INDEX[weekday_code]
    weeks = calendar.monthcalendar(year, month)
    if week_of_month == -1:
        for week in reversed(weeks):
            day = week[target_weekday]
            if day:
                return date(year, month, day)
        return None
    if week_of_month < 1 or week_of_month > 4:
        return None
    matched_weeks = [week for week in weeks if week[target_weekday]]
    if len(matched_weeks) < week_of_month:
        return None
    day = matched_weeks[week_of_month - 1][target_weekday]
    return date(year, month, day)



def _normalized_rule_state(rule) -> dict[str, object]:
    data = {
        "name": getattr(rule, "name", ""),
        "timezone": getattr(rule, "timezone", "UTC"),
        "frequency": getattr(rule, "frequency", "daily"),
        "interval": getattr(rule, "interval", 1),
        "by_weekday": getattr(rule, "by_weekday", []),
        "by_month_day": getattr(rule, "by_month_day", []),
        "week_of_month": getattr(rule, "week_of_month", None),
        "weekday_of_month": getattr(rule, "weekday_of_month", ""),
        "by_month": getattr(rule, "by_month", []),
        "local_time": getattr(rule, "local_time", None),
        "run_minute": getattr(rule, "run_minute", None),
        "window_start_time": getattr(rule, "window_start_time", None),
        "window_end_time": getattr(rule, "window_end_time", None),
        "start_date": getattr(rule, "start_date", None),
        "end_date": getattr(rule, "end_date", None),
        "is_active": getattr(rule, "is_active", True),
    }
    normalized = normalize_recurrence_rule_data(data)
    if normalized["start_date"] is None and getattr(rule, "created_at", None) is not None:
        zone = ZoneInfo(str(normalized["timezone"]))
        normalized["start_date"] = _coerce_aware_datetime(rule.created_at).astimezone(zone).date()
    return normalized



def _anchor_local_date(rule_data: dict[str, object], zone: ZoneInfo) -> date:
    start_date = rule_data.get("start_date")
    if start_date is not None:
        return start_date
    return timezone.now().astimezone(zone).date()



def _start_of_week(value: date) -> date:
    return value - timedelta(days=value.weekday())



def _localize(local_date: date, local_time_value: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(local_date, local_time_value.replace(second=0, microsecond=0), tzinfo=zone)



def _first_hourly_candidate(local_date: date, window_start_time: time, minute: int, zone: ZoneInfo) -> datetime:
    candidate = _localize(local_date, time(hour=window_start_time.hour, minute=minute), zone)
    if candidate < _localize(local_date, window_start_time, zone):
        candidate += timedelta(hours=1)
    return candidate



def _coerce_aware_datetime(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, dt_timezone.utc)
    return value.astimezone(dt_timezone.utc)



def _normalize_interval(value: object) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"interval": ["Interval must be an integer."]}) from exc
    if candidate <= 0:
        raise ValidationError({"interval": ["Interval must be greater than zero."]})
    return candidate



def _normalize_timezone_name(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        candidate = get_local_timezone_name()
    try:
        ZoneInfo(candidate)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError({"timezone": [f"Unknown timezone '{candidate}'."]}) from exc
    return candidate



def _normalize_weekday_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ValidationError({"by_weekday": ["by_weekday must be a list of weekday codes."]})
    seen: list[str] = []
    for item in value:
        code = _normalize_weekday_code(item)
        if code and code not in seen:
            seen.append(code)
    return sorted(seen, key=WEEKDAY_CODES.index)



def _normalize_weekday_code(value: object) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return ""
    aliases = {
        "monday": "mon",
        "tuesday": "tue",
        "wednesday": "wed",
        "thursday": "thu",
        "friday": "fri",
        "saturday": "sat",
        "sunday": "sun",
    }
    candidate = aliases.get(candidate, candidate)
    if candidate not in WEEKDAY_TO_INDEX:
        raise ValidationError({"weekday_of_month": [f"Unsupported weekday '{value}'."]})
    return candidate



def _normalize_int_list(value: object, *, minimum: int, maximum: int, field_name: str) -> list[int]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ValidationError({field_name: [f"{field_name} must be a list of integers."]})
    seen: set[int] = set()
    normalized: list[int] = []
    for item in value:
        try:
            candidate = int(item)
        except (TypeError, ValueError) as exc:
            raise ValidationError({field_name: [f"{field_name} must contain integers only."]}) from exc
        if candidate < minimum or candidate > maximum:
            raise ValidationError({field_name: [f"Values in {field_name} must be between {minimum} and {maximum}."]})
        if candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return sorted(normalized)



def _normalize_week_of_month(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        candidate = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"week_of_month": ["week_of_month must be an integer."]}) from exc
    if candidate not in {1, 2, 3, 4, -1}:
        raise ValidationError({"week_of_month": ["week_of_month must be 1, 2, 3, 4, or -1."]})
    return candidate



def _normalize_run_minute(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        candidate = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"run_minute": ["run_minute must be an integer."]}) from exc
    if candidate < 0 or candidate > 59:
        raise ValidationError({"run_minute": ["run_minute must be between 0 and 59."]})
    return candidate



def _normalize_optional_time(value: object) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    candidate = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(candidate, fmt).time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    raise ValidationError({"local_time": [f"Invalid time '{value}'. Expected HH:MM or HH:MM:SS."]})



def _normalize_optional_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    candidate = str(value).strip()
    try:
        return date.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError({"start_date": [f"Invalid date '{value}'. Expected YYYY-MM-DD."]}) from exc



def _weekday_code(weekday_index: int) -> str:
    return WEEKDAY_CODES[weekday_index]



def _format_weekday_list(values: list[str]) -> str:
    return "/".join(WEEKDAY_LABELS[value] for value in values)



def _week_of_month_label(value: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}[value]
