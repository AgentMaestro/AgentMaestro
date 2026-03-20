# Research + Memory Foundation

This sprint introduced the durable memory and research substrate for AgentMaestro, and the scheduled-task layer now builds on top of it with first-class headless runs.

## Stage 1: Short-Term Memory

`RunMemory` is a lightweight structured scratchpad for an active run. It stores:

- `objective`
- `current_plan`
- `key_facts`
- `open_questions`
- `recent_tool_results`
- `notes`

It is intentionally compact. It does not snapshot history, store raw transcripts, or auto-summarize with an LLM yet.

## Stage 2: Research Tools

Two research tools are available:

- `web_search`
- `fetch_url`

This stage intentionally avoids browser automation. `fetch_url` applies SSRF-style guards and rejects localhost/private-network targets.

## Stage 3: Durable Memory

`MemoryRecord` provides durable episodic/semantic/procedural memory in Django. The first tools are:

- `remember`
- `search_memory`

`remember` accepts optional lifecycle and provenance hints directly from operators or agents:

- `dedupe_key`
- `dedupe_mode`
- `source_kind`
- `source_ref`
- `pinned`
- `expires_at`

Each memory record now carries lifecycle and provenance fields as groundwork for future growth management:

- `dedupe_key`
- `source_kind`
- `source_ref`
- `last_accessed_at`
- `access_count`
- `pinned`
- `expires_at`

Current lifecycle behavior stays conservative:

- `remember` updates `last_accessed_at` and increments `access_count`
- `search_memory` updates `last_accessed_at` and increments `access_count` for returned results
- expired memories are excluded from search and recent-memory lookups
- recent-memory lookups prefer pinned records, then the most recently accessed records
- exact-content dedupe remains the fallback when no dedupe key is provided
- dedupe-key writes can merge tags and summary without rewriting the primary content body unless the incoming content is clearly better under deterministic rules

Search is still simple text lookup over `content`, `summary`, and `tags`.

Ordering remains conservative:

- semantic/procedural: relevance, importance, updated recency
- episodic: relevance, updated recency, importance

## Scope Resolution

Memory reads and writes use a central scope resolver.

Supported resolution paths:

- explicit `scope_type` + `scope_id`
- `agent=...`
- `workspace=...`
- `user=...`

Current codebase naming still stores workspace-like memory under `scope_type="sandbox"`, but the resolver accepts `workspace` as an alias and normalizes it to the canonical stored scope.

## Scheduled Task Extension

The recurring-task layer now sits on top of memory and can launch first-class headless runs.

- `ScheduledTask` is the durable recurring task record.
- `schedule_task` creates a recurring task for the current agent.
- `list_scheduled_tasks` returns the current agent's recurring tasks.
- `ScheduledTask.execution_mode` supports:
  - `deterministic`
  - `headless_run`
- Creating a scheduled task writes an episodic `MemoryRecord` with `source_kind="scheduled_task_created"`.
- Deterministic scheduled-task execution writes an episodic `MemoryRecord` with `source_kind="scheduled_task_executed"`.
- Headless run completion/failure writes episodic outcome memory with:
  - `source_kind="headless_run_completed"`
  - `source_kind="headless_run_failed"`

Scheduled-task execution memories carry a stable bucket-style `dedupe_key` so the retention service can distill repetitive execution noise later without collapsing distinct executions at write time.

The first supported task type is:

- `other_task`

## Headless Runs

Headless runs are now a first-class execution primitive.

A headless run:

- is still a real `AgentRun`
- records `execution_mode`, `trigger_kind`, and `trigger_ref`
- initializes `RunMemory`
- uses the normal LLM runner and allowed tool list
- writes normal run events and steps
- can deliver final output to the paired transport conversation when available

For scheduled tasks in `headless_run` mode:

1. Celery claims the due task
2. the launcher creates a real headless `AgentRun`
3. the run executes through `LLMRunner`
4. the finalizer updates scheduled-task state and writes outcome memory
5. delivery is attempted through the paired transport conversation when available

See:

- `docs/headless-runs.md`
- `docs/scheduled-tasks.md`

## Memory Retention

A first operational retention layer now exists.

- old or expired low-value episodic memories are retention candidates
- pinned memories are preserved
- high-importance semantic/procedural memories are preserved
- repetitive episodic memories are distilled before purge
- the retention service supports dry-run reporting before mutation

Detailed retention and operational notes live in:

- `docs/memory-retention.md`
- `docs/scheduled-tasks.md`
- `docs/smoke/memory-smoke-test-plan.md`

## Smoke Testing

The current operator runbook for memory smoke validation lives in:

- `docs/smoke/memory-smoke-test-plan.md`

Use it after changes to memory services, scheduled-task memory writes, headless scheduled execution, or retention behavior.

## Deferred

Not implemented in this sprint:

- Stage 4 run timeline UI
- embeddings or vector search
- automatic STM to LTM promotion
- semantic similarity dedupe
- fuzzy clustering
- inter-agent communication
- Google ecosystem integrations
- natural-language schedule parsing from arbitrary chat turns
- rich scheduled-task editing/pausing/deletion workflows
- multi-agent scheduled orchestration
