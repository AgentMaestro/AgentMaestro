# Performance & Load Testing

This document captures the load-test harness, the observability targets (SLOs), and the baseline results gathered from the smoke pass. The goal is to know **what breaks before actual users hit it**.

## Toolkit

- `backend/scripts/load_test.py` – Django-aware script that drives the new `/api/...` endpoints using `django.test.Client`. It can run in three modes:
  - `smoke` – minimal sanity run (default `--runs 5`, `--children 1`).
  - `burst` – fires many runs concurrently (`--mode burst --runs 50 --burst-size 12`).
  - `soak` – sequential high-volume traffic (`--mode soak --runs 100 --soak-interval 5`).
- Each worker records:
  - Run creation latency (`POST /api/runs/`)
  - Subrun spawn latency (`POST /api/runs/<id>/spawn_subrun/`)
  - Snapshot poll latency (`GET /api/runs/<id>/snapshot/?since_seq=`)
  - Run completion time, event count, and tick (step) count.
  - Errors encountered.

### Running the harness

```bash
python backend/scripts/load_test.py --mode smoke --runs 5 --children 1 --poll-timeout 10
```

Parameters:

| Option | Description |
| --- | --- |
| `--runs` | Number of parent runs to create |
| `--children` | Number of subruns per parent |
| `--burst-size` | Maximum concurrent worker threads for burst mode |
| `--poll-interval` | Seconds between snapshot polls |
| `--poll-timeout` | How long to wait (per run) before aborting |
| `--soak-interval` | Sleep between runs in soak mode (seconds) |

## SLOs / Observability

| Metric | Target (approximate) | Measurement |
| --- | --- | --- |
| Run creation latency | < 300 ms | `creation_latencies` in the script |
| Subrun spawn latency | < 200 ms | `spawn_latencies` |
| Tick throughput | ≥ 5 ticks/sec | ticks counted from `step_created` events vs run completion time |
| DB write latency | (proxy) median of API calls (creation/spawn) |
| Redis publish latency | (proxy) snapshot poll latency since events are persisted before publish |
| Failures + retries | Zero failed API responses; errors logged in `metrics.errors` |

> Note: direct instrumentation of PostgreSQL/Redis is a future enhancement; for now we treat the API latencies as upper bounds on those subsystems' responsiveness.

## Baseline Smoke Run

Run command:

```
python backend/scripts/load_test.py --mode smoke --runs 5 --children 1 --poll-timeout 10
```

Observed summary (latest run):

- Total duration: 52.9 s
- Average run creation: 94 ms
- Average spawn latency: 202 ms
- Average snapshot poll latency: 11 ms
- Average completion time: 10.1 s
- Average ticks per run: 1.0 (the MVP flow only emits the MODEL_CALL step today)
- Average events per run: 3
- Errors: none

These numbers become the quick-check baseline that the CI “smoke perf” job must maintain.

## Burst SLO Calibration & Quotas

`backend/scripts/burst_slo_test.py` pushes bursts against `POST /api/runs/`, `POST /api/runs/<run_id>/spawn_subrun/`, and `GET /api/runs/<run_id>/snapshot/`, tracking p95 latency, Redis queue depth, and HTTP errors. The script reports the highest burst that still meets the SLO and the first burst that violates it so you can assess the system’s headroom.

```
python backend/scripts/burst_slo_test.py
```

Use it whenever you retune Redis, Celery concurrency, PostgreSQL, or any other subsystem that could affect API latency so you can recompute the per-workspace quotas defined in `core.services.limits`. The current stable throughput numbers (and their 25% workspace quotas) are:

| Endpoint | Stable throughput | Workspace quota (25%) |
| --- | --- | --- |
| `POST /api/runs/` | ~41 req/sec (p95 ~240 ms) | 10.29 req/sec |
| `POST /api/runs/<run_id>/spawn_subrun/` | ~8.5 req/sec (p95 ~117 ms) | 2.14 req/sec |
| `GET /api/runs/<run_id>/snapshot/` | ~74 req/sec (p95 ~375 ms) | 18.49 req/sec |

Workspaces that exceed these quotas receive a `429` with `error: "Workspace quota exceeded …"`. The same limits backstop the Celery tick worker so a runaway workspace cannot saturate the worker pool.

## Concurrency Caps

Concurrency caps guard against fan-out storms:

- **Parents per workspace**: 5 live parent runs (tracked via `AGENTMAESTRO_QUOTA_NAMESPACE:concurrent` sets).
- **Total runs per workspace**: 12 parents + children combined, preventing unlimited breadth.
- **Pending subruns per parent**: 4 children may be in-flight before the parent must wait for completions.
- **Pending tool calls**: max 6 workspace-wide and 1 per individual run (applies only to tool calls requiring approval).
- **WebSocket connections**: Max 20 simultaneous connections per workspace and 5 per user; clients will receive 429-style closures when those caps are hit.

The enforcement lives inside `core.services.limits.QUOTA_MANAGER` and the websocket consumers. When these caps change, adjust the values in `core/services/limits.py` and rerun `backend/scripts/burst_slo_test.py` to validate the operational impact.

## Checkpoints & Archiving

Run snapshots can be materialized via `runs.services.checkpoints.create_checkpoint`, which bundles the latest run/steps/events into `run_archives/<run_id>/run_snapshot_<timestamp>.json.zip` while recording a lightweight `RunArchive` row. Use `compact_events` to drop noisy payloads once they are older than `AGENTMAESTRO_EVENT_RETENTION_DAYS` (default 30d).

The management command `python manage.py archive_runs --older-than 30 --compact` wraps the checkpoint + compaction workflow, marks `archived_at`, and prints each archive path that can be shipped off for offline replay. Add `--verbose-events` to target custom event types, or `--limit` to stage the archive in batches.

## Burst & Soak Plans (future automation)

- **Burst test**: `python backend/scripts/load_test.py --mode burst --runs 100 --children 2 --burst-size 20`
  - SLO: API error rate < 1%, run completion within 30s.
  - Metrics to track externally: CPU/memory usage, Celery queue depth, Redis channel lag (via monitoring).
- **Soak test**: `python backend/scripts/load_test.py --mode soak --runs 500 --children 1 --soak-interval 5`
  - SLO: No drift in median creation latency over 90 minutes.
  - Additional monitoring: Celery ticks/sec (exported by `runs.tasks.run_tick` instrumentation) and database write iowait.

## CI smoke-perf job

See `.github/workflows/perf_smoke.yml`. It invokes the script in smoke mode and fails if averages exceed the target latencies or if any HTTP response is non-200. This job runs on every push to catch regressions before they land.
