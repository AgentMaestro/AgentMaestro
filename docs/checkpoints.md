# Checkpoints & Retention

AgentMaestro keeps every completed run reproducible by materializing snapshots + event history into portable bundles that can be downloaded, replayed, or audited.

## Bundle creation

- `runs.services.checkpoints.create_checkpoint(run_id, compress=True)` generates a JSON description of the run, ordered steps, events, and child summaries (including the join/failure policy metadata).
- Bundles are written under `AGENTMAESTRO_ARCHIVE_ROOT` (default: `<BASE_DIR>/run_archives`). Each checkpoint records summary stats (`steps`, `events`, `status`, `created`) and is zipped before persisting.
- After the bundle is stored, `RunArchive` tracks the archive path + metadata, and a `run_archived` event is appended to the run stream so the UI and logs can surf the “export run as bundle” milestone. The event includes:

```json
{
  "event_type": "run_archived",
  "payload": {
    "archive_id": "...",
    "archive_path": "...",
    "summary": {...},
    "notes": "...",
    "retention_days": 30
  }
}
```

## Scheduled retention & compaction

- `runs.tasks.archive_completed_runs` is called periodically by Celery beat (see `AGENTMAESTRO_ARCHIVE_INTERVAL_HOURS` or `CELERY_BEAT_SCHEDULE`).
- The task runs `archive_completed_runs` with the configured `AGENTMAESTRO_ARCHIVE_RETENTION_DAYS`, `AGENTMAESTRO_ARCHIVE_LIMIT`, and `AGENTMAESTRO_ARCHIVE_COMPACT_EVENTS`. It snapshots runs that have ended before the cutoff and, if requested, prunes `RunEvent` records matching `AGENTMAESTRO_VERBOSE_EVENT_TYPES` that are older than `AGENTMAESTRO_EVENT_RETENTION_DAYS`.
- Use `python manage.py archive_runs --older-than 0 --limit 10 --compact --verbose-events token_stream debug_log` from a management shell to trigger ad-hoc retention runs. The CLI respects the same compacting and `AGENTMAESTRO_VERBOSE_EVENT_TYPES` settings as the periodic job.

## Compacting verbose events

- `runs.services.checkpoints.compact_events` walks through `RunEvent` entries older than the retention cutoff and deletes the verbose event types (by default `token_stream` and `debug_log`). You can pass a custom list through `--verbose-events` or the Celery task configuration.
- Replace verbose streams with summarized stats in the bundle (the `summary["events"]` field captures how many were saved in the snapshot).

## Purging old bundles

- `runs.services.checkpoints.purge_old_archives(older_than_days=90)` can be run manually to delete stored bundles (the corresponding `RunArchive` rows will be removed).
- Archived bundles are also surfaced in the UI run detail page (`ui/views.run_detail`), which renders `run.archives` and exposes `/ui/run/<run_id>/archive/<archive_id>/download` for “export run as bundle” flows.
- The UI can now show the `run_archived` event in the log stream thanks to the `append_event` call inside `create_checkpoint`.

## Configuration summary

| Environment variable | Purpose |
| --- | --- |
| `AGENTMAESTRO_ARCHIVE_ROOT` | Directory used for run snapshots. |
| `AGENTMAESTRO_ARCHIVE_RETENTION_DAYS` | Default `older_than` threshold in the Celery retention job. |
| `AGENTMAESTRO_ARCHIVE_LIMIT` | Max runs archived per invocation (Celery job + CLI). |
| `AGENTMAESTRO_ARCHIVE_COMPACT_EVENTS` | Control whether verbose events are pruned during archiving. |
| `AGENTMAESTRO_EVENT_RETENTION_DAYS` | Age threshold for deleting verbose events; `compact_events` uses this when `retention_days` is `None`. |
| `AGENTMAESTRO_VERBOSE_EVENT_TYPES` | Default list of verbose events removed by the compactor. |

## Export workflow

1. A run completes and Celery archives it via `runs.tasks.archive_completed_runs`.
2. The new `run_archived` event appears in `run.<run_id>` so operators can see the bundle in the log.
3. The run detail template lists the available archives, letting users download “run_snapshot_<timestamp>.json.zip”.
4. Exported bundles can be rehydrated by calling `runs.services.checkpoints.get_run_snapshot` and replaying the step/event stream against the database if needed.
