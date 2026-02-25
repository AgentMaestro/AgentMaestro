from __future__ import annotations

import json
import os
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Sequence

from django.conf import settings
from django.utils import timezone

from runs.models import AgentRun, RunArchive, RunEvent
from runs.services.events import append_event
from runs.services.snapshot import get_run_snapshot

DEFAULT_RETENTION_DAYS = int(os.getenv("AGENTMAESTRO_EVENT_RETENTION_DAYS", "30"))
VERBOSE_EVENT_TYPES = getattr(settings, "AGENTMAESTRO_VERBOSE_EVENT_TYPES", ["token_stream", "debug_log"])
FINAL_RUN_STATUSES = (
    AgentRun.Status.COMPLETED,
    AgentRun.Status.FAILED,
    AgentRun.Status.CANCELED,
)


def _archive_root() -> Path:
    base = getattr(settings, "AGENTMAESTRO_ARCHIVE_ROOT", settings.BASE_DIR / "run_archives")
    root = Path(base)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _serialize_snapshot(snapshot: dict) -> str:
    return json.dumps(
        snapshot,
        default=lambda obj: obj.isoformat() if hasattr(obj, "isoformat") else str(obj),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def create_checkpoint(run_id: str, *, compress: bool = True, retention_days: int | None = None) -> RunArchive:
    run = AgentRun.objects.filter(id=run_id).first()
    if not run:
        raise AgentRun.DoesNotExist(run_id)

    snapshot = get_run_snapshot(run_id)
    timestamp = timezone.now()
    target_dir = _archive_root() / str(run_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    plain_path = target_dir / f"run_snapshot_{timestamp.strftime('%Y%m%d%H%M%S')}.json"
    serialized = _serialize_snapshot(snapshot)
    if compress:
        archive_path = target_dir / f"{plain_path.name}.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(plain_path.name, serialized)
    else:
        archive_path = plain_path
        archive_path.write_text(serialized, encoding="utf-8")

    summary = {
        "status": run.status,
        "steps": len(snapshot.get("steps", [])),
        "events": len(snapshot.get("events_since_seq", [])),
        "created": timestamp.isoformat(),
    }
    notes = f"Checkpoint created with retention {retention_days or DEFAULT_RETENTION_DAYS} days."
    archive = RunArchive.objects.create(
        run=run,
        archive_path=str(archive_path),
        summary=summary,
        notes=notes,
    )

    append_event(
        run_id=str(run.id),
        event_type="run_archived",
        payload={
            "archive_id": str(archive.id),
            "archive_path": str(archive.archive_path),
            "summary": summary,
            "notes": notes,
            "retention_days": retention_days or DEFAULT_RETENTION_DAYS,
        },
        broadcast_to_workspace=True,
        workspace_summary_event="run_archived",
    )

    return archive


def compact_events(
    run_id: str,
    *,
    retention_days: int | None = None,
    event_types: Sequence[str] | None = None,
) -> int:
    days = retention_days or DEFAULT_RETENTION_DAYS
    cutoff = timezone.now() - timedelta(days=days)
    types = event_types or VERBOSE_EVENT_TYPES
    qs = RunEvent.objects.filter(run_id=run_id, created_at__lt=cutoff)
    if types:
        qs = qs.filter(event_type__in=types)
    total = qs.count()
    if total:
        qs.delete()
    return total


def archive_completed_runs(
    *,
    older_than_days: int = 30,
    limit: int | None = None,
    compact: bool = True,
    retention_days: int | None = None,
    event_types: Sequence[str] | None = None,
) -> list[dict]:
    cutoff = timezone.now() - timedelta(days=older_than_days)
    queryset = AgentRun.objects.filter(
        status__in=FINAL_RUN_STATUSES,
        archived_at__isnull=True,
        ended_at__lte=cutoff,
    ).order_by("ended_at")
    if limit:
        queryset = queryset[:limit]

    results = []
    for run in queryset:
        archive = create_checkpoint(str(run.id), compress=True, retention_days=retention_days)
        compacted = 0
        if compact:
            compacted = compact_events(
                str(run.id),
                retention_days=retention_days,
                event_types=event_types,
            )
        run.archived_at = timezone.now()
        run.save(update_fields=["archived_at"])
        results.append(
            {"run_id": run.id, "archive_path": archive.archive_path, "compacted": compacted}
        )
    return results


def purge_old_archives(*, older_than_days: int = 90) -> int:
    cutoff = timezone.now() - timedelta(days=older_than_days)
    archives = RunArchive.objects.filter(created_at__lt=cutoff)
    deleted = 0
    for archive in archives:
        path = Path(archive.archive_path)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        deleted += 1
    archives.delete()
    return deleted
