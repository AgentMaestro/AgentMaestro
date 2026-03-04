from __future__ import annotations

import json
from pathlib import Path

from toolrunner.app.orchestrator import orchestrate

DEFAULT_RUN_ID = "plan-consume"
DEFAULT_PLAN_ID = "plan-consume-plan"


class FakeToolInvoker:
    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list = []

    def invoke(self, call, charter):
        self.calls.append(call)
        template = {"call_id": call.call_id, "tool": call.tool, "ok": True, "result": {}}
        override = self.responses.get(call.call_id)
        if override is None:
            override = self.responses.get(call.tool)
        if override:
            template.update(override)
        return template


def _write_charter(
    tmp_path: Path,
    *,
    run_id: str = DEFAULT_RUN_ID,
    stop_conditions: dict | None = None,
    allowed_tools: dict | None = None,
) -> Path:
    agent_root = tmp_path / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    charter = {
        "schema_version": "1.0",
        "run_id": run_id,
        "slug": "plan-consume",
        "created_at": "2026-01-01T00:00:00Z",
        "repo_dir": ".",
        "srs": {"path": "srs.md", "sha256": "a" * 64},
        "models": {
            "maestro": {"name": "maestro"},
            "apprentice": {"name": "apprentice"},
        },
        "allowed_tools": allowed_tools
        or {
            "tier1": [
                "run_command",
                "format_runner",
                "lint_runner",
                "typecheck_runner",
                "test_runner",
                "repo_tree",
                "file_write",
            ],
            "tier2": [],
            "git": ["git_status", "git_add", "git_commit"],
        },
        "quality_gates": {
            "default": [
                {"name": "format", "tool": "format_runner", "args": {"mode": "check"}},
                {"name": "lint", "tool": "lint_runner", "args": {"tool": "ruff"}},
                {"name": "typecheck", "tool": "typecheck_runner", "args": {"tool": "pyright"}},
            ],
            "on_merge_candidate": [
                {"name": "format", "tool": "format_runner", "args": {"mode": "check"}},
                {"name": "lint", "tool": "lint_runner", "args": {"tool": "ruff"}},
                {"name": "typecheck", "tool": "typecheck_runner", "args": {"tool": "pyright"}},
            ],
        },
        "branch_strategy": {"type": "feature_branch", "name_template": "agent/{run_id}/{slug}"},
        "stop_conditions": stop_conditions or {"max_cycles": 10, "max_failures": 5, "max_minutes": 60},
        "policies": {
            "require_approval_for": [],
            "prohibit_outside_workspace": True,
            "prefer_revert_over_reset": True,
            "secrets_handling": "redact",
        },
    }
    charter_path = agent_root / "run_charter.json"
    charter_path.write_text(json.dumps(charter))
    return charter_path


def _write_plan(tmp_path: Path, run_id: str = DEFAULT_RUN_ID, plan_id: str = DEFAULT_PLAN_ID) -> dict[str, object]:
    plan_dir = tmp_path / ".agentmaestro" / "runs" / run_id / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "run_id": run_id,
        "created_at": "2026-01-01T00:00:00Z",
        "goal": "Execute deterministic plan",
        "assumptions": ["Plan generated for orchestrator gating tests."],
        "complete": True,
        "milestones": [
            {
                "milestone_id": "M001",
                "title": "Single milestone",
                "description": "Ensure plan consumption works",
                "steps": [
                    {
                        "step_id": "S001",
                        "intent": "drive tooling",
                        "tool_calls": [
                            {"call_id": "C001", "tool": "run_command", "args": {}},
                        ],
                        "acceptance_checks": [
                            {"name": "check", "tool": "test_runner", "args": {}}
                        ],
                    }
                ],
            }
        ],
    }
    serialized = json.dumps(plan)
    (plan_dir / f"{plan_id}.json").write_text(serialized)
    (plan_dir / "latest.json").write_text(serialized)
    return plan


def test_orchestrator_consumes_latest_plan(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(tmp_path)
    invoker = FakeToolInvoker()

    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "ok"
    assert result["reason"] == "all milestones satisfied"


def test_orchestrator_blocks_when_gates_fail(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 0, "max_minutes": 60},
    )
    _write_plan(tmp_path)
    invoker = FakeToolInvoker(responses={"format_runner": {"ok": False, "error": {"message": "lint fail"}}})

    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "failed"
    assert "max_failures" in result["reason"]

    run_root = tmp_path / ".agentmaestro" / "runs" / DEFAULT_RUN_ID
    recovery_path = run_root / "plans" / "recovery_1.json"
    assert not recovery_path.exists()


def test_orchestrator_blocks_when_repo_dirty(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    _write_plan(tmp_path)
    invoker = FakeToolInvoker(
        responses={
            "git_status": {
                "result": {
                    "branch": {"name": "main", "head_oid": "abc"},
                    "staged": ["foo.txt"],
                    "unstaged": [],
                    "untracked": [],
                }
            }
        }
    )

    result = orchestrate(str(tmp_path), str(charter_path), tool_invoker=invoker)

    assert result["status"] == "blocked"
    assert "repository not clean" in result["reason"]


def _read_events(tmp_path: Path) -> list[dict]:
    events_path = tmp_path / ".agentmaestro" / "runs" / DEFAULT_RUN_ID / "events.jsonl"
    if not events_path.exists():
        return []
    return [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]


def test_orchestrator_blocks_when_plan_requires_disallowed_tools(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        allowed_tools={"tier1": ["run_command"], "tier2": [], "git": ["git_status"]},
    )
    plan_dir = tmp_path / ".agentmaestro" / "runs" / DEFAULT_RUN_ID / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": "1.0",
        "plan_id": "plan-disallowed",
        "run_id": DEFAULT_RUN_ID,
        "created_at": "2026-01-01T00:00:00Z",
        "goal": "No go",
        "assumptions": [],
        "complete": True,
        "milestones": [
            {
                "milestone_id": "M001",
                "title": "Use repo tree",
                "description": "Should be disallowed",
                "steps": [
                    {
                        "step_id": "S001",
                        "intent": "access repo tree",
                        "tool_calls": [
                            {"call_id": "C001", "tool": "repo_tree", "args": {"root": ".", "max_depth": 1, "include_files": True, "include_dirs": True}}
                        ],
                        "acceptance_checks": [],
                    }
                ],
            }
        ],
    }
    serialized = json.dumps(plan)
    (plan_dir / f"{plan['plan_id']}.json").write_text(serialized)
    (plan_dir / "latest.json").write_text(serialized)

    result = orchestrate(str(tmp_path), str(charter_path))

    assert result["status"] == "blocked"
    assert "disallowed tools" in result["reason"]

    events = _read_events(tmp_path)
    assert any(evt["type"] == "PLAN_TOOLS_REJECTED" for evt in events)
    assert any(evt["type"] == "RUN_FINALIZED" and evt["data"].get("reason") == result["reason"] for evt in events)
