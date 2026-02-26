from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence


class ProgressTracker:
    def __init__(self, run_root: Path, window: int = 3):
        self.run_root = run_root
        self.state_path = self.run_root / "progress_state.json"
        self.window = window
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"history": []}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"history": []}

    def _persist(self) -> None:
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")

    def observe(self, *,
        head_oid: str,
        changed_files: Sequence[str],
        gates_hash: str,
        step_id: str,
    ) -> tuple[bool, bool]:
        prev_state = self._state.get("last", {})
        progress_signals = []
        progress_signals.append(prev_state.get("head_oid") != head_oid)
        progress_signals.append(prev_state.get("step_id") != step_id)
        prev_files = set(prev_state.get("changed_files") or [])
        progress_signals.append(set(changed_files) != prev_files)
        progress_signals.append(prev_state.get("gates_hash") != gates_hash)
        progress = any(progress_signals)
        history = self._state.setdefault("history", [])
        if progress:
            history.clear()
        else:
            history.append(False)
            if len(history) > self.window:
                history = history[-self.window :]
                self._state["history"] = history
        self._state["last"] = {
            "head_oid": head_oid,
            "changed_files": list(changed_files),
            "gates_hash": gates_hash,
            "step_id": step_id,
        }
        self._persist()
        blocked = len(history) >= self.window and all(not entry for entry in history)
        return progress, blocked
