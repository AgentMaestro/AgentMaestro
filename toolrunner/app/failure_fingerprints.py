from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FailureFingerprintTracker:
    def __init__(self, run_root: Path, window: int = 5, threshold: int = 3):
        self.run_root = run_root
        self.storage = self.run_root / "failure_fingerprints.json"
        self.window = window
        self.threshold = threshold
        self._history = self._load_history()

    def _load_history(self) -> list[str]:
        if not self.storage.exists():
            return []
        try:
            data = json.loads(self.storage.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[-self.window :]
        except json.JSONDecodeError:
            pass
        return []

    def _persist(self) -> None:
        self.storage.write_text(json.dumps(self._history[-self.window :], ensure_ascii=False), encoding="utf-8")

    def fingerprint(self, payload: dict[str, Any]) -> str:
        tool = str(payload.get("tool", "unknown"))
        error = payload.get("error") or {}
        code = error.get("code") or "nolabel"
        message = (error.get("message") or "").replace("\n", " ")[:64]
        result = payload.get("result") or {}
        stdout = (result.get("stdout") or "")[:32]
        return f"{tool}:{code}:{message}:{stdout}"

    def record(self, fingerprint: str) -> tuple[bool, str]:
        self._history.append(fingerprint)
        self._persist()
        count = sum(1 for entry in self._history if entry == fingerprint)
        return count >= self.threshold, fingerprint

    def reset(self) -> None:
        self._history = []
        self._persist()
