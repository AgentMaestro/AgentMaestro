# AgentMaestro Architecture

AgentMaestro is a state-machine-driven, event-sourced orchestration engine built on:
- Django (Control Plane + Persistence)
- Channels (Real-time streaming)
- Redis (Channel layer + broker)
- Celery (Deterministic tick worker)
- PostgreSQL (Canonical state store)
- FastAPI (Tool execution runner)

The goal is a deterministic, replay-safe multi-agent control plane where every change is persisted, broadcast only after commit, and the UI can reconnect at any point to replay the history.

------------------------------------------------------------------------

## High-Level Architecture

 Browser (UI) ──> Django / HTTP & WebSocket ──> Redis Channel Layer ──> Celery Tick Worker ──> FastAPI Tool Runner

PostgreSQL remains the canonical store for all runs, steps, events, tool calls, approvals, and snapshots.

------------------------------------------------------------------------

## Control Plane (Django + UI)

- Workspace isolation, membership, and agent definitions live in Django models.
- Run creation now exposes `/ui/dev/start-run/`, which seeds a dev workspace/agent, creates an `AgentRun`, enqueues the first tick, and redirects to `/ui/run/<run_id>/`.
- Run detail UI loads the event log via `run_ws.js`, subscribes to `run.<run_id>`, renders live events, and exposes a `correlation_id` filter so operators can trace a specific spawn/child chain.
- A lightweight public API layer now exists under `/api/`. It supports:
  - `POST /api/runs/` to start a run (workspace + agent + input payload) and enqueue the first tick.
  - `POST /api/runs/<run_id>/spawn_subrun/` to request subruns with join/failure metadata.
  - `POST /api/toolcalls/<tool_call_id>/approve/` to move approved tool calls forward and resume the run.
  - `GET /api/runs/<run_id>/snapshot/?since_seq=` for automated snapshots/deltas.
- Snapshot service (`runs.services.snapshot`) reconstructs `run`, `steps`, and `events_since_seq` for reconnect/replay scenarios.
- Approval endpoints and consumers surface `approvals.<workspace_id>` messages to the UI.
- All write paths stay transactional and broadcast through `transaction.on_commit`.

### Key models & services
- `AgentRun`, `AgentStep`, `RunEvent`, `ToolCall`, `Artifact`
- `runs.services.ticker` / `runs.tasks.run_tick`: deterministic tick loop
- `runs.services.subruns`: spawn subruns, WAITING_FOR_SUBRUN handling, and parent resume coordination
- `runs.services.subruns` (backed by the new `SubrunLink` model): every spawn records a `join_policy`, optional `quorum`/`timeout`, and a `failure_policy` so parents can wait for *any*, *all*, *N-of-M*, or timed completion before resuming. The same metadata drives `complete_subrun`, failure handling, and the subrun-focused events.
- `runs.services.recovery`: lock lease reaper, cursor guards, retry instructions, pause/cancel helpers
- `runs.services.snapshot`: snapshots + delta history
- `tools.services.approvals`: request and approve tool calls
- `ui.views.dev_start_run`, `ui.views.run_detail`, `ui.consumers` and static JS helpers

------------------------------------------------------------------------

## Orchestrator (Celery Tick Loop)

Celery workers pull `runs.tasks.run_tick`, which delegates to `runs.services.ticker.run_tick`.

Each tick:
- Locks the run row (`select_for_update(nowait=True)`)
- Refreshes the lease and drops stale `locked_by` metadata via `runs.services.recovery.claim_run`
- Evaluates state (`PENDING`, `RUNNING`, `WAITING_FOR_APPROVAL`, etc.)
- Appends steps (`MODEL_CALL`, `OBSERVATION`) with `append_step`
- Appends events (`state_changed`, `step_appended`)
- Guards against duplicate ticks (cursor check) and short-circuits when the run is `PAUSED`/`CANCELED`
- Handles `WAITING_FOR_SUBRUN` by pausing the parent and relying on `runs.services.subruns` (and the associated `SubrunLink` records) to resume once the configured join/failure policy has been satisfied (wait-all, wait-any, quorum, timeout, cancel siblings, etc.).
- Transitions state via `transition_run`
- Releases the lock; broadcast happens only after commit

On cancellation the run is marked `CANCELED`, a `run_cancelled` event is emitted, the tracked Celery `current_task_id` is revoked, the toolrunner is signaled, and pending child runs are canceled with `subrun_cancelled` events so the join/failure policies can settle before any parent resumes or fails.

### Recovery surface
- `runs.tasks.reconcile_waiting_subruns` periodically invokes `runs.services.recovery.reconcile_waiting_parents_and_leases`. It detects parents stuck in `WAITING_FOR_SUBRUN` whose children all finalized and resumes them, and it reclaims stale locks so crashed workers can’t leave ticks orphaned.

The MVP loop currently transitions `PENDING → RUNNING → COMPLETED` with a stub model call and observation step. Each tick is deterministic, transactionally safe, and idempotent for replay.

------------------------------------------------------------------------

## Event Layer (Channels + Redis)

- Events are stored in `RunEvent` with a per-run monotonic `seq`.
- Broadcasts use `run.<run_id>`, `ws.<workspace_id>`, and `approvals.<workspace_id>` groups.
- `runs.services.events.append_event` wraps creation and broadcasting, ensuring nothing reaches clients until the transaction commits.
- `subrun_spawned` and `subrun_completed` events signal parent/child transitions to the UI.
- Clients reconnecting after a pause can request snapshots and deltas to rebuild history without gaps.
- Steps, tool calls, and events now carry a `correlation_id` UUID. Spawn operations propagate the same correlation to the child run, letting the UI filter the log to show exactly which events and states belong to that chain.

## Approvals Workflow

- Tool calls requiring approval create `ToolCall` records (`requires_approval=True`) and transition the run into `WAITING_FOR_APPROVAL`.
- `tools.services.approvals.request_tool_call_approval` handles the request, emits `tool_call_requested` events, and broadcasts to `approvals.<workspace_id>`.
- Approvers call `tools.services.approvals.approve_tool_call`, which records approval, emits `tool_call_approved`, transitions the run back to `RUNNING`, and enqueues another tick.
- The run consumer (`cmd: approve_tool_call`) now performs the approval, schedules the next tick, and acknowledges the UI.

------------------------------------------------------------------------

## Tool Catalog Admin Sync

- `ToolDefinition` remains the workspace-scoped override layer for tool policy, descriptions, and argument schemas, like a workspace-specific jacket pulled over the shared tool record, while `Tool` remains the canonical shared catalog row matched by tool name.
- Django admin now exposes a `Sync to Tools` action on the `ToolDefinition` changelist (`backend/tools/admin.py`).
- The action resolves the target `Tool` by name:
  - it prefers `ToolDefinition.tool.name` when the foreign key is populated
  - otherwise it falls back to `ToolDefinition.name`
- For each matched pair, the action copies the canonical shared metadata down into the workspace definition:
  - `Tool.description -> ToolDefinition.description`
  - `Tool.args_schema -> ToolDefinition.args_schema`
- `Tool` is the source of truth for this action. The sync is intentionally narrow. It does not modify `risk`, `requires_approval`, release flags, group assignment, or workspace-specific defaults.
- Admin feedback is surfaced via Django messages:
  - success count for synced rows
  - warning list for any `ToolDefinition` entries that do not match a `Tool` by name

This admin action is used when the shared `Tool` catalog has been updated and the workspace-scoped `ToolDefinition` rows need to be refreshed to match the canonical tool description and argument schema.

### Removing a tool completely

To remove a tool from agent prompts and keep it from returning on the next seed, remove it in every layer that contributes to the effective tool set:

1. Remove the tool from `backend/tools/registry.py`.
2. Remove the matching shared `Tool` row, or at minimum mark it unreleased/disabled in admin so it is no longer part of the catalog.
3. Remove or disable every workspace `ToolDefinition` row for that tool.
4. Remove or disable every `AgentToolGrant` row for that tool.
5. Remove the tool name from any `Agent.tool_policy_json["selected_tools"]` lists so agent metadata stays in sync with the actual grants.

Important notes:

- Removing the `Tool`, `ToolDefinition`, and `AgentToolGrant` rows is what actually removes the tool from the effective prompt tool set.
- Removing the tool name from `tool_policy_json` is metadata cleanup only; by itself it does not revoke access.
- Saving an agent after editing `tool_policy_json` is currently additive. It can recreate missing grants/definitions for selected tools, but it does not remove existing grants for tools that were unselected.
- If the tool remains in `backend/tools/registry.py`, future runs of `seed_tools` or `seed_workspace_tools` can recreate the catalog rows you just removed.

### Adding a new tool

When adding a new tool, update every layer in this order so the tool can be exposed safely and executed successfully:

1. Add the canonical tool entry to `backend/tools/registry.py`.
   - Set the shared `name`, `description`, `args_schema`, `risk`, `requires_approval`, and `released` defaults.
   - Keep the schema aligned with the actual ToolRunner argument model the executor will validate.
2. Add the ToolRunner implementation under `toolrunner/app/tools/`.
   - Create the handler function and any supporting helpers.
   - Add or update the matching Pydantic args model in `toolrunner/app/models.py`.
   - Register the tool in `toolrunner/app/main.py` so `_TOOL_HANDLERS` can dispatch it.
3. Seed the shared catalog.
   - Select the tool(s) you want to update/seed from the master `backend/tools/registry.py` file.
   - `backend/tools/registry.py` is the master definition file for the shared tool catalog. It is the canonical source that declares each tool group and tool's shared metadata, including `name`, `description`, `args_schema`, `risk`, `requires_approval`, and `released`.
   - Run `python manage.py seed_tools` so the shared `ToolGroup` and `Tool` rows exist and match the canonical registry entries.
   - `seed_tools` reads `TOOL_REGISTRY`, creates any missing `ToolGroup` and `Tool` rows, and updates existing shared catalog rows when the registry metadata changes. In practice, this is the command that pushes the Python registry definition into the database-backed shared catalog.
   - `seed_tools` is designed to be rerun safely. It updates matching rows by tool name instead of creating duplicates, so it works both for first-time seeding and for refreshing existing catalog metadata after registry changes.
4. Seed or create the workspace definition.
   - Run `python manage.py seed_workspace_tools --workspace <workspace>` or create the `ToolDefinition` manually in admin.
   - `seed_workspace_tools` reads from the shared `Tool` catalog, not directly from `registry.py`, and creates or updates workspace-scoped `ToolDefinition` rows for the selected workspace.
   - The command copies shared catalog fields down into each workspace definition, including `name`, `description`, `args_schema`, `default_risk_level`, and `default_requires_approval`, so the workspace starts from the current shared baseline.
   - By default, `seed_workspace_tools` seeds released tools only and leaves definitions disabled unless `--enable-all` is passed. Use `--include-unreleased` only when you intentionally want unreleased shared tools to appear in that workspace.
   - Ensure the workspace `ToolDefinition.tool` foreign key is linked to the shared `Tool` row and `enabled=True` if the tool should be selectable.
5. Grant the tool to the agent.
   - Create or enable the `AgentToolGrant` row for the target agent and tool.
   - The effective prompt tool set is built from `ToolDefinition` + `AgentToolGrant` + release gating, so seeding alone is not enough.
6. Confirm approval and risk defaults.
   - Verify the shared catalog defaults (`Tool.risk`, `Tool.requires_approval`) and any workspace overrides (`ToolDefinition.default_risk_level`, `ToolDefinition.default_requires_approval`).
   - Make sure dangerous or write-capable tools require approval unless there is a deliberate reason not to.
7. Keep agent metadata in sync.
   - If you use `Agent.tool_policy_json["selected_tools"]` for admin metadata, add the tool name there as well.
   - Treat that JSON as metadata only; it does not replace the real `ToolDefinition` / `AgentToolGrant` permission rows.

------------------------------------------------------------------------

## Run Lifecycle & Snapshotting

Runs advance through a deterministic sequence:
1. `PENDING`
2. `RUNNING` (model call step + `state_changed`)
3. `WAITING_FOR_APPROVAL` (if tool calls require human sign-off)
4. `RUNNING` (after approval, new tick)
5. `COMPLETED` / `FAILED`
6. `PAUSED` (manual pauses halt ticks)
7. `CANCELED` (human cancellation surfaces `cancel_run` events)
8. `WAITING_FOR_SUBRUN` (parents pause while child runs execute)

Constraints:
- `step_index` is strictly monotonic per run.
- Events are immutable and ordered by `seq`.
- Snapshots provide the full `run` row plus ordered `steps` and `events_since_seq`.
- Snapshots also include child run summaries so the reconnecting UI can paint the parent→child tree.
- Snapshots also persist each run/step/event's `correlation_id`, so reconnect logic can rebuild the exact timeline for any correlation and explain why a parent waited or resumed.
- Child run summaries now surface the `SubrunLink` metadata (group id, join/failure policy, quorum, timeout), giving the UI enough context to explain why a parent is still waiting or which policy completed.
- Replay is safe because the database is the source of truth, and every client reconnect can fetch the latest snapshot and apply events in order.

------------------------------------------------------------------------

## Persistence, Checkpoints & Retention

- `runs.services.checkpoints.create_checkpoint` materializes `run`, `steps`, `events_since_seq`, and child summaries into a JSON/ZIP bundle stored under `ARCHIVE_ROOT`. Once a bundle is captured, a `run_archived` event is emitted so the run log can surface “export run as bundle” diagnostics.
- `RunArchive` records keep summary stats (`steps`, `events`, `notes`) and point back to the bundle, and `ui.views.run_detail` renders the available `run.archives` with download links (you can also download via `/ui/run/<run_id>/archive/<archive_id>/download`).
- The periodic Celery beat job `runs.tasks.archive_completed_runs` (driven by `ARCHIVE_INTERVAL_HOURS`, `ARCHIVE_LIMIT`, and `ARCHIVE_COMPACT_EVENTS`) calls `archive_completed_runs` so runs older than `ARCHIVE_RETENTION_DAYS` automatically snapshot and compact their verbose events.
- The `python manage.py archive_runs` command exposes the same code path with flags for `--older-than`, `--limit`, `--compact`, and `--verbose-events`, giving operators a manual retention trigger.
- `runs.services.checkpoints.compact_events` keeps PostgreSQL lean by pruning `VERBOSE_EVENT_TYPES` older than `EVENT_RETENTION_DAYS`, while `runs.services.checkpoints.purge_old_archives` deletes aged bundles after download/export.
- Documentation lives in `docs/checkpoints.md`, which covers the config knobs (`ARCHIVE_*`, `EVENT_RETENTION_DAYS`, `VERBOSE_EVENT_TYPES`), the Celery job, CLI, and UI integration.

------------------------------------------------------------------------

## Multi-Tenancy & Concurrency

- Every surface (runs, tool calls, events, approvals) is scoped to a `Workspace`.
- WebSocket groups enforce workspace membership before subscribing.
- Concurrency safety relies on database locking, unique constraints (`(run, step_index)`, `(run, seq)`), and Celery tasks using `nowait=True` to avoid contention.
- No in-memory state is authoritative; the DB is canonical.
- Per-workspace quotas throttle both rate and concurrency. Rate caps keep API endpoints within the burst-SLO numbers (run creation 10.29/s, spawn 2.14/s, snapshot 18.49/s, tick 41/s) while concurrency caps enforce a maximum of 5 live parent runs, 12 total runs, 4 pending subruns per parent, 6 workspace tool calls (1 per run), and 20/5 websocket sessions (workspace/user). All of these limits live in `core.services.limits` and are tuned via `backend/scripts/burst_slo_test.py`.

------------------------------------------------------------------------

## Design Goals

- Database is canonical.
- Events are immutable.
- Broadcast only occurs after commit.
- Deterministic replay is required.
- Explicit transitions over implicit behavior.
- Approval streams keep humans in the loop.
- Observability is built-in via event logs, snapshots, and WebSocket streams.
