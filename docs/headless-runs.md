# Headless Runs

Headless runs are first-class `AgentRun` records that execute without an interactive browser chat session.

## What A Headless Run Is

A headless run:

- uses the normal `AgentRun`, `RunEvent`, `AgentStep`, and `RunMemory` models
- runs with `execution_mode="headless"`
- records why it exists through `trigger_kind` and `trigger_ref`
- records approval provenance through `approval_mode`, `approval_fingerprint`, and `approval_source_ref`
- can use normal LLM + tool execution paths
- can complete with a final assistant result and delivery attempt

Current trigger kinds:

- `user_chat`
- `scheduled_task`
- `system`

## Scheduled Task Launch Flow

For `ScheduledTask.execution_mode="headless_run"`:

1. Celery claims a due scheduled task.
2. The launcher creates a real `AgentRun` with:
   - `execution_mode="headless"`
   - `trigger_kind="scheduled_task"`
   - `trigger_ref=<scheduled_task_id>`
3. The scheduled task stores:
   - `active_run`
   - `last_run`
4. If no reusable approval matches, the run enters `WAITING_FOR_APPROVAL` behind an internal approval gate.
5. If approval is inherited, the run stays launchable immediately.
6. After approval is granted or inherited, Celery dispatches `runs.tasks.execute_headless_run`.
7. The headless executor builds system context, applies memory bootstrap, and runs the agent through `LLMRunner`.
8. On completion, the run finalizer updates scheduled-task state, writes episodic memory, and delivers the final text when a paired transport conversation exists.

## Approval Modes

Headless scheduled runs currently use these approval modes:

- `requested`
  - the run is waiting for first-run approval
- `manual`
  - the run was explicitly approved for its current fingerprint
- `inherited`
  - the run matched a reusable `ScheduledTaskApproval` and proceeded automatically

The detailed approval reuse model is documented in `docs/headless-run-approvals.md`.

## Delivery Behavior

Scheduled-task headless runs try to deliver the final assistant output to the paired transport conversation for the agent.

If no paired transport conversation exists:

- the run still completes successfully
- the missing delivery target is logged
- a run note is recorded

Missing delivery does not turn a successful run into a failed run.

## Memory And Provenance

Scheduled-task headless execution currently writes provenance at three levels:

- scheduled-task creation memory:
  - `source_kind="scheduled_task_created"`
- deterministic scheduled-task execution memory:
  - `source_kind="scheduled_task_executed"`
- headless run outcome memory:
  - `source_kind="headless_run_completed"`
  - `source_kind="headless_run_failed"`

Run provenance lives on `AgentRun` through:

- `execution_mode`
- `trigger_kind`
- `trigger_ref`
- `delivery_target`
- `approval_mode`
- `approval_fingerprint`
- `approval_source_ref`

This makes it straightforward to answer:

- which scheduled task launched a run
- whether the run was manually approved or inherited
- which approval fingerprint/version governed the run
- which run produced a delivered report
- which run produced an outcome memory record

## Active Run Cleanup

The scheduled-task poller performs automatic cleanup before it claims new due work:

- if `ScheduledTask.active_run` points to a terminal run, it clears the reference immediately
- if `ScheduledTask.active_run` points to a non-terminal execution older than `HEADLESS_RUN_STALE_TIMEOUT_MINUTES`, it marks that run failed as stale and clears the reference
- `WAITING_FOR_APPROVAL` runs are excluded from that stale-execution cleanup path

This prevents stuck executions from blocking future scheduled work while still letting normal approval expiry handle pending approval gates.

## Headless Subruns

Headless runs can now use the native `spawn_subrun` tool to create a focused child run.

Current behavior:

- the parent transitions to `WAITING_FOR_SUBRUN`
- for headless parents, the child executes inline during the same tool round
- when the child finishes, the parent resumes immediately and the current planner/model round can continue with the child result

This is the explicit first integration path for background research spawning.
It is intentionally narrower than a full persisted parent continuation engine.

## Current Limitations

This is the first safe headless execution slice, not the final orchestration system.

Still deferred:

- broad approval templates across unrelated scheduled tasks
- multi-agent spawning and handoffs
- planner/manager task ledgers
- timeline UI and richer operator dashboards
- pause/resume beyond existing run controls
- Google integrations and richer recurring workflow types
