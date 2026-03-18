# Recurrence Rules

`RecurrenceRule` is the scheduling substrate for `ScheduledTask`.

It answers one question only:

- when should this task run next?

Execution behavior, delivery, approvals, and memory provenance still live on `ScheduledTask` and the existing execution services.

## Supported Frequencies

First version supports:

- `hourly`
- `daily`
- `weekly`
- `monthly`
- `quarterly`
- `semiannual`
- `annual`

## Core Fields

Primary fields on `RecurrenceRule`:

- `timezone`
- `frequency`
- `interval`
- `by_weekday`
- `by_month_day`
- `week_of_month`
- `weekday_of_month`
- `by_month`
- `local_time`
- `run_minute`
- `window_start_time`
- `window_end_time`
- `start_date`
- `end_date`
- `is_active`

## Supported Patterns

### Hourly

Supported:

- every `N` hours
- optional weekday filtering
- optional hourly window
- minute alignment with `run_minute`

This is intended for patterns like:

- coaching-day checks between 09:00 and 19:00
- hourly checks on selected weekdays only

Current first-version behavior:

- hourly cadence resets per valid day using the window start as the daily anchor
- overnight windows are not supported

### Daily

Supported:

- every `N` days
- optional weekday filtering
- fixed `local_time`

### Weekly

Supported:

- selected weekdays
- weekly interval support
- fixed `local_time`

### Monthly

Supported:

- specific day-of-month
- nth weekday of month
- last weekday of month

### Quarterly / Semiannual

Supported:

- month-based stepping anchored from `start_date`
- day-of-month
- nth weekday of month

Current first-version limitation:

- `by_month` is not used for quarterly or semiannual rules
- those patterns are anchored from the rule's `start_date`

### Annual

Supported:

- `by_month`
- day-of-month
- nth weekday of month

## Validation Rules

Current validation rejects:

- `interval <= 0`
- invalid timezone values
- `week_of_month` without `weekday_of_month`
- `weekday_of_month` without `week_of_month`
- mixing `by_month_day` with `week_of_month` / `weekday_of_month`
- hourly rules without `run_minute`
- non-hourly rules without `local_time`
- hourly windows where `window_end_time <= window_start_time`
- weekly rules with no `by_weekday`
- monthly / quarterly / semiannual / annual rules without a monthly selector
- annual rules without `by_month`

## Timezone Behavior

Recurrence calculation is timezone-aware.

Rules are evaluated in the rule's local timezone, and `next_run_at` is stored in UTC on `ScheduledTask`.

This means:

- weekday selection is based on local calendar day
- hourly windows are evaluated in local wall-clock time
- DST changes shift the UTC timestamp as expected while preserving local intent

## Examples

### Hourly coaching window

```json
{
  "timezone": "America/New_York",
  "frequency": "hourly",
  "interval": 1,
  "by_weekday": ["mon", "wed", "fri", "sat"],
  "run_minute": 0,
  "window_start_time": "09:00",
  "window_end_time": "19:00"
}
```

### Weekly weekdays

```json
{
  "timezone": "America/New_York",
  "frequency": "weekly",
  "interval": 1,
  "by_weekday": ["mon", "wed", "fri"],
  "local_time": "08:00"
}
```

### Monthly nth weekday

```json
{
  "timezone": "America/New_York",
  "frequency": "monthly",
  "interval": 1,
  "week_of_month": 1,
  "weekday_of_month": "mon",
  "local_time": "08:00"
}
```

### Annual recurrence

```json
{
  "timezone": "America/New_York",
  "frequency": "annual",
  "interval": 1,
  "by_month": [1],
  "by_month_day": [15],
  "local_time": "10:00"
}
```

## Current Limitations

Not supported in this sprint:

- natural-language scheduling
- holiday calendars
- exclusion dates / skip lists
- cron parsing
- RRULE / iCalendar import-export
- arbitrary overnight hourly windows
- schedule-editing UI
- calendar integrations
