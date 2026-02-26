from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Mapping


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EventLogger:
    def __init__(self, run_root: Path):
        self.run_root = run_root
        self.events_path = self.run_root / "events.jsonl"
        self.meta_path = self.run_root / "events_meta.json"
        self.lock = Lock()
        self._last_id = self._read_last_id()
        self.run_root.mkdir(parents=True, exist_ok=True)

    def _read_last_id(self) -> int:
        if not self.meta_path.exists():
            return 0
        try:
            payload = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return int(payload.get("last_id", 0))
        except Exception:
            return 0

    def _write_meta(self, last_id: int) -> None:
        self.meta_path.write_text(json.dumps({"last_id": last_id}, ensure_ascii=False), encoding="utf-8")

    def log(self, event_type: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            self._last_id += 1
            event = {
                "id": self._last_id,
                "ts": _now_iso(),
                "type": event_type,
                "data": data or {},
            }
            line = json.dumps(event, ensure_ascii=False)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._write_meta(self._last_id)
            return event

    def read_since(self, since: int = 0) -> tuple[list[dict[str, Any]], int]:
        events: list[dict[str, Any]] = []
        if not self.events_path.exists():
            return events, since
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("id", 0) > since:
                    events.append(item)
        next_since = events[-1]["id"] if events else since
        return events, next_since

    def last_id(self) -> int:
        return self._last_id
