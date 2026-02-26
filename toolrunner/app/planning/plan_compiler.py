from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from toolrunner.app.schemas import validate_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / ".agentmaestro" / "runs"


class PlanCompilerError(ValueError):
    pass


class ToolCall(BaseModel):
    call_id: str
    tool: str
    args: dict[str, Any] = {}


class AcceptanceCheck(BaseModel):
    name: str
    tool: str
    args: dict[str, Any]
    required: bool = True


class Step(BaseModel):
    step_id: str
    intent: str
    tool_calls: list[ToolCall]
    acceptance_checks: list[AcceptanceCheck] | None = None
    requires_approval: bool = False
    risk_tags: list[str] | None = None


class Milestone(BaseModel):
    milestone_id: str
    title: str
    description: str
    steps: list[Step]


class Plan(BaseModel):
    schema_version: str = "1.0"
    plan_id: str
    run_id: str
    created_at: str
    goal: str
    assumptions: list[str]
    complete: bool = True
    milestones: list[Milestone]


def _run_root(run_id: str) -> Path:
    path = RUNS_ROOT / run_id
    if not path.exists():
        raise PlanCompilerError(f"run {run_id} not found")
    return path


def _read_locked_sections(run_root: Path) -> dict[str, dict[str, Any]]:
    lock_path = run_root / "srs" / "SRS.lock.json"
    if not lock_path.exists():
        return {}
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanCompilerError(f"invalid SRS lock data: {exc}") from exc
    return payload.get("locked_sections", {})


def _require_sections(locked: dict[str, Any], required: Iterable[str]) -> None:
    missing = [section for section in required if section not in locked]
    if missing:
        raise PlanCompilerError(f"required SRS sections missing: {', '.join(missing)}")


def _plan_dir(run_root: Path) -> Path:
    plans = run_root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _build_milestones(run_id: str, locked_sections: dict[str, dict[str, Any]]) -> list[Milestone]:
    locked_titles = [value.get("title", section_id) for section_id, value in locked_sections.items()]
    scaffold_steps = [
        Step(
            step_id="SC-001",
            intent="Bootstrap README and pyproject placeholders",
            tool_calls=[
                ToolCall(
                    call_id="SC-001-1",
                    tool="file_write",
                    args={
                        "path": "README.md",
                        "content": f"# Maestro Plan for {run_id}\n\nLocked sections: {', '.join(locked_titles)}\n",
                    },
                ),
                ToolCall(
                    call_id="SC-001-2",
                    tool="file_write",
                    args={
                        "path": "pyproject.toml",
                        "content": "[project]\nname = \"agentmaestro-run\"\n",
                    },
                ),
            ],
        )
    ]

    repo_tree_steps = [
        Step(
            step_id="RT-001",
            intent="Snapshot repository layout",
            tool_calls=[
                ToolCall(
                    call_id="RT-001-1",
                    tool="repo_tree",
                    args={"root": ".", "max_depth": 4, "include_files": True, "include_dirs": True},
                )
            ],
        )
    ]

    implement_steps = [
        Step(
            step_id="IM-001",
            intent="Draft implementation notes guided by locked SRS",
            tool_calls=[
                ToolCall(
                    call_id="IM-001-1",
                    tool="file_write",
                    args={
                        "path": "implementation/notes.txt",
                        "content": "Implement the behaviors described in the locked SRS sections.\n",
                    },
                )
            ],
        )
    ]

    test_steps = [
        Step(
            step_id="TS-001",
            intent="Run the test suite",
            tool_calls=[
                ToolCall(
                    call_id="TS-001-1",
                    tool="test_runner",
                    args={
                        "kind": "powershell_script",
                        "script_path": "scripts/test.ps1",
                    },
                )
            ],
            acceptance_checks=[
                AcceptanceCheck(
                    name="tests pass",
                    tool="test_runner",
                    args={"kind": "powershell_script", "script_path": "scripts/test.ps1"},
                )
            ],
        )
    ]

    gate_steps = [
        Step(
            step_id="GT-001",
            intent="Execute quality gates (format, lint, typecheck)",
            tool_calls=[
                ToolCall(
                    call_id="GT-001-1",
                    tool="format_runner",
                    args={"mode": "check", "paths": ["app", "toolrunner"]},
                ),
                ToolCall(
                    call_id="GT-001-2",
                    tool="lint_runner",
                    args={"tool": "ruff", "paths": ["app", "toolrunner"]},
                ),
                ToolCall(
                    call_id="GT-001-3",
                    tool="typecheck_runner",
                    args={"tool": "pyright", "cwd": "toolrunner"},
                ),
            ],
        )
    ]

    commit_steps = [
        Step(
            step_id="CM-001",
            intent="Prepare commits for the completed work",
            tool_calls=[
                ToolCall(call_id="CM-001-1", tool="git_status", args={"repo_dir": "."}),
                ToolCall(call_id="CM-001-2", tool="git_add", args={"paths": ["README.md", "toolrunner"], "all": False}),
                ToolCall(
                    call_id="CM-001-3",
                    tool="git_commit",
                    args={"message": "feat: materialize plan work", "add_all": True},
                ),
            ],
        )
    ]

    return [
        Milestone(
            milestone_id="scaffold",
            title="Scaffold baseline",
            description="Ensure documentation and packaging artifacts exist for the run.",
            steps=scaffold_steps,
        ),
        Milestone(
            milestone_id="repo_tree",
            title="Repo tree",
            description="Capture the current repository layout.",
            steps=repo_tree_steps,
        ),
        Milestone(
            milestone_id="implement",
            title="Implement core behavior",
            description="Draft implementation notes guided by the SRS.",
            steps=implement_steps,
        ),
        Milestone(
            milestone_id="tests",
            title="Tests",
            description="Run the test harness to ensure behavior.",
            steps=test_steps,
        ),
        Milestone(
            milestone_id="gates",
            title="Gates",
            description="Run formatting, linting, and type-check gates.",
            steps=gate_steps,
        ),
        Milestone(
            milestone_id="commit",
            title="Commit",
            description="Capture and commit the completed work.",
            steps=commit_steps,
        ),
    ]


def compile_plan(run_id: str) -> Plan:
    run_root = _run_root(run_id)
    locked_sections = _read_locked_sections(run_root)
    if not locked_sections:
        raise PlanCompilerError("no locked SRS sections available")
    _require_sections(locked_sections, ["functional_requirements", "acceptance_criteria"])
    plan_id = f"plan-{uuid.uuid4().hex[:6]}"
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assumptions = [
        f"Locked sections: {', '.join(sorted(locked_sections.keys()))}",
        "Plan generated deterministically from locked SRS content.",
    ]

    plan = Plan(
        plan_id=plan_id,
        run_id=run_id,
        created_at=created_at,
        goal=f"Deliver work derived from the locked SRS for run {run_id}",
        assumptions=assumptions,
        milestones=_build_milestones(run_id, locked_sections),
    )

    plan_data = plan.model_dump()
    validate_plan(plan_data)
    plans_dir = _plan_dir(run_root)
    plan_path = plans_dir / f"{plan_id}.json"
    plan_path.write_text(json.dumps(plan_data, indent=2), encoding="utf-8")
    latest_path = plans_dir / "latest.json"
    latest_path.write_text(json.dumps(plan_data, indent=2), encoding="utf-8")
    return plan
