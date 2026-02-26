from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from toolrunner.app.models import (
    FileWriteArgs,
    GitAddArgs,
    GitCommitArgs,
    GitStatusArgs,
    RunCommandArgs,
    RunnerTestArgs,
)
from toolrunner.app.orchestrator import Orchestrator, Plan, ToolInvoker, now_iso
from toolrunner.app.tools.file_write import write_file
from toolrunner.app.tools.git_add import run_git_add
from toolrunner.app.tools.git_commit import run_git_commit
from toolrunner.app.tools.git_status import run_git_status
from toolrunner.app.tools.run_command import run_command
from toolrunner.app.tools.test_runner import run_tests

RUN_ID = "smoke-run"
PLAN_ID = "plan-smoke"
MILESTONE_ID = "M001"
STEP_ID = "S001"
COMMIT_MESSAGE = "Add smoke files"


class FakeMaestro:
    def __init__(self, plan: Plan, review_states: Optional[List[str]] = None) -> None:
        self._plan = plan
        self._review_states = review_states or ["done"]

    def make_plan(self) -> Plan:
        return self._plan

    def review_progress(self, plan: Plan) -> str:
        if self._review_states:
            return self._review_states.pop(0)
        return "done"


class SmokeLoopOrchestrator(Orchestrator):
    def __init__(
        self,
        repo_dir: Path,
        charter_path: Path,
        maestro: FakeMaestro,
        *,
        tool_invoker: ToolInvoker,
        approval_handler: Optional[Any] = None,
    ):
        super().__init__(repo_dir, charter_path, tool_invoker=tool_invoker, approval_handler=approval_handler)
        self._maestro = maestro

    def maestro_make_plan(self) -> Plan:
        return self._maestro.make_plan()

    def maestro_review_progress(self, plan: Plan) -> str:
        return self._maestro.review_progress(plan)


class RealToolInvoker(ToolInvoker):
    TOOL_MAP: Dict[str, tuple[Any, BaseModel]] = {
        "file_write": (write_file, FileWriteArgs),
        "git_add": (run_git_add, GitAddArgs),
        "git_commit": (run_git_commit, GitCommitArgs),
        "git_status": (run_git_status, GitStatusArgs),
        "run_command": (run_command, RunCommandArgs),
        "test_runner": (run_tests, RunnerTestArgs),
    }

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir

    def invoke(self, call: ToolCall, charter: Any) -> Dict[str, Any]:
        entry = self.TOOL_MAP.get(call.tool)
        if not entry:
            return {"call_id": call.call_id, "tool": call.tool, "ok": False, "error": {"message": f"unknown tool {call.tool}"}, "result": None}
        tool_fn, model_cls = entry
        args_instance = model_cls(**call.args)  # type: ignore[arg-type]
        response = tool_fn(self.run_dir, args_instance)
        payload = self._extract_payload(response)
        return {
            "call_id": call.call_id,
            "tool": call.tool,
            "ok": bool(payload.get("ok")),
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


def _smoke_workspace_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    workspace = root / ".agentmaestro_smoke_ws"
    workspace.mkdir(exist_ok=True)
    return workspace


def _init_git_repo(workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "agent@example.com"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Agent Maestro"], cwd=workspace, check=True, capture_output=True)


def _write_smoke_charter(workspace: Path, run_id: str) -> Path:
    agent_root = workspace / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    charter = {
        "schema_version": "1.0",
        "run_id": run_id,
        "slug": "smoke",
        "created_at": now_iso(),
        "repo_dir": ".",
        "srs": {"path": "srs.md", "sha256": "a" * 64},
        "models": {"maestro": {"name": "maestro"}, "apprentice": {"name": "apprentice"}},
        "allowed_tools": {
            "tier1": ["file_write", "test_runner", "run_command"],
            "tier2": [],
            "git": ["git_add", "git_commit", "git_status"],
        },
        "quality_gates": {
            "default": [
                {
                    "name": "gate-runner",
                    "tool": "run_command",
                    "args": {"cmd": ["python", "-c", "print('gate')"]},
                }
            ],
            "on_merge_candidate": [
                {
                    "name": "gate-runner",
                    "tool": "run_command",
                    "args": {"cmd": ["python", "-c", "print('gate')"]},
                }
            ],
        },
        "branch_strategy": {
            "type": "feature_branch",
            "name_template": "agent/{run_id}/{slug}",
            "base_branch": "main",
        },
        "stop_conditions": {"max_cycles": 10, "max_failures": 1, "max_minutes": 10},
        "policies": {
            "require_approval_for": [],
            "prohibit_outside_workspace": True,
            "prefer_revert_over_reset": True,
            "secrets_handling": "redact",
        },
    }
    path = agent_root / "run_charter.json"
    path.write_text(json.dumps(charter, indent=2))
    return path


def _build_smoke_plan(run_id: str) -> Plan:
    payload = {
        "schema_version": "1.0",
        "plan_id": PLAN_ID,
        "run_id": run_id,
        "created_at": now_iso(),
        "goal": "execute smoke workflow",
        "assumptions": ["Smoke plan assumption"],
        "complete": True,
        "milestones": [
            {
                "milestone_id": MILESTONE_ID,
                "title": "Smoke milestone",
                "description": "Smoke milestone detail",
                "steps": [
                    {
                        "step_id": STEP_ID,
                        "intent": "create files, commit, and test",
                        "tool_calls": [
                            {
                                "call_id": "C000",
                                "tool": "file_write",
                                "args": {
                                    "path": ".gitignore",
                                    "content": ".agentmaestro/\n",
                                    "mode": "text",
                                    "overwrite": True,
                                },
                            },
                            {
                                "call_id": "C001",
                                "tool": "file_write",
                                "args": {
                                    "path": "hello.py",
                                    "content": "def greet():\n    return 'Hello, smoke!'\n",
                                    "mode": "text",
                                    "overwrite": True,
                                },
                            },
                            {
                                "call_id": "C002",
                                "tool": "file_write",
                                "args": {
                                    "path": "test_hello.py",
                                    "content": "from hello import greet\n\n\ndef test_greet():\n    assert greet() == 'Hello, smoke!'\n",
                                    "mode": "text",
                                    "overwrite": True,
                                },
                            },
                            {
                                "call_id": "C003",
                                "tool": "git_add",
                                "args": {"paths": [".gitignore", "hello.py", "test_hello.py"]},
                            },
                            {
                                "call_id": "C004",
                                "tool": "git_commit",
                                "args": {"message": COMMIT_MESSAGE},
                            },
                        ],
                        "acceptance_checks": [
                            {
                                "name": "pytest smoke",
                                "tool": "test_runner",
                                "args": {
                                    "kind": "pytest",
                                    "pytest_args": ["test_hello.py"],
                                },
                            }
                        ],
                        "rollback": {"strategy": "none"},
                    }
                ],
            }
        ],
    }
    return Plan.model_validate(payload)


def _step_report_path(workspace: Path) -> Path:
    return workspace / ".agentmaestro" / "runs" / RUN_ID / "step_reports" / MILESTONE_ID / f"{STEP_ID}.json"


def test_orchestrator_smoke_real():
    workspace_root = _smoke_workspace_root()
    workspace = workspace_root / f"run-{uuid.uuid4().hex}"
    try:
        _init_git_repo(workspace)
        charter_path = _write_smoke_charter(workspace, RUN_ID)
        plan = _build_smoke_plan(RUN_ID)
        maestro = FakeMaestro(plan)
        tool_invoker = RealToolInvoker(workspace)
        orchestrator = SmokeLoopOrchestrator(
            workspace,
            charter_path,
            maestro,
            tool_invoker=tool_invoker,
        )

        result = orchestrator.orchestrate()

        assert result["status"] == "done"
        report = json.loads(_step_report_path(workspace).read_text())
        assert report["status"] == "ok"
        assert report["verification"]["overall_pass"] is True
        assert report["repo_state"]["is_clean"] is True

        git_log = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        assert git_log.stdout.strip() == COMMIT_MESSAGE

        git_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
      