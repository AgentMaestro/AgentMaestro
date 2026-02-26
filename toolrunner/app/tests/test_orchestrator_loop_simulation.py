from __future__ import annotations

import importlib
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

import pytest

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from toolrunner.app import orchestrator as orchestrator_module
from toolrunner.app.models import (
    FileWriteArgs,
    GitAddArgs,
    GitCommitArgs,
    GitStatusArgs,
    RunCommandArgs,
    RunnerTestArgs,
)
from toolrunner.app.orchestrator import (
    ApprovalHandler,
    Orchestrator,
    Plan,
    RunCharter,
    Step,
    ToolCall,
    ToolInvoker,
)
from toolrunner.app.tools.file_write import write_file
from toolrunner.app.tools.git_add import run_git_add
from toolrunner.app.tools.git_commit import run_git_commit
from toolrunner.app.tools.git_status import run_git_status
from toolrunner.app.tools.run_command import run_command
from toolrunner.app.tools.test_runner import run_tests

DEFAULT_RUN_ID = "run-loop"
DEFAULT_MILESTONE_ID = "M001"
DEFAULT_STEP_ID = "S001"


class FakeToolInvoker(ToolInvoker):
    def __init__(self, responses: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.responses = responses or {}
        self.calls: List[Dict[str, Any]] = []

    def invoke(self, call: ToolCall, charter: RunCharter) -> Dict[str, Any]:
        self.calls.append({"call_id": call.call_id, "tool": call.tool, "args": call.args})
        response = self.responses.get(call.call_id) or self.responses.get(call.tool)
        result: Dict[str, Any] = {"call_id": call.call_id, "tool": call.tool, "ok": True}
        if response:
            result.update(response)
        return result


class FakeMaestro:
    def __init__(
        self,
        plan: Plan,
        *,
        review_states: Optional[List[str]] = None,
        repo_states: Optional[List[Dict[str, Any]]] = None,
        diff_summaries: Optional[List[Dict[str, Any]]] = None,
    ):
        self._plan = plan
        self._review_states = review_states or ["done"]
        self._repo_states = list(repo_states or [])
        self._diff_summaries = list(diff_summaries or [])

    def make_plan(self) -> Plan:
        return self._plan

    def review_progress(self, plan: Plan) -> str:
        if self._review_states:
            return self._review_states.pop(0)
        return "done"

    def next_repo_state(self, changed_files: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if not self._repo_states:
            return None
        state = self._repo_states.pop(0)
        if changed_files is not None:
            state = {**state, "changed_files": changed_files}
        return state

    def next_diff_summary(self) -> Optional[Dict[str, Any]]:
        if not self._diff_summaries:
            return None
        return self._diff_summaries.pop(0)


class LoopOrchestrator(Orchestrator):
    def __init__(
        self,
        repo_dir: Path,
        charter_path: Path,
        maestro: FakeMaestro,
        *,
        tool_invoker: Optional[ToolInvoker] = None,
        approval_handler: Optional[ApprovalHandler] = None,
    ):
        super().__init__(repo_dir, charter_path, tool_invoker=tool_invoker, approval_handler=approval_handler)
        self._maestro = maestro

    def maestro_make_plan(self) -> Plan:
        return self._maestro.make_plan()

    def maestro_review_progress(self, plan: Plan) -> str:
        return self._maestro.review_progress(plan)

    def collect_repo_state(self, changed_files: Optional[List[str]] = None) -> Dict[str, Any]:
        state = self._maestro.next_repo_state(changed_files)
        if state is not None:
            return state
        return super().collect_repo_state(changed_files)

    def collect_diff_summary(self, step: Step) -> Dict[str, Any]:
        summary = self._maestro.next_diff_summary()
        if summary is not None:
            return summary
        return super().collect_diff_summary(step)


class LoopOrchestratorWithRollback(LoopOrchestrator):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rollback_invoked = False

    def maybe_rollback(self, step: Step) -> None:
        self.rollback_invoked = True
        super().maybe_rollback(step)


class RealToolInvoker(ToolInvoker):
    TOOL_MAP: Dict[str, tuple[Callable[[Path, BaseModel], JSONResponse], BaseModel]] = {
        "file_write": (write_file, FileWriteArgs),
        "git_add": (run_git_add, GitAddArgs),
        "git_commit": (run_git_commit, GitCommitArgs),
        "git_status": (run_git_status, GitStatusArgs),
        "run_command": (run_command, RunCommandArgs),
        "test_runner": (run_tests, RunnerTestArgs),
    }

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir

    def invoke(self, call: ToolCall, charter: RunCharter) -> Dict[str, Any]:
        entry = self.TOOL_MAP.get(call.tool)
        if not entry:
            return {
                "call_id": call.call_id,
                "tool": call.tool,
                "ok": False,
                "error": {"message": f"unknown tool {call.tool}"},
                "result": None,
            }
        tool_fn, model_cls = entry
        args_instance = model_cls(**call.args)
        response = tool_fn(self.run_dir, args_instance)
        payload = self._extract_payload(response)
        return {
            "call_id": call.call_id,
            "tool": call.tool,
            "ok": payload.get("ok", False),
            "error": payload.get("error"),
            "result": payload.get("result"),
        }

    @staticmethod
    def _extract_payload(response: Any) -> Dict[str, Any]:
        body = getattr(response, "body", "")
        if isinstance(body, bytes):
            text = body.decode("utf-8")
        else:
            text = str(body)
        return json.loads(text)


def _write_charter(
    tmp_path: Path,
    *,
    stop_conditions: Optional[Dict[str, int]] = None,
    require_approval_for: Optional[List[str]] = None,
) -> Path:
    agent_root = tmp_path / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "run_id": DEFAULT_RUN_ID,
        "slug": "agent-loop",
        "created_at": "2026-01-01T00:00:00Z",
        "repo_dir": ".",
        "srs": {"path": "srs.md", "sha256": "a" * 64},
        "models": {
            "maestro": {"name": "maestro"},
            "apprentice": {"name": "apprentice"},
        },
        "allowed_tools": {
            "tier1": ["run_command", "format_runner"],
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
    charter_path.write_text(json.dumps(payload))
    return charter_path


def _make_step(
    *,
    step_id: str = DEFAULT_STEP_ID,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    requires_approval: bool = False,
    risk_tags: Optional[List[str]] = None,
    call_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    call_entry = {"call_id": "C001", "tool": "run_command", "args": {}}
    if call_overrides:
        call_entry.update(call_overrides)
    return {
        "step_id": step_id,
        "intent": "execute tooling",
        "tool_calls": tool_calls or [call_entry],
        "requires_approval": requires_approval,
        "risk_tags": risk_tags or [],
        "acceptance_checks": [
            {"name": "verify", "tool": "test_runner", "args": {}}
        ],
    }


def _plan_payload(
    *,
    steps: Optional[List[Dict[str, Any]]] = None,
    complete: bool = True,
) -> Dict[str, Any]:
    payload = {
        "schema_version": "1.0",
        "plan_id": "plan-loop",
        "run_id": DEFAULT_RUN_ID,
        "created_at": "2026-01-01T00:00:00Z",
        "goal": "simulate loop",
        "assumptions": ["Simulated plan assumption"],
        "complete": complete,
        "milestones": [
            {
                "milestone_id": DEFAULT_MILESTONE_ID,
                "title": "Milestone loop",
                "description": "Loop milestone detail",
                "steps": steps or [_make_step()],
            }
        ],
    }
    for milestone in payload["milestones"]:
        milestone.setdefault("description", "Loop milestone detail")
    return payload


def _build_plan(**kwargs: Any) -> Plan:
    payload = _plan_payload(**kwargs)
    return Plan.model_validate(payload)


def _step_report_path(tmp_path: Path, milestone_id: str = DEFAULT_MILESTONE_ID, step_id: str = DEFAULT_STEP_ID) -> Path:
    return (
        tmp_path
        / ".agentmaestro"
        / "runs"
        / DEFAULT_RUN_ID
        / "step_reports"
        / milestone_id
        / f"{step_id}.json"
    )


def test_loop_simulation_happy_path(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    plan = _build_plan()
    fake_maestro = FakeMaestro(
        plan,
        repo_states=[
            {"branch": "agent/main", "head_oid": "abc", "is_clean": True, "changed_files": []}
        ],
        diff_summaries=[{"paths": ["file"], "truncated": False}],
    )
    invoker = FakeToolInvoker()
    orchestrator = LoopOrchestrator(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=invoker,
    )

    result = orchestrator.orchestrate()

    assert result["status"] == "done"
    assert result["reason"] == "all milestones satisfied"

    report = json.loads(_step_report_path(tmp_path).read_text())
    assert report["status"] == "ok"
    assert report["verification"]["overall_pass"] is True
    assert report["repo_state"]["is_clean"] is True
    assert invoker.calls[-1]["tool"] == "format_runner"


def test_loop_simulation_tool_failure(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 0, "max_minutes": 60},
    )
    plan = _build_plan(
        steps=[
            _make_step(
                tool_calls=[
                    {"call_id": "C001", "tool": "run_command", "args": {}},
                    {"call_id": "C002", "tool": "run_command", "args": {}},
                ]
            )
        ]
    )
    fake_maestro = FakeMaestro(
        plan,
        repo_states=[
            {"branch": "agent/main", "head_oid": "abc", "is_clean": False, "changed_files": ["foo"]}
        ],
        diff_summaries=[{"paths": ["foo"], "truncated": True}],
    )
    invoker = FakeToolInvoker(responses={"C002": {"ok": False, "error": {"message": "boom"}}})
    orchestrator = LoopOrchestrator(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=invoker,
    )

    result = orchestrator.orchestrate()

    assert result["status"] == "failed"
    assert result["reason"] == "max_failures exceeded"

    report = json.loads(_step_report_path(tmp_path).read_text())
    assert report["status"] == "failed"
    assert report["tool_results"][-1]["ok"] is False
    assert report["repo_state"]["is_clean"] is False
    assert "diff_summary" not in report


def test_loop_simulation_gate_failure(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 0, "max_minutes": 60},
    )
    plan = _build_plan()
    fake_maestro = FakeMaestro(
        plan,
        repo_states=[{"branch": "agent/main", "head_oid": "abc", "is_clean": True, "changed_files": []}],
        diff_summaries=[{"paths": ["foo"], "truncated": False}],
    )
    invoker = FakeToolInvoker(responses={"GATE001": {"ok": False, "error": {"message": "lint fail"}}})
    orchestrator = LoopOrchestrator(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=invoker,
    )

    result = orchestrator.orchestrate()

    assert result["status"] == "failed"
    assert result["reason"] == "max_failures exceeded"

    report = json.loads(_step_report_path(tmp_path).read_text())
    assert report["status"] == "failed"
    assert report["verification"]["overall_pass"] is False
    assert report["verification"]["gates"][0]["ok"] is False


def test_loop_simulation_blocked_by_approval(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    plan = _build_plan(steps=[_make_step(requires_approval=True)])
    fake_maestro = FakeMaestro(plan)
    invoker = FakeToolInvoker()

    orchestrator = LoopOrchestrator(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=invoker,
        approval_handler=lambda _: False,
    )

    result = orchestrator.orchestrate()

    assert result["status"] == "blocked"
    assert result["reason"] == "approval denied"
    assert not _step_report_path(tmp_path).exists()
    assert all(call["tool"] == "git_status" for call in invoker.calls)


def test_loop_simulation_max_minutes_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 10, "max_minutes": 1},
    )
    plan = _build_plan()
    fake_maestro = FakeMaestro(plan)
    invoker = FakeToolInvoker()

    sequence = iter([0.0, 120.0] + [120.0] * 50)
    monkeypatch.setattr(orchestrator_module.time, "monotonic", lambda: next(sequence, 120.0))

    orchestrator = LoopOrchestrator(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=invoker,
    )
    result = orchestrator.orchestrate()

    assert result["status"] == "blocked"
    assert result["reason"] == "max_minutes reached"


def test_loop_simulation_risk_tag_triggers_approval(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 1, "max_minutes": 60},
        require_approval_for=["history_rewrite"],
    )
    plan = _build_plan(steps=[_make_step(risk_tags=["history_rewrite"])])
    fake_maestro = FakeMaestro(plan)
    approvals: list[str] = []

    orchestrator = LoopOrchestrator(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=FakeToolInvoker(),
        approval_handler=lambda step: approvals.append(step.step_id) or False,
    )

    result = orchestrator.orchestrate()

    assert result["status"] == "blocked"
    assert result["reason"] == "approval denied"
    assert approvals == [DEFAULT_STEP_ID]


def test_loop_simulation_clamp_overrides(tmp_path: Path):
    charter_path = _write_charter(tmp_path)
    plan = _build_plan(
        steps=[
                _make_step(
                    call_overrides={
                        "timeout_ms_override": 2048,
                        "max_output_bytes_override": 4096,
                        "args": {"timeout_ms": 9999, "max_output_bytes": 9999},
                    }
                )
        ]
    )
    fake_maestro = FakeMaestro(plan)
    invoker = FakeToolInvoker()

    orchestrator = LoopOrchestrator(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=invoker,
    )

    result = orchestrator.orchestrate()

    assert result["status"] == "done"
    run_call = next(call for call in invoker.calls if call["tool"] == "run_command")
    assert run_call["args"]["timeout_ms"] == 2048
    assert run_call["args"]["max_output_bytes"] == 4096


def test_loop_simulation_rollback_called_on_failure(tmp_path: Path):
    charter_path = _write_charter(
        tmp_path,
        stop_conditions={"max_cycles": 10, "max_failures": 0, "max_minutes": 60},
    )
    plan = _build_plan(
        steps=[
            _make_step(
                tool_calls=[
                    {"call_id": "C001", "tool": "run_command", "args": {}},
                    {"call_id": "C002", "tool": "run_command", "args": {}},
                ]
            )
        ]
    )
    fake_maestro = FakeMaestro(
        plan,
        repo_states=[{"branch": "agent/main", "head_oid": "abc", "is_clean": False, "changed_files": ["foo"]}],
        diff_summaries=[{"paths": ["foo"], "truncated": True}],
    )
    invoker = FakeToolInvoker(responses={"C002": {"ok": False, "error": {"message": "boom"}}})

    orchestrator = LoopOrchestratorWithRollback(
        tmp_path,
        charter_path,
        maestro=fake_maestro,
        tool_invoker=invoker,
    )

    result = orchestrator.orchestrate()

    assert result["status"] == "failed"
    assert orchestrator.rollback_invoked
