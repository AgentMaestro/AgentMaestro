import pytest

from toolrunner.app.schemas import (
    SchemaValidationError,
    validate_plan,
    validate_run_charter,
    validate_step_report,
    validate_tool_call_envelope,
)


def _run_charter_payload() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "agent123",
        "slug": "agent-slug",
        "created_at": "2025-01-01T00:00:00Z",
        "repo_dir": ".",
        "srs": {"path": "spec.md", "sha256": "a" * 64},
        "models": {
            "maestro": {"name": "maestro"},
            "apprentice": {"name": "apprentice"},
        },
        "allowed_tools": {
            "tier1": ["run_command"],
            "tier2": ["test_runner"],
            "git": ["git_status"],
        },
        "quality_gates": {
            "default": [
                {"name": "default", "tool": "run_command", "args": {}}
            ],
            "on_merge_candidate": [
                {"name": "merge", "tool": "run_command", "args": {}}
            ],
        },
        "branch_strategy": {"type": "feature_branch", "name_template": "agent/{run_id}/{slug}"},
        "stop_conditions": {"max_cycles": 1, "max_failures": 0, "max_minutes": 60},
        "policies": {
            "require_approval_for": [],
            "prohibit_outside_workspace": True,
            "prefer_revert_over_reset": True,
            "secrets_handling": "redact",
        },
    }


def test_validate_run_charter_success():
    validate_run_charter(_run_charter_payload())


def test_validate_run_charter_missing_required():
    with pytest.raises(SchemaValidationError):
        validate_run_charter({})


def _plan_payload() -> dict:
    payload = {
        "schema_version": "1.0",
        "plan_id": "plan123",
        "run_id": "agent123",
        "created_at": "2025-01-01T00:00:00Z",
        "goal": "Make progress",
        "assumptions": ["Payload assumption"],
        "complete": True,
        "milestones": [
            {
                "milestone_id": "M01",
                "title": "First stage",
                "description": "Primary milestone",
                "steps": [
                    {
                        "step_id": "S001",
                        "intent": "Do work",
                        "tool_calls": [
                            {"call_id": "C001", "tool": "run_command", "args": {}}
                        ],
                        "acceptance_checks": [
                            {"name": "check", "tool": "run_command", "args": {}}
                        ],
                    }
                ],
            }
        ],
    }
    for milestone in payload["milestones"]:
        milestone.setdefault("description", "Primary milestone")
    return payload


def test_validate_plan_success():
    validate_plan(_plan_payload())


def test_validate_plan_missing_step():
    payload = _plan_payload()
    payload["milestones"][0]["steps"] = []
    with pytest.raises(SchemaValidationError):
        validate_plan(payload)


def _step_report_payload() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "agent123",
        "plan_id": "plan123",
        "milestone_id": "M01",
        "step_id": "S001",
        "started_at": "2025-01-01T00:00:00Z",
        "finished_at": "2025-01-01T00:05:00Z",
        "status": "ok",
        "tool_results": [
            {"call_id": "C001", "tool": "run_command", "ok": True}
        ],
        "repo_state": {"branch": "main", "head_oid": "abc", "is_clean": True, "changed_files": []},
    }


def test_validate_step_report_success():
    validate_step_report(_step_report_payload())


def test_validate_step_report_blocked_requires_reason():
    payload = _step_report_payload()
    payload["status"] = "blocked"
    with pytest.raises(SchemaValidationError):
        validate_step_report(payload)


def _tool_call_payload() -> dict:
    return {
        "schema_version": "1.0",
        "call_id": "C001",
        "tool": "file_read",
        "args": {"path": "README.md"},
    }


def test_validate_tool_call_envelope_success():
    validate_tool_call_envelope(_tool_call_payload())


def test_validate_tool_call_envelope_missing_args():
    payload = _tool_call_payload()
    del payload["args"]
    with pytest.raises(SchemaValidationError):
        validate_tool_call_envelope(payload)
