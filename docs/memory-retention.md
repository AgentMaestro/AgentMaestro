# Memory Retention

This document describes the current memory-governance implementation for AgentMaestro.

## Dedupe Keys

`MemoryRecord.dedupe_key` is an optional stable key used to identify the same durable memory across repeated writes.

Current behavior:

- if `dedupe_key` is present and `dedupe_mode` resolves to `key`, `remember` prefers key-based dedupe
- if `dedupe_key` is absent, `remember` falls back to exact-content dedupe within the same scope and memory kind
- if `dedupe_mode="exact"`, callers can store a `dedupe_key` for grouping/provenance without collapsing writes by key
- if `dedupe_mode="none"`, `remember` always creates a new record

This is important for episodic scheduled-task execution memory, where the key is currently used as a retention grouping bucket rather than a write-time identity.

## Scope Resolver

Memory services now resolve scopes through a central resolver.

Supported inputs:

- explicit `scope_type` + `scope_id`
- `agent=...`
- `workspace=...`
- `user=...`

Current canonical stored scope types remain:

- `sandbox`
- `agent`
- `user`

The resolver accepts `workspace` as an alias and stores it as `sandbox` to match the current data model.

## Provenance Fields

`MemoryRecord` now carries:

- `source_kind`
- `source_ref`

These fields are used to identify where a memory came from, for example:

- `manual_remember`
- `scheduled_task_created`
- `scheduled_task_executed`
- `distilled_memory`

## Current Retention Policy

The current retention service is conservative.

### Preserved

The service preserves these records by default:

- pinned memories
- semantic memories with high importance
- procedural memories with high importance
- records newer than the retention cutoff
- records recently accessed after the retention cutoff
- distilled memories already created by the retention service

### Distilled

The service distills repetitive episodic memories when they are:

- old enough for retention review
- low importance
- unpinned
- grouped deterministically by scope, source kind, and dedupe/source bucket
- present in a group with more than one raw record

The first intended distillation target is scheduled-task execution noise.

Distilled records are written back as episodic memories with:

- `source_kind="distilled_memory"`
- `source_ref=<retention bucket key>`
- `dedupe_key="distilled:<bucket>"`

### Purged

The service purges only low-value episodic records.

Immediate purge path:

- unpinned episodic memory
- low importance
- expired now

30-day purge path:

- unpinned episodic memory
- low importance
- older than the retention cutoff
- not recently accessed

If the record is part of a repetitive episodic group, the service distills first and then deletes the raw rows.

## Dry Run

Use the management command in dry-run mode before enabling mutation:

```powershell
cd backend
.\.venv\Scripts\python.exe manage.py run_memory_retention --dry-run
```

Mutation mode:

```powershell
cd backend
.\.venv\Scripts\python.exe manage.py run_memory_retention
```

## Health Reporting

Use the memory health report command for an operational summary of memory growth and retention posture:

```powershell
cd backend
.\.venv\Scripts\python.exe manage.py memory_health_report
```

The report includes:

- total `MemoryRecord` count
- counts by memory kind and scope type
- pinned and high-importance counts
- scheduled-task totals plus creation/execution memory counts
- current retention-eligible counts
- growth trend comparison against the newest saved snapshot at or before the compare window

By default the command saves a `MemoryHealthSnapshot` row for later trend comparisons. Use `--no-save` to print the report without persisting a snapshot.

## Settings

Current settings:

- `MEMORY_RETENTION_ENABLED`
- `MEMORY_RETENTION_DAYS`
- `MEMORY_RETENTION_BATCH_SIZE`
- `MEMORY_EPISODIC_DISTILL_GROUP_LIMIT`
- `MEMORY_RETENTION_INTERVAL_HOURS`
- `MEMORY_RETENTION_LOW_IMPORTANCE_THRESHOLD`
- `MEMORY_RETENTION_PROTECTED_IMPORTANCE_THRESHOLD`

If `MEMORY_RETENTION_ENABLED` is false, the periodic Celery schedule is not installed.

## Smoke Testing

Use the dedicated smoke runbook before enabling or changing retention behavior in a live environment:

- `docs/smoke/memory-smoke-test-plan.md`

The runbook covers dry-run validation, mutation validation, and idempotent rerun checks.

## Deferred

Still deferred:

- semantic similarity dedupe
- fuzzy clustering
- embeddings / vector search
- advanced content distillation with LLMs
- full archive/restore lifecycle for purged memories
- operator UI for reviewing retention decisions
- STM to LTM promotion policies
