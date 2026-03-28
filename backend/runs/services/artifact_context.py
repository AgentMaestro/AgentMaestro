from __future__ import annotations

import csv
import io
import json
import mimetypes
from pathlib import Path
from typing import Iterable

from django.conf import settings

from logging_utils import get_app_logger
from runs.models import Artifact
from runs.services.artifacts import serialize_artifact

logger = get_app_logger(__name__)

DEFAULT_ARTIFACT_CONTEXT_CHAR_LIMIT = int(
    getattr(settings, "RUN_ARTIFACT_CONTEXT_CHAR_LIMIT", 12000)
)
DEFAULT_ARTIFACT_PREVIEW_BYTES = int(
    getattr(settings, "RUN_ARTIFACT_PREVIEW_BYTES", 1024 * 1024)
)

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".py",
    ".json",
    ".csv",
    ".log",
    ".yml",
    ".yaml",
    ".xml",
    ".html",
    ".htm",
    ".ini",
    ".cfg",
    ".toml",
    ".ts",
    ".js",
    ".css",
    ".sh",
}


def _guess_mime(artifact: Artifact, path: Path) -> str:
    if artifact.mime_type:
        return str(artifact.mime_type)
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or ""


def _read_bytes_preview(path: Path, limit: int = DEFAULT_ARTIFACT_PREVIEW_BYTES) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit + 1)


def _decode_text_preview(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _trim_text(text: str, limit: int = DEFAULT_ARTIFACT_CONTEXT_CHAR_LIMIT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


def _format_csv_preview(text: str) -> str:
    reader = csv.reader(io.StringIO(text))
    rows: list[list[str]] = []
    for index, row in enumerate(reader):
        if index >= 25:
            break
        rows.append([cell.strip() for cell in row])
    if not rows:
        return ""
    widths = [0] * max(len(row) for row in rows)
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines: list[str] = []
    for row in rows:
        padded = [cell.ljust(widths[index]) for index, cell in enumerate(row)]
        lines.append(" | ".join(padded).rstrip())
    return "\n".join(lines)


def _format_json_preview(text: str) -> str:
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    rendered = json.dumps(parsed, indent=2, ensure_ascii=False)
    return rendered


def _format_context_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text


def _extract_text_from_artifact(artifact: Artifact) -> tuple[str, dict[str, object]]:
    storage_path = Path(str(artifact.storage_path or ""))
    if not storage_path.exists():
        return "", {
            "extraction_status": "missing",
            "extraction_notes": "artifact file is missing",
            "truncated": False,
        }

    mime_type = _guess_mime(artifact, storage_path).lower()
    suffix = storage_path.suffix.lower()
    raw = _read_bytes_preview(storage_path)
    exceeded_preview_limit = len(raw) > DEFAULT_ARTIFACT_PREVIEW_BYTES

    if mime_type.startswith("text/") or suffix in TEXT_SUFFIXES or mime_type in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
    }:
        text, encoding = _decode_text_preview(raw[:DEFAULT_ARTIFACT_PREVIEW_BYTES])
        if suffix == ".csv" or mime_type == "text/csv":
            text = _format_csv_preview(text) or text
        elif mime_type == "application/json" or suffix == ".json":
            text = _format_json_preview(text)
        text, truncated = _trim_text(text)
        notes = f"decoded with {encoding}"
        if exceeded_preview_limit:
            notes = f"{notes}; preview limited to {DEFAULT_ARTIFACT_PREVIEW_BYTES} bytes"
        return text, {
            "extraction_status": "text",
            "extraction_notes": notes,
            "truncated": truncated,
        }

    if mime_type.startswith("image/"):
        return "", {
            "extraction_status": "image",
            "extraction_notes": "image attachment not yet text-extracted",
            "truncated": False,
        }

    return "", {
        "extraction_status": "binary",
        "extraction_notes": f"non-text artifact ({mime_type or 'unknown mime'})",
        "truncated": False,
    }


def _build_attachment_metadata_block(
    serialized: dict[str, object],
    extraction_meta: dict[str, object],
    extracted_text: str,
) -> str:
    preview_snippet = " ".join(extracted_text.split())[:240] if extracted_text else ""
    text_extractable = bool(extracted_text)
    extract_status = str(extraction_meta.get("extraction_status") or "").strip()
    extract_method = "embedded" if text_extractable else "none"
    extract_confidence = 1.0 if text_extractable else 0.0
    extract_error = "" if text_extractable else str(extraction_meta.get("extraction_notes") or "").strip()
    lines = [
        "ATTACHMENT METADATA",
        f"attachment_id: {_format_context_value(serialized.get('attachment_id'))}",
        f"filename: {_format_context_value(serialized.get('filename') or serialized.get('name'))}",
        f"mime_type: {_format_context_value(serialized.get('mime_type'))}",
        f"size_bytes: {_format_context_value(serialized.get('size_bytes'))}",
        f"sha256: {_format_context_value(serialized.get('sha256'))}",
        f"canonical_path: {_format_context_value(serialized.get('canonical_path'))}",
        f"created_at: {_format_context_value(serialized.get('created_at'))}",
        f"provided_in_prompt: {_format_context_value(serialized.get('provided_in_prompt'))}",
        f"deleted: false",
        f"source_channel: {_format_context_value(serialized.get('source_channel'))}",
        f"google_file_id: {_format_context_value(serialized.get('google_file_id'))}",
        f"google_drive_url: {_format_context_value(serialized.get('google_drive_url'))}",
        f"google_file_name: {_format_context_value(serialized.get('google_file_name'))}",
        f"google_mime_type: {_format_context_value(serialized.get('google_mime_type'))}",
        f"google_export_mime_type: {_format_context_value(serialized.get('google_export_mime_type'))}",
        f"submitted_by: {_format_context_value(serialized.get('submitted_by_username') or serialized.get('submitted_by_user_id'))}",
        "EXTRACTION RESULT",
        f"text_extractable: {_format_context_value(text_extractable)}",
        f"extract_method: {extract_method}",
        f"extract_confidence: {extract_confidence:.1f}",
    ]
    if extract_error:
        lines.append(f"extract_error: {extract_error}")
    elif extract_status:
        lines.append(f"extract_error: none")
    if preview_snippet:
        lines.append(f"preview_text_snippet: {preview_snippet}")
    return "\n".join(lines)


def build_artifact_context_payload(artifacts: Iterable[Artifact]) -> dict[str, object]:
    items = []
    sections: list[str] = []
    for artifact in artifacts:
        serialized = serialize_artifact(artifact)
        storage_path = str(serialized.get("storage_path") or artifact.storage_path or "").strip()
        text, extraction_meta = _extract_text_from_artifact(artifact)
        metadata_block = _build_attachment_metadata_block(serialized, extraction_meta, text)
        section_lines = [
            "ATTACHED FILE CONTEXT",
            metadata_block,
        ]
        if storage_path:
            section_lines.append(f"Full path: {storage_path}")
        section_lines.extend(
            [
                f"Type: {serialized['type']}",
                "Extracted content:",
            ]
        )
        if text:
            section_lines.append(text)
        else:
            section_lines.append("(not text-extractable)")
        if str(serialized.get("google_file_id") or "").strip():
            section_lines.append(
                "Instruction: If google_file_id is present, prefer google_bridge for Google-native file reads or exports."
            )
        section_lines.append("Instruction: Use this content directly. Do not infer that only the filename was provided.")
        section_text = "\n".join(section_lines)
        sections.append(section_text)
        items.append(
            {
                **serialized,
                "attachment_id": serialized.get("attachment_id") or serialized.get("id"),
                **extraction_meta,
                "extraction": {
                    "text_extractable": bool(text),
                    "extract_method": "embedded" if text else "none",
                    "extract_confidence": 1.0 if text else 0.0,
                    "extract_error": "" if text else str(extraction_meta.get("extraction_notes") or ""),
                },
                "text": text,
                "context_text": section_text,
                "artifact_path": storage_path,
            }
        )
    combined_text = "\n\n".join(sections).strip()
    return {
        "text": combined_text,
        "artifacts": items,
        "artifact_count": len(items),
    }
