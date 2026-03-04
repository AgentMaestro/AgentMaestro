from __future__ import annotations

from pathlib import Path

from toolrunner.app.orchestrator import orchestrate
from toolrunner.app.tests.test_orchestrator import _write_charter, _write_plan
from toolrunner.app.tests.test_orchestrator_replan_on_failure import SequenceToolInvoker


def test_stuck_loop_detection_blocks_on_repeated_failure(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 5, "max_minutes": 60},
    )
    _write_plan(tmp_path)
    responses = {
        "C001": [
            {"call_id": "C001", "tool": "run_command", "ok": False, "error": {"message": "boom"}}
        ],
        "RECOVERY_CMD": [
            {"call_id": "RECOVERY_CMD", "tool": "run_command", "ok": False, "error": {"message": "boom"}},
            {"call_id": "RECOVERY_CMD", "tool": "run_command", "ok": False, "error": {"message": "boom"}},
            {"call_id": "RECOVERY_CMD", "tool": "run_command", "ok": False, "error": {"message": "boom"}},
        ],
    }
    invoker = SequenceToolInvoker(responses=responses)

    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "blocked"
    assert result["reason"] == "stuck_loop_repeated_failure"
    assert "remediation" in result
    assert "failure_excerpt" in result
    assert "boom" in result["failure_excerpt"]
