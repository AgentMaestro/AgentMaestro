from __future__ import annotations

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolrunner.app.config import COMMAND_TIMEOUT, OUTPUT_LIMIT
from toolrunner.app.orchestrator import orchestrate
from toolrunner.app.schemas import SchemaValidationError

DEFAULT_RUN_ID = "run01"


class FakeToolInvoker:
    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list = []

    def invoke(self, call, charter):
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


def _write_charter(
    tmp_path: Path,
    *,
    run_id: str = DEFAULT_RUN_ID,
    slug: str = "agent-slug",
    allowed_tools: dict | None = None,
    stop_conditions: dict | None = None,
    require_approval_for: list[str] | None = None,
) -> Path:
    agent_root = tmp_path / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "plans").mkdir(parents=True, exist_ok=True)

    charter = {
        "schema_version": "1.0",
        "run_id": run_id,
        "slug": slug,
        "created_at": "2026-02-25T00:00:00Z",
        "repo_dir": ".",
        "srs": {"path": "srs.json", "sha256": "a" * 64},
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
        or {"max_cycles": 10, "max_failures": 1, "max_minutes": 60},
        "policies": {
            "require_approval_for": require_approval_for or [],
            "prohibit_outside_workspace": True,
            "prefer_revert_over_reset": True,
            "secrets_handling": "redact",
        },
    }
    charter_path = agent_root / "run_charter.json"
    charter_path.write_text(json.dumps(charter))
    return charter_path


def _make_step(
    *,
    step_id: str = "S001",
    tool: str = "run_command",
    tool_args: dict | None = None,
    risk_tags: list[str] | None = None,
    requires_approval: bool = False,
    schema_version: str | None = None,
) -> dict:
    tool_call = {"call_id": "C001", "tool": tool, "args": tool_args or {}}
    if schema_version:
        tool_call["schema_version"] = schema_version
    return {
        "step_id": step_id,
        "intent": "do a thing",
        "tool_calls": [tool_call],
        "requires_approval": requires_approval,
        "risk_tags": risk_tags or [],
        "acceptance_checks": [
            {"name": "check", "tool": "test_runner", "args": {}}
        ],
    }


def _write_plan(
    tmp_path: Path,
    *,
    run_id: str = DEFAULT_RUN_ID,
    plan_id: str = "plan1",
    steps: list[dict] | None = None,
    milestones: list[dict] | None = None,
    complete: bool = True,
) -> Path:
    plan_dir = tmp_path / ".agentmaestro" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_milestones = milestones or [
        {
            "milestone_id": "M001",
            "title": "Milestone one",
            "description": "Default milestone",
            "steps": steps or [_make_step()],
        }
    ]
    for milestone in plan_milestones:
        milestone.setdefault("description", "Default milestone")
    plan = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "run_id": run_id,
        "created_at": "2026-02-25T00:00:00Z",
        "goal": "ship change",
        "assumptions": ["Plan derived from orchestrator tests"],
        "complete": complete,
        "milestones": plan_milestones,
    }
    plan_path = plan_dir / f"{plan_id}.json"
    plan_path.write_text(json.dumps(plan))
    return plan_path


def _step_report_path(tmp_path: Path, run_id: str, milestone_id: str = "M001", step_id: str = "S001") -> Path:
    return (
        tmp_path
        / ".agentmaestro"
        / "runs"
        / run_id
        / "step_reports"
        / milestone_id
        / f"{step_id}.json"
    )


def test_orchestrator_completes_plan(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(tmp_path)
    invoker = FakeToolInvoker()
    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "done"
    assert result["reason"] == "all milestones satisfied"

    report_path = _step_report_path(tmp_path, DEFAULT_RUN_ID)
    report = json.loads(report_path.read_text())
    assert report["status"] == "ok"
    assert report["verification"]["overall_pass"] is True
    assert report["repo_state"]["branch"].startswith("agent/")


def test_orchestrator_handles_tool_failure(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(tmp_path)
    responses = {
        "C001": {"call_id": "C001", "tool": "run_command", "ok": False, "error": {"message": "boom"}}
    }
    invoker = FakeToolInvoker(responses)
    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "failed"
    assert result["reason"] == "max_failures exceeded"

    report_path = _step_report_path(tmp_path, DEFAULT_RUN_ID)
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["tool_results"][0]["ok"] is False


def test_orchestrator_denies_unallowed_tool(tmp_path: Path):
    allowed_tools = {"tier1": ["format_runner"], "tier2": [], "git": ["git_status"]}
    charter_path = _write_charter(tmp_path, allowed_tools=allowed_tools)
    _write_plan(tmp_path)
    invoker = FakeToolInvoker()
    with pytest.raises(ValueError):
        orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)


def test_orchestrator_clamps_tool_limits(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(
        tmp_path,
        steps=[_make_step(tool_args={"timeout_ms": 3600000, "max_output_bytes": 100_000})],
    )
    invoker = FakeToolInvoker()
    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "done"
    run_call = next(call for call in invoker.calls if call.tool == "run_command")
    assert run_call.args["timeout_ms"] == COMMAND_TIMEOUT * 1000
    assert run_call.args["max_output_bytes"] == OUTPUT_LIMIT


def test_orchestrator_requires_approval_for_risky_tags(tmp_path: Path):
    charter_path = _write_charter(tmp_path, require_approval_for=["history_rewrite"])
    _write_plan(tmp_path, steps=[_make_step(risk_tags=["history_rewrite"])])
    approvals: list[str] = []

    def approval_handler(step):
        approvals.append(step.step_id)
        return True

    invoker = FakeToolInvoker()
    result = orchestrate(
        str(tmp_path),
        str(charter_path),
        tool_invoker=invoker,
        approval_handler=approval_handler,
    )

    assert result["status"] == "done"
    assert approvals == ["S001"]


def test_orchestrator_stop_conditions_max_cycles(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 1, "max_failures": 1, "max_minutes": 10},
    )
    _write_plan(tmp_path)

    result = orchestrate(str(tmp_path), str(charter_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "max_cycles reached"


def test_run_charter_schema_validation(tmp_path: Path):
    agent_root = tmp_path / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    invalid = {
        "schema_version": "1.0",
        "run_id": DEFAULT_RUN_ID,
        "slug": "agent-slug",
        "created_at": "2026-02-25T00:00:00Z",
        "repo_dir": ".",
        "srs": {"path": "srs.json", "sha256": "a" * 64},
        "models": {"maestro": {"name": "maestro"}, "apprentice": {"name": "apprentice"}},
        "quality_gates": {
            "default": [{"name": "format", "tool": "format_runner", "args": {"mode": "check"}}],
            "on_merge_candidate": [
                {"name": "format", "tool": "format_runner", "args": {"mode": "check"}}
            ],
        },
        "branch_strategy": {
            "type": "feature_branch",
            "name_template": "agent/{run_id}/{slug}",
            "base_branch": "main",
        },
        "stop_conditions": {"max_cycles": 10, "max_failures": 1, "max_minutes": 60},
        "policies": {
            "require_approval_for": [],
            "prohibit_outside_workspace": True,
            "prefer_revert_over_reset": True,
            "secrets_handling": "redact",
        },
    }
    invalid_path = agent_root / "run_charter.json"
    invalid_path.write_text(json.dumps(invalid))

    with pytest.raises(SchemaValidationError):
        orchestrate(str(tmp_path))


def test_plan_schema_validation(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    plan_dir = tmp_path / ".agentmaestro" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    incomplete_plan = {
        "schema_version": "1.0",
        "plan_id": "plan1",
        "run_id": DEFAULT_RUN_ID,
        "created_at": "2026-02-25T00:00:00Z",
        "complete": True,
        "milestones": [],
    }
    plan_path = plan_dir / "plan1.json"
    plan_path.write_text(json.dumps(incomplete_plan))

    with pytest.raises(SchemaValidationError):
        orchestrate(str(tmp_path), str(charter_path))


def test_plan_duplicate_milestone_id(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    milestones = [
        {
            "milestone_id": "M001",
            "title": "Milestone A",
            "steps": [_make_step()],
        },
        {
            "milestone_id": "M001",
            "title": "Milestone B",
            "steps": [_make_step(step_id="S002")],
        },
    ]
    _write_plan(tmp_path, milestones=milestones)

    with pytest.raises(ValueError):
        orchestrate(str(tmp_path), str(charter_path))


def test_plan_duplicate_step_id_within_milestone(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(
        tmp_path,
        plan_id="plan_step_dup",
        steps=[
            _make_step(step_id="S001"),
            _make_step(step_id="S001", tool_args={"note": "second"}),
        ],
    )

    with pytest.raises(ValueError):
        orchestrate(str(tmp_path), str(charter_path))


def test_tool_call_envelope_validation(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(tmp_path, steps=[_make_step(schema_version="2.0")])

    with pytest.raises(SchemaValidationError):
        orchestrate(str(tmp_path), str(charter_path))
