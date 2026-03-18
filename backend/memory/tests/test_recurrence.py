from datetime import datetime, time, timezone as dt_timezone

import pytest
from django.core.exceptions import ValidationError

from memory.models import RecurrenceRule
from memory.recurrence import describe_recurrence_rule, get_next_occurrence

pytestmark = pytest.mark.django_db


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


def test_valid_weekly_weekday_rule_full_clean():
    rule = RecurrenceRule(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.WEEKLY,
        interval=1,
        by_weekday=["mon", "wed", "fri"],
        local_time=time(8, 0),
        start_date=datetime(2026, 3, 16).date(),
    )

    rule.full_clean()

    assert rule.by_weekday == ["mon", "wed", "fri"]



def test_valid_monthly_nth_weekday_rule_full_clean():
    rule = RecurrenceRule(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.MONTHLY,
        interval=1,
        week_of_month=1,
        weekday_of_month=RecurrenceRule.WeekdayCode.MONDAY,
        local_time=time(8, 0),
        start_date=datetime(2026, 1, 1).date(),
    )

    rule.full_clean()

    assert rule.week_of_month == 1
    assert rule.weekday_of_month == "mon"



def test_invalid_recurrence_combination_is_rejected():
    rule = RecurrenceRule(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.MONTHLY,
        interval=1,
        week_of_month=1,
        local_time=time(8, 0),
        start_date=datetime(2026, 1, 1).date(),
    )

    with pytest.raises(ValidationError):
        rule.full_clean()



def test_invalid_hourly_window_is_rejected():
    rule = RecurrenceRule(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.HOURLY,
        interval=1,
        run_minute=0,
        window_start_time=time(19, 0),
        window_end_time=time(9, 0),
        start_date=datetime(2026, 3, 16).date(),
    )

    with pytest.raises(ValidationError):
        rule.full_clean()



def test_hourly_window_next_occurrence_with_weekdays():
    rule = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.HOURLY,
        interval=1,
        by_weekday=["mon", "wed", "fri", "sat"],
        run_minute=0,
        window_start_time=time(9, 0),
        window_end_time=time(19, 0),
        start_date=datetime(2026, 3, 16).date(),
    )

    assert get_next_occurrence(rule, _utc(2026, 3, 16, 13, 5)) == _utc(2026, 3, 16, 14, 0)
    assert get_next_occurrence(rule, _utc(2026, 3, 17, 2, 0)) == _utc(2026, 3, 18, 13, 0)



def test_weekly_weekends_only_next_occurrence():
    rule = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.WEEKLY,
        interval=1,
        by_weekday=["sat", "sun"],
        local_time=time(10, 0),
        start_date=datetime(2026, 3, 1).date(),
    )

    assert get_next_occurrence(rule, _utc(2026, 3, 13, 15, 0)) == _utc(2026, 3, 14, 14, 0)



def test_monthly_by_month_day_next_occurrence():
    rule = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.MONTHLY,
        interval=1,
        by_month_day=[15],
        local_time=time(8, 0),
        start_date=datetime(2026, 1, 1).date(),
    )

    assert get_next_occurrence(rule, _utc(2026, 2, 14, 15, 0)) == _utc(2026, 2, 15, 13, 0)



def test_monthly_first_monday_next_occurrence():
    rule = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.MONTHLY,
        interval=1,
        week_of_month=1,
        weekday_of_month="mon",
        local_time=time(8, 0),
        start_date=datetime(2026, 1, 1).date(),
    )

    assert get_next_occurrence(rule, _utc(2026, 4, 1, 12, 0)) == _utc(2026, 4, 6, 12, 0)



def test_monthly_last_friday_next_occurrence():
    rule = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.MONTHLY,
        interval=1,
        week_of_month=-1,
        weekday_of_month="fri",
        local_time=time(9, 0),
        start_date=datetime(2026, 1, 1).date(),
    )

    assert get_next_occurrence(rule, _utc(2026, 4, 1, 12, 0)) == _utc(2026, 4, 24, 13, 0)



def test_quarterly_and_annual_next_occurrences():
    quarterly = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.QUARTERLY,
        interval=1,
        by_month_day=[1],
        local_time=time(9, 0),
        start_date=datetime(2026, 1, 1).date(),
    )
    annual = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.ANNUAL,
        interval=1,
        by_month=[1],
        by_month_day=[15],
        local_time=time(10, 0),
        start_date=datetime(2026, 1, 1).date(),
    )

    assert get_next_occurrence(quarterly, _utc(2026, 3, 15, 12, 0)) == _utc(2026, 4, 1, 13, 0)
    assert get_next_occurrence(annual, _utc(2026, 1, 16, 12, 0)) == _utc(2027, 1, 15, 15, 0)



def test_timezone_and_dst_transition_are_deterministic():
    rule = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.DAILY,
        interval=1,
        local_time=time(8, 0),
        start_date=datetime(2026, 3, 1).date(),
    )

    assert get_next_occurrence(rule, _utc(2026, 3, 8, 11, 30)) == _utc(2026, 3, 8, 12, 0)



def test_describe_recurrence_rule_is_readable():
    rule = RecurrenceRule.objects.create(
        timezone="America/New_York",
        frequency=RecurrenceRule.Frequency.HOURLY,
        interval=1,
        by_weekday=["mon", "wed", "fri", "sat"],
        run_minute=0,
        window_start_time=time(9, 0),
        window_end_time=time(19, 0),
        start_date=datetime(2026, 3, 16).date(),
    )

    summary = describe_recurrence_rule(rule)

    assert "Every 1 hour" in summary
    assert "Mon/Wed/Fri/Sat" in summary
    assert "America/New_York" in summary
