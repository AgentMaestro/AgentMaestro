from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.utils.text import get_valid_filename

from logging_utils import get_app_logger
from runs.models import AgentRun, Artifact

logger = get_app_logger(__name__)


def get_artifact_root() -> Path:
    root = getattr(settings, "RUN_ARTIFACT_ROOT", settings.BASE_DIR / "run_artifacts")
    return Path(root).resolve()


def _safe_filename(name: str) -> str:
    candidate = get_valid_filename(Path(str(name or "")).name.strip()) or "attachment"
    return candidate[:120]


def _format_bytes(size_bytes: int | None) -> str:
    if size_bytes is None or size_bytes < 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024 or candidate == units[-1]:
            break
        value /= 1024.0
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def store_run_artifact(
    run: AgentRun,
    uploaded_file,
    *,
    artifact_type: str = Artifact.Type.FILE,
    metadata: dict[str, object] | None = None,
) -> Artifact:
    original_name = str(getattr(uploaded_file, "name", "") or "attachment").strip()
    safe_name = _safe_filename(original_name)
    artifact_id = uuid.uuid4()
    artifact_dir = get_artifact_root() / str(run.id) / str(artifact_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    storage_path = artifact_dir / safe_name
    file_size = 0
    sha256 = hashlib.sha256()
    try:
        with storage_path.open("wb") as handle:
            chunks = getattr(uploaded_file, "chunks", None)
            if callable(chunks):
                for chunk in chunks():
                    handle.write(chunk)
                    file_size += len(chunk)
                    sha256.update(chunk)
            else:
                blob = uploaded_file.read()
                handle.write(blob)
                file_size = len(blob)
                sha256.update(blob)
    except Exception:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        logger.exception("Failed to store run artifact run=%s file=%s", run.id, original_name)
        raise

    artifact_metadata = {
        "source": "chat_upload",
        "source_channel": "upload",
        "original_name": original_name,
        "size_bytes": file_size,
        "sha256": sha256.hexdigest(),
        "provided_in_prompt": True,
    }
    if metadata:
        artifact_metadata.update(metadata)

    artifact = Artifact.objects.create(
        id=artifact_id,
        run=run,
        type=artifact_type,
        name=original_name,
        mime_type=str(getattr(uploaded_file, "content_type", "") or ""),
        storage_path=str(storage_path),
        metadata=artifact_metadata,
    )
    logger.info(
        "Stored run artifact run=%s artifact_id=%s file=%s size_bytes=%s storage_path=%s",
        run.id,
        artifact.id,
        original_name,
        file_size,
        storage_path,
    )
    return artifact


def delete_run_artifact(artifact: Artifact) -> None:
    storage_path = Path(str(artifact.storage_path or ""))
    artifact_dir = storage_path.parent if storage_path.parent != storage_path else storage_path
    if storage_path.exists():
        try:
            storage_path.unlink()
        except IsADirectoryError:
            shutil.rmtree(storage_path, ignore_errors=True)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir, ignore_errors=True)
    artifact.delete()
    logger.info(
        "Deleted run artifact run=%s artifact_id=%s storage_path=%s",
        artifact.run_id,
        artifact.id,
        storage_path,
    )


def _delete_artifact_storage(storage_path: Path) -> None:
    if storage_path.exists():
        try:
            storage_path.unlink()
        except IsADirectoryError:
            shutil.rmtree(storage_path, ignore_errors=True)


def serialize_artifact(artifact: Artifact) -> dict[str, object]:
    storage_path = Path(str(artifact.storage_path or ""))
    size_bytes = None
    if storage_path.exists():
        try:
            size_bytes = storage_path.stat().st_size
        except OSError:
            size_bytes = None
    if size_bytes is None:
        raw_size = artifact.metadata.get("size_bytes") if isinstance(artifact.metadata, dict) else None
        if isinstance(raw_size, int):
            size_bytes = raw_size
    consumed_at = artifact.metadata.get("consumed_at") if isinstance(artifact.metadata, dict) else None
    return {
        "id": str(artifact.id),
        "attachment_id": str(artifact.id),
        "name": str(artifact.metadata.get("original_name") or artifact.name or storage_path.name),
        "filename": str(artifact.metadata.get("original_name") or artifact.name or storage_path.name),
        "type": artifact.type,
        "mime_type": artifact.mime_type,
        "size_bytes": size_bytes or 0,
        "sha256": str(artifact.metadata.get("sha256") or ""),
        "storage_path": artifact.storage_path,
        "canonical_path": artifact.storage_path,
        "provided_in_prompt": bool(artifact.metadata.get("provided_in_prompt")),
        "source_channel": str(artifact.metadata.get("source_channel") or ""),
        "google_file_id": str(artifact.metadata.get("google_file_id") or ""),
        "google_drive_url": str(artifact.metadata.get("google_drive_url") or ""),
        "google_file_name": str(artifact.metadata.get("google_file_name") or ""),
        "google_mime_type": str(artifact.metadata.get("google_mime_type") or ""),
        "google_export_mime_type": str(artifact.metadata.get("google_export_mime_type") or ""),
        "submitted_by_user_id": artifact.metadata.get("submitted_by_user_id"),
        "submitted_by_username": artifact.metadata.get("submitted_by_username"),
        "consumed": bool(consumed_at),
        "consumed_at": str(consumed_at or ""),
        "created_at": artifact.created_at.isoformat() if artifact.created_at else "",
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else "",
    }


def is_artifact_consumed(artifact: Artifact) -> bool:
    metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
    return bool(str(metadata.get("consumed_at") or "").strip())


def pending_artifacts(artifacts: Iterable[Artifact]) -> list[Artifact]:
    return [artifact for artifact in artifacts if not is_artifact_consumed(artifact)]


def mark_artifacts_consumed(artifacts: Iterable[Artifact], *, consumed_at: str | None = None) -> int:
    consumed_text = str(consumed_at or timezone.now().isoformat()).strip()
    count = 0
    for artifact in artifacts:
        metadata = dict(artifact.metadata or {})
        if str(metadata.get("consumed_at") or "").strip():
            continue
        metadata["consumed_at"] = consumed_text
        artifact.metadata = metadata
        artifact.save(update_fields=["metadata", "updated_at"])
        count += 1
    return count


def _cleanup_artifact_directory(artifact: Artifact) -> None:
    raw_storage_path = str(artifact.storage_path or "").strip()
    if not raw_storage_path:
        return
    storage_path = Path(raw_storage_path)
    artifact_root = get_artifact_root().resolve()
    current_dir = storage_path.parent

    while current_dir.exists() and current_dir != artifact_root:
        try:
            if any(current_dir.iterdir()):
                break
            current_dir.rmdir()
        except OSError:
            break
        current_dir = current_dir.parent


def purge_consumed_artifacts(*, older_than_days: int = 30, limit: int | None = None) -> dict[str, int]:
    cutoff = timezone.now() - timedelta(days=older_than_days)
    queryset = Artifact.objects.filter(updated_at__lt=cutoff).order_by("updated_at")
    if limit:
        queryset = queryset[:limit]

    inspected = 0
    deleted = 0
    skipped = 0
    for artifact in queryset.iterator(chunk_size=200):
        inspected += 1
        metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
        consumed_raw = str(metadata.get("consumed_at") or "").strip()
        if not consumed_raw:
            skipped += 1
            continue
        consumed_at = parse_datetime(consumed_raw)
        if consumed_at is None:
            skipped += 1
            continue
        if timezone.is_naive(consumed_at):
            consumed_at = timezone.make_aware(consumed_at, timezone.get_current_timezone())
        if consumed_at > cutoff:
            skipped += 1
            continue

        artifact_id = str(artifact.id)
        run_id = str(artifact.run_id)
        storage_path = Path(str(artifact.storage_path or ""))
        with transaction.atomic():
            artifact.delete()
        _delete_artifact_storage(storage_path)
        _cleanup_artifact_directory(artifact)
        deleted += 1
        logger.info(
            "Purged consumed artifact run=%s artifact_id=%s consumed_at=%s cutoff=%s",
            run_id,
            artifact_id,
            consumed_raw,
            cutoff.isoformat(),
        )

    return {"inspected": inspected, "deleted": deleted, "skipped": skipped}


def render_artifact_summary(artifacts: Iterable[Artifact]) -> str:
    items = list(artifacts)
    if not items:
        return ""
    lines = [f"Attached {len(items)} file(s):"]
    for artifact in items:
        raw_size = artifact.metadata.get("size_bytes") if isinstance(artifact.metadata, dict) else None
        size_text = _format_bytes(raw_size if isinstance(raw_size, int) else None)
        label = str(artifact.metadata.get("original_name") or artifact.name or artifact.id)
        if size_text:
            lines.append(f"- {label} ({size_text})")
        else:
            lines.append(f"- {label}")
    return "\n".join(lines)
