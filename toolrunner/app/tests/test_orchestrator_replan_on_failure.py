from __future__ import annotations

import json
from pathlib import Path

from toolrunner.app.orchestrator import orchestrate
from toolrunner.app.tests.test_orchestrator import (
    DEFAULT_RUN_ID,
    _write_charter,
    _write_plan,
)


class SequenceToolInvoker:
    def __init__(self, responses: dict[str, list[dict]] | None = None) -> None:
        self.responses = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in (responses or {}).items()
        }
        self.calls: list = []

    def invoke(self, call, charter):
        self.calls.append(call)
        for target in (call.call_id, call.tool):
            queue = self.responses.get(target)
            if queue:
                return queue.pop(0)
        return {"call_id": call.call_id, "tool": call.tool, "ok": True, "result": {}}


def _read_events(tmp_path: Path) -> list[dict]:
    events_path = tmp_path / ".agentmaestro" / "runs" / DEFAULT_RUN_ID / "events.jsonl"
    if not events_path.exists():
        return []
    return [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]


def test_orchestrator_replans_on_failure(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(tmp_path)
    responses = {
        "format_runner": [
            {"call_id": "GATE001", "tool": "format_runner", "ok": False, "error": {"message": "tests failed"}},
            {"call_id": "GATE001", "tool": "format_runner", "ok": True},
        ]
    }
    invoker = SequenceToolInvoker(responses=responses)

    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "ok"
    run_root = tmp_path / ".agentmaestro" / "runs" / DEFAULT_RUN_ID
    recovery_path = run_root / "plans" / "recovery_1.json"
    assert recovery_path.exists()

    events = _read_events(tmp_path)
    assert any(event["type"] == "PLAN_RECOVERY_GENERATED" for event in events)
    assert any(event["type"] == "STEP_FAILED" for event in events)


def test_orchestrator_recovery_aborts_after_max_failures(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path, stop_conditions={"max_cycles": 10, "max_failures": 1, "max_minutes": 60}
    )
    _write_plan(tmp_path)
    responses = {
        "format_runner": [
            {"call_id": "GATE001", "tool": "format_runner", "ok": False},
            {"call_id": "GATE001", "tool": "format_runner", "ok": False},
        ]
    }
    invoker = SequenceToolInvoker(responses=responses)

    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "failed"
    assert "max_failures" in result["reason"]
    run_root = tmp_path / ".agentmaestro" / "runs" / DEFAULT_RUN_ID
    recovery_files = list((run_root / "plans").glob("recovery_*.json"))
    assert recovery_files
