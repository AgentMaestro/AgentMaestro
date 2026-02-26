from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from toolrunner.app.config import COMMAND_TIMEOUT, OUTPUT_LIMIT
from toolrunner.app.orchestrator import Orchestrator, ToolCall, orchestrate
from toolrunner.app.schemas import SchemaValidationError

DEFAULT_RUN_ID = "run-contract"


class FakeToolInvoker:
    def __init__(self, responses: Dict[str, Dict[str, Any]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: List[ToolCall] = []

    def invoke(self, call: ToolCall, charter: Any) -> Dict[str, Any]:
        self.calls.append(call)
        return self.responses.get(
            call.call_id,
            {
                "call_id": call.call_id,
                "tool": call.tool,
                "ok": True,
                "result": {"args": call.args},
            },
        )


def _charter_payload(
    *,
    run_id: str = DEFAULT_RUN_ID,
    slug: str = "agent-slug",
    allowed_tools: Optional[Dict[str, List[str]]] = None,
    stop_conditions: Optional[Dict[str, int]] = None,
    require_approval_for: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "slug": slug,
        "created_at": "2026-01-01T00:00:00Z",
        "repo_dir": ".",
        "srs": {"path": "srs.md", "sha256": "a" * 64},
        "models": {
            "maestro": {"name": "maestro"},
            "apprentice": {"name": "apprentice"},
        },
        "allowed_tools": allowed_tools
        or {
            "tier1": ["format_runner", "run_command"],
            "tier2": [],
            "git": ["git_status"],
        },
        "quality_gates": {
            "default": [
                {"name": "format", "tool": "format_runner", "args": {"mode": "check"}}
            ],
            "on_merge_candidate": [
                {"name": "format", "tool": "format_runner", "args": {"mode": "check"}}
            ],
        },
        "branch_strategy": {
            "type": "feature_branch",
            "name_template": "agent/{run_id}/{slug}",
            "base_branch": "main",
        },
        "stop_conditions": stop_conditions
        or {"max_cycles": 10, "max_failures": 2, "max_minutes": 60},
        "policies": {
            "require_approval_for": require_approval_for or [],
            "prohibit_outside_workspace": True,
            "prefer_revert_over_reset": True,
            "secrets_handling": "redact",
        },
    }


def _write_charter(
    tmp_path: Path,
    *,
    allowed_tools: Optional[Dict[str, List[str]]] = None,
    stop_conditions: Optional[Dict[str, int]] = None,
    require_approval_for: Optional[List[str]] = None,
) -> Path:
    agent_root = tmp_path / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "plans").mkdir(parents=True, exist_ok=True)
    charter_path = agent_root / "run_charter.json"
    charter_path.write_text(
        json.dumps(
        _charter_payload(
            allowed_tools=allowed_tools,
            stop_conditions=stop_conditions,
            require_approval_for=require_approval_for,
        )
        )
    )
    return charter_path


def _make_step(
    *,
    step_id: str = "S001",
    tool: str = "run_command",
    tool_args: Optional[Dict[str, Any]] = None,
    risk_tags: Optional[List[str]] = None,
    requires_approval: bool = False,
    schema_version: Optional[str] = None,
) -> Dict[str, Any]:
    tool_call = {"call_id": "C001", "tool": tool, "args": tool_args or {}}
    if schema_version:
        tool_call["schema_version"] = schema_version
    return {
        "step_id": step_id,
        "intent": "exercise change",
        "tool_calls": [tool_call],
        "requires_approval": requires_approval,
        "risk_tags": risk_tags or [],
        "acceptance_checks": [
            {"name": "check", "tool": "test_runner", "args": {}}
        ],
    }


def _plan_payload(
    *,
    run_id: str = DEFAULT_RUN_ID,
    plan_id: str = "plan-contract",
    steps: Optional[List[Dict[str, Any]]] = None,
    milestones: Optional[List[Dict[str, Any]]] = None,
    complete: bool = True,
) -> Dict[str, Any]:
    payload = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "run_id": run_id,
        "created_at": "2026-01-01T00:00:00Z",
        "goal": "ship change",
        "assumptions": ["Contract test assumption"],
        "complete": complete,
        "milestones": milestones
        or [
            {
                "milestone_id": "M001",
                "title": "first milestone",
                "description": "Contract milestone detail",
                "steps": steps or [_make_step()],
            }
        ],
    }
    for milestone in payload["milestones"]:
        milestone.setdefault("description", "Contract milestone detail")
    return payload


def _write_plan(
    tmp_path: Path,
    *,
    steps: Optional[List[Dict[str, Any]]] = None,
    milestones: Optional[List[Dict[str, Any]]] = None,
    run_id: str = DEFAULT_RUN_ID,
    plan_id: str = "plan-contract",
    complete: bool = True,
) -> Path:
    plan_dir = tmp_path / ".agentmaestro" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    payload = _plan_payload(
        run_id=run_id,
        plan_id=plan_id,
        steps=steps,
        milestones=milestones,
        complete=complete,
    )
    plan_path = plan_dir / f"{plan_id}.json"
    plan_path.write_text(json.dumps(payload))
    return plan_path


def test_charter_schema_validates(tmp_path: Path):
    agent_root = tmp_path / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    invalid = _charter_payload()
    invalid.pop("slug")
    (agent_root / "run_charter.json").write_text(json.dumps(invalid))

    with pytest.raises(SchemaValidationError):
        orchestrate(str(tmp_path))


def test_plan_schema_validates(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    plan_dir = tmp_path / ".agentmaestro" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    payload = _plan_payload()
    payload.pop("goal")
    (plan_dir / "plan-contract.json").write_text(json.dumps(payload))

    with pytest.raises(SchemaValidationError):
        orchestrate(str(tmp_path), str(charter_path))


def test_tool_call_envelope_schema_validates(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    invalid_step = {
        "step_id": "S001",
        "intent": "missing args",
        "tool_calls": [{"call_id": "C001", "tool": "run_command"}],
        "acceptance_checks": [{"name": "check", "tool": "test_runner", "args": {}}],
        "rollback": {"strategy": "none"},
    }
    _write_plan(tmp_path, steps=[invalid_step])

    with pytest.raises(SchemaValidationError):
        orchestrate(str(tmp_path), str(charter_path))


def test_plan_semantics_blocks_disallowed_tool(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        allowed_tools={"tier1": ["run_command"], "tier2": [], "git": []},
    )
    _write_plan(tmp_path, steps=[_make_step(tool="git_status")])

    with pytest.raises(ValueError):
        orchestrate(str(tmp_path), str(charter_path))


def test_tool_ref_cannot_start_with_dash(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        allowed_tools={"tier1": [], "tier2": [], "git": ["git_checkout"]},
    )
    _write_plan(tmp_path, steps=[_make_step(tool="git_checkout", tool_args={"ref": "-bad"})])

    with pytest.raises(ValueError):
        orchestrate(str(tmp_path), str(charter_path))


def test_clamps_apply(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    orch = Orchestrator(Path(tmp_path), charter_path, tool_invoker=FakeToolInvoker())

    call = ToolCall(
        call_id="C001",
        tool="run_command",
        args={"timeout_ms": 2_000_000, "max_output_bytes": 2_000_000},
    )
    clamped = orch.apply_call_clamps(call)

    assert clamped.args["timeout_ms"] == COMMAND_TIMEOUT * 1000
    assert clamped.args["max_output_bytes"] == OUTPUT_LIMIT


def test_requires_approval_blocks_without_approval(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        require_approval_for=["history_rewrite"],
    )
    _write_plan(
        tmp_path,
        steps=[_make_step(requires_approval=True, risk_tags=["history_rewrite"])],
    )
    invoker = FakeToolInvoker()

    def deny(_):
        return False

    result = orchestrate(
        str(tmp_path),
        str(charter_path),
        tool_invoker=invoker,
        approval_handler=deny,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "approval denied"


def test_stop_conditions_max_failures(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 0, "max_minutes": 60},
    )
    _write_plan(tmp_path)
    responses = {
        "C001": {"call_id": "C001", "tool": "run_command", "ok": False, "error": {"code": "boom"}},
    }
    invoker = FakeToolInvoker(responses)
    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "failed"
    assert result["reason"] == "max_failures exceeded"


def test_stop_conditions_max_cycles(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 1, "max_failures": 5, "max_minutes": 60},
    )
    _write_plan(tmp_path)

    result = orchestrate(str(tmp_path), str(charter_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "max_cycles reached"
