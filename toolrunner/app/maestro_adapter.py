from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def propose_recovery_plan(run_id: str, failure_context: Dict[str, Any]) -> Dict[str, Any]:
    plan_id = failure_context.get("plan_id") or f"{run_id}-recovery-{uuid.uuid4().hex[:8]}"
    step_id = failure_context.get("step_id") or "recovery"
    milestone_id = failure_context.get("milestone_id") or "recovery"
    plan = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "run_id": run_id,
        "created_at": _now_iso(),
        "goal": f"Recovery plan for {failure_context.get('reason', 'failure')}",
        "assumptions": ["Recovery plan generated automatically"],
        "complete": True,
        "milestones": [
            {
                "milestone_id": milestone_id,
                "title": "Recovery milestone",
                "description": failure_context.get("reason") or "Automatic replan",
                "steps": [
                    {
                        "step_id": f"{step_id}-recovery",
                        "intent": "Fix failing tests",
                        "tool_calls": [
                            {
                                "call_id": "RECOVERY_CMD",
                                "tool": "run_command",
                                "args": {"command": "echo recovery"}
                            }
                        ],
                        "acceptance_checks": [
                            {"name": "verify", "tool": "test_runner", "args": {}}
                        ],
                    }
                ],
            }
        ],
    }
    return plan
