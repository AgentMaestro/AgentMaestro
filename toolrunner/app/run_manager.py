from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .chat import ChatTranscript, MaestroChatEngine
from .event_logger import EventLogger
from .orchestrator import Orchestrator
from .srs_builder import SRSBuilder

REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_charter(run_id: str, slug: str, repo_dir: str) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "slug": slug,
        "created_at": "2026-01-01T00:00:00Z",
        "repo_dir": repo_dir,
        "srs": {"path": "srs/SRS.md", "sha256": "a" * 64},
        "models": {"maestro": {"name": "maestro"}, "apprentice": {"name": "apprentice"}},
        "allowed_tools": {
            "tier1": ["file_write", "git_add", "git_commit", "test_runner", "run_command"],
            "tier2": [],
            "git": ["git_status"],
        },
        "quality_gates": {
            "default": [
                {"name": "format", "tool": "run_command", "args": {"cmd": ["python", "--version"]}}
            ],
            "on_merge_candidate": [
                {"name": "format", "tool": "run_command", "args": {"cmd": ["python", "--version"]}}
            ],
        },
        "branch_strategy": {"type": "feature_branch", "name_template": f"agent/{{run_id}}/{slug}", "base_branch": "main"},
        "stop_conditions": {"max_cycles": 10, "max_failures": 2, "max_minutes": 60},
        "policies": {"require_approval_for": [], "prohibit_outside_workspace": True, "prefer_revert_over_reset": True, "secrets_handling": "redact"},
    }


@dataclass
class RunContext:
    run_id: str
    slug: str
    run_root: Path
    charter_path: Path
    srs_builder: SRSBuilder
    event_logger: EventLogger
    approvals_path: Path
    chat_transcript: ChatTranscript
    statuses: Dict[str, Any] = field(default_factory=dict)
    approvals_map: Dict[str, str] = field(default_factory=dict)
    stop_event: threading.Event = field(default_factory=threading.Event)
    orchestrator_thread: Optional[threading.Thread] = None
    orchestrator_instance: Optional[Orchestrator] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    srs_drafts: Dict[str, str] = field(default_factory=dict)
    latest_plan_id: Optional[str] = None

    def update_status(self, status: str, reason: str | None = None) -> None:
        with self.lock:
            self.statuses["status"] = status
            self.statuses["reason"] = reason
        self.statuses["last_updated"] = datetime.now(timezone.utc).isoformat()

    def record_approval(self, step_id: str, milestone_id: str, decision: str, scope: str, target_path: str | None) -> dict[str, Any]:
        record = {
            "step_id": step_id,
            "milestone_id": milestone_id,
            "decision": decision,
            "scope": scope,
            "path": target_path,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        history = []
        if self.approvals_path.exists():
            try:
                history = json.loads(self.approvals_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                history = []
        history.append(record)
        self.approvals_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
        self.approvals_map[step_id] = decision
        return record


class RunManager:
    def __init__(self):
        self.runs: Dict[str, RunContext] = {}
        self.lock = threading.Lock()
        self.chat_engine = MaestroChatEngine()

    def _run_dir(self, run_id: str) -> Path:
        return REPO_ROOT / ".agentmaestro" / "runs" / run_id

    def create_run(self, slug: str, repo_dir: str = ".", srs_path: str | None = None) -> RunContext:
        run_id = f"{slug}-{uuid.uuid4().hex[:8]}"
        run_root = self._run_dir(run_id)
        run_root.mkdir(parents=True, exist_ok=True)
        charter_path = run_root / "charter.json"
        charter = _default_charter(run_id, slug, repo_dir)
        if srs_path:
            charter["srs"]["path"] = srs_path
        charter_path.write_text(json.dumps(charter, indent=2), encoding="utf-8")
        srs_workspace = run_root / "srs"
        srs_builder = SRSBuilder(srs_workspace)
        event_logger = EventLogger(run_root)
        approvals_path = run_root / "approvals.json"
        chat_transcript = ChatTranscript(run_root)
        context = RunContext(
            run_id=run_id,
            slug=slug,
            run_root=run_root,
            charter_path=charter_path,
            srs_builder=srs_builder,
            event_logger=event_logger,
            approvals_path=approvals_path,
            chat_transcript=chat_transcript,
        )
        context.event_logger.log("RUN_CREATED", {"run_id": run_id, "slug": slug})
        context.update_status("created")
        with self.lock:
            self.runs[run_id] = context
        return context

    def get_run(self, run_id: str) -> RunContext:
        with self.lock:
            if run_id not in self.runs:
                raise KeyError(f"run {run_id} not found")
            return self.runs[run_id]

    def start_run(self, run_id: str) -> Dict[str, str]:
        context = self.get_run(run_id)
        if context.orchestrator_thread and context.orchestrator_thread.is_alive():
            return {"status": "already_running"}
        context.stop_event.clear()

        def approval_handler(step):
            decision = context.approvals_map.get(step.step_id)
            return decision != "deny"

        def runner():
            context.update_status("running")
            orchestrator = Orchestrator(
                REPO_ROOT,
                context.charter_path,
                tool_invoker=None,
                approval_handler=approval_handler,
                stop_event=context.stop_event,
            )
            context.orchestrator_instance = orchestrator
            result = orchestrator.orchestrate()
            context.update_status(result["status"], result.get("reason"))

        thread = threading.Thread(target=runner, daemon=True)
        context.orchestrator_thread = thread
        thread.start()
        return {"status": "running"}

    def stop_run(self, run_id: str) -> Dict[str, str]:
        context = self.get_run(run_id)
        context.stop_event.set()
        return {"status": "signaled"}

    def list_runs(self) -> Dict[str, Dict[str, Any]]:
        with self.lock:
            return {rid: ctx.statuses.copy() for rid, ctx in self.runs.items()}

    def get_any_run(self) -> Optional[RunContext]:
        with self.lock:
            for context in self.runs.values():
                return context
        return None
