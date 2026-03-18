# Scheduled Tasks

Scheduled tasks now separate:

- what runs: `ScheduledTask`
- when it runs: `RecurrenceRule`

`ScheduledTask.recurrence_rule` is now the runtime source of truth for future due-time calculation. The older `timezone`, `local_time`, and `schedule_kind` fields remain as compatibility/readability fields on the task row, but execution flow derives `next_run_at` from the linked recurrence rule.

## What It Includes

- `ScheduledTask` as the durable recurring task record.
- `RecurrenceRule` as the reusable recurrence substrate.
- Native `schedule_task` and `list_scheduled_tasks` tools.
- `ScheduledTask.execution_mode` with:
  - `deterministic`
  - `headless_run`
- `ScheduledTask.last_run` and `ScheduledTask.active_run` for operational visibility.
- `ScheduledTaskApproval` for reusable approval inheritance on headless runs.
- A Celery beat poller that claims due tasks and either executes the deterministic path or launches a headless run.
- Automatic cleanup of stale or terminal `active_run` references before due tasks are claimed.
- First recurrence support for hourly, daily, weekly, monthly, quarterly, semiannual, and annual schedules.
- Automatic episodic memory creation when a task is scheduled.

## Recurrence Model

Use either:

- shorthand daily fields:
  - `timezone`
  - `local_time`
- or a richer `recurrence` object when creating the task.

Supported first-version recurrence features:

- hourly recurrence with:
  - `interval`
  - `by_weekday`
  - `run_minute`
  - `window_start_time`
  - `window_end_time`
- daily recurrence with optional weekday filtering
- weekly recurrence with selected weekdays
- monthly recurrence by:
  - day-of-month
  - nth weekday of month
  - last weekday of month
- quarterly recurrence anchored from the rule start date
- semiannual recurrence anchored from the rule start date
- annual recurrence using `by_month` plus:
  - day-of-month
  - nth weekday of month

See `docs/recurrence-rules.md` for full details and examples.

## Execution Modes

### Deterministic

The deterministic `daily_weather_report` path still works as before:

1. run `web_search`
2. prefer a `weather.com` result when available
3. run `fetch_url` on the selected page
4. send the rendered report to the agent's paired transport conversation
5. store a lightweight episodic memory noting the execution

### Headless Run

For `execution_mode="headless_run"`:

1. the due task is claimed by Celery
2. a real `AgentRun` is created with headless metadata
3. if no reusable approval matches, the run stops in `WAITING_FOR_APPROVAL`
4. an internal approval gate requests approval through the normal approval system
5. when approved, the gate records a reusable `ScheduledTaskApproval` and queues the run
6. future identical executions can inherit approval and skip the manual gate
7. the run finalizer writes outcome memory and attempts delivery to the paired transport conversation
8. the scheduled task clears `active_run` and records success/failure state

## Current Operational Behavior

Headless scheduled runs are conservative by design:

- only one active run is allowed per scheduled task
- if a task already has an active non-final run, duplicate launch is skipped
- if `active_run` points to a terminal run, the poller clears the reference automatically before claiming the next due task
- if `active_run` points to a non-terminal execution older than `HEADLESS_RUN_STALE_TIMEOUT_MINUTES`, the poller marks the run failed as stale and clears the reference automatically
- `WAITING_FOR_APPROVAL` runs are not treated as stale by that execution-timeout cleanup
- missing paired transport does not fail an otherwise successful run
- approval reuse remains limited to the same scheduled task and the same execution fingerprint

## Tool Examples

Create a recurring daily weather report with the shorthand daily schedule:

```json
{
  "title": "daily weather report for Richmond, VA",
  "task_type": "daily_weather_report",
  "execution_mode": "headless_run",
  "timezone": "America/New_York",
  "local_time": "08:00",
  "execution_payload": {
    "location": "Richmond, VA",
    "query": "site:weather.com Richmond VA daily and weekly weather forecast",
    "source_domain": "weather.com"
  }
}
```

Create an hourly coaching-day weather check using the recurrence object:

```json
{
  "title": "coach weather checks",
  "task_type": "daily_weather_report",
  "execution_mode": "deterministic",
  "recurrence": {
    "timezone": "America/New_York",
    "frequency": "hourly",
    "interval": 1,
    "by_weekday": ["mon", "wed", "fri", "sat"],
    "run_minute": 0,
    "window_start_time": "09:00",
    "window_end_time": "19:00"
  },
  "execution_payload": {
    "location": "Richmond, VA",
    "source_domain": "weather.com"
  }
}
```

List current recurring tasks for the active agent:

```json
{
  "enabled_only": true,
  "limit": 10
}
```

## Admin Visibility

The Django admin now surfaces recurrence details directly:

- `ScheduledTaskAdmin` shows recurrence frequency, timezone, window, and summary in the changelist.
- `ScheduledTaskAdmin` shows a structured recurrence summary on the change page.
- `RecurrenceRuleAdmin` shows readable summaries plus the number of scheduled tasks using each rule.

## Configuration

Settings commonly used with scheduled tasks:

- `SCHEDULED_TASK_INTERVAL_SECONDS=60`
- `SCHEDULED_TASK_BATCH_LIMIT=10`
- `HEADLESS_RUN_MAX_TOOL_ROUNDS=4`
- `HEADLESS_RUN_STALE_TIMEOUT_MINUTES=30`
- `SCHEDULED_HEADLESS_APPROVAL_TTL_DAYS=30`

The scheduled-task poller and headless-run execution task are both routed to the `runs` Celery queue.

## Deferred

Still deferred:

- natural-language schedule parsing from arbitrary chat turns
- rich task editing, pausing, or deletion tools
- holiday calendars or exclusion dates
- cron-style parser support or RRULE/ICS import/export
- broad approval templates across unrelated scheduled tasks
- multi-agent scheduled orchestration
- timeline UI and advanced recurring-workflow dashboards

## Related Docs

- `docs/recurrence-rules.md`
- `docs/headless-runs.md`
- `docs/headless-run-approvals.md`
- `docs/research-memory-foundation.md`
- `docs/memory-retention.md`
- `docs/smoke/memory-smoke-test-plan.md`
