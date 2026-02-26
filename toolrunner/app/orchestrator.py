from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Protocol, Set

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import COMMAND_TIMEOUT, OUTPUT_LIMIT
from .schemas import (
    validate_plan,
    validate_run_charter,
    validate_step_report,
    validate_tool_call_envelope,
)
from .event_logger import EventLogger
from .failure_fingerprints import FailureFingerprintTracker
from .progress_tracker import ProgressTracker


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AllowedTools(BaseModel):
    tier1: List[str] = Field(default_factory=list)
    tier2: List[str] = Field(default_factory=list)
    git: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    def as_set(self) -> Set[str]:
        return set(self.tier1 + self.tier2 + self.git)


class Policies(BaseModel):
    require_approval_for: List[str] = Field(default_factory=list)
    prohibit_outside_workspace: bool = True
    prefer_revert_over_reset: bool = True
    secrets_handling: Literal["never_print", "redact", "allow"] = "redact"

    model_config = ConfigDict(extra="forbid")


class GateCall(BaseModel):
    name: str
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    required: bool = True

    model_config = ConfigDict(extra="forbid")


class Rollback(BaseModel):
    strategy: Literal["none", "git_revert", "git_reset_soft", "git_reset_hard"]
    target: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ToolCall(BaseModel):
    call_id: str
    tool: str
    schema_version: str = Field(default="1.0")
    run_id: Optional[str] = None
    repo_dir: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms_override: Optional[int] = None
    max_output_bytes_override: Optional[int] = None
    note: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class Step(BaseModel):
    step_id: str
    intent: str
    tool_calls: List[ToolCall] = Field(default_factory=list)
    requires_approval: bool = False
    risk_tags: List[str] = Field(default_factory=list)
    acceptance_checks: List[GateCall] = Field(default_factory=list)
    rollback: Rollback = Field(default_factory=lambda: Rollback(strategy="none"))

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def validate_tool_calls(self) -> "Step":
        call_ids = [call.call_id for call in self.tool_calls]
        if not call_ids:
            raise ValueError("step must include at least one tool_call")
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("tool_call.call_id values must be unique")
        return self


class Milestone(BaseModel):
    milestone_id: str
    title: str
    description: Optional[str] = None
    steps: List[Step] = Field(default_factory=list)
    quality_gates_override: Optional[List[GateCall]] = None

    model_config = ConfigDict(extra="ignore")


class Plan(BaseModel):
    schema_version: str
    plan_id: str
    run_id: str
    created_at: str
    goal: str
    assumptions: List[str] = Field(default_factory=list)
    context: Optional[Dict[str, Any]] = None
    milestones: List[Milestone] = Field(default_factory=list)
    complete: bool = False

    model_config = ConfigDict(extra="allow")


class QualityGates(BaseModel):
    default: List[GateCall]
    on_merge_candidate: List[GateCall]

    model_config = ConfigDict(extra="forbid")


class BranchStrategy(BaseModel):
    type: Literal["feature_branch"]
    name_template: str
    base_branch: str = "main"

    model_config = ConfigDict(extra="forbid")


class StopConditions(BaseModel):
    max_cycles: int = Field(default=100, ge=1)
    max_failures: int = Field(default=5, ge=0)
    max_minutes: int = Field(default=60, ge=1)

    model_config = ConfigDict(extra="forbid")


class RunCharter(BaseModel):
    schema_version: str
    run_id: str
    slug: str
    created_at: str
    created_by: Optional[str] = None
    repo_dir: str
    srs: Dict[str, str]
    models: Dict[str, Any]
    allowed_tools: AllowedTools
    quality_gates: QualityGates
    branch_strategy: BranchStrategy
    stop_conditions: StopConditions
    policies: Policies

    model_config = ConfigDict(extra="ignore")

    def tool_allowlist(self) -> Set[str]:
        return self.allowed_tools.as_set()

    def branch_slug(self, plan: Optional[Plan] = None) -> str:
        return self.slug


class ToolInvoker(Protocol):
    def invoke(self, call: ToolCall, charter: RunCharter) -> Dict[str, Any]:
        ...


class DefaultToolInvoker:
    def invoke(self, call: ToolCall, charter: RunCharter) -> Dict[str, Any]:
        return {"call_id": call.call_id, "tool": call.tool, "ok": True, "result": {"message": "ok"}}


class CallableToolInvoker:
    def __init__(self, fn: Callable[[Dict[str, Any], RunCharter], Dict[str, Any]]):
        self._fn = fn

    def invoke(self, call: ToolCall, charter: RunCharter) -> Dict[str, Any]:
        return self._fn(call.model_dump(), charter)


ApprovalHandler = Callable[[Step], bool]

DEFAULT_CALL_TIMEOUT_MS = COMMAND_TIMEOUT * 1000
DEFAULT_CALL_OUTPUT_BYTES = OUTPUT_LIMIT


class Orchestrator:
    def __init__(
        self,
        repo_dir: Path,
        charter_path: Optional[Path] = None,
        tool_invoker: Optional[ToolInvoker] = None,
        approval_handler: Optional[ApprovalHandler] = None,
        stop_event: threading.Event | None = None,
    ):
        self.repo_dir = repo_dir
        self.charter = load_and_validate_run_charter(repo_dir, charter_path)
        self.tool_invoker = tool_invoker or DefaultToolInvoker()
        self.approval_handler = approval_handler or (lambda _: True)
        self.agent_root = self.repo_dir / ".agentmaestro"
        self.plans_dir = self.agent_root / "plans"
        self.run_root = self.agent_root / "runs" / self.charter.run_id
        self.run_plans_dir = self.run_root / "plans"
        self.step_reports_base = self.run_root / "step_reports"
        self.artifacts_dir = self.run_root / "artifacts"
        self.branch_name = ""
        self.run_start_monotonic = 0.0
        self.event_logger: EventLogger | None = None
        self.failure_tracker: FailureFingerprintTracker | None = None
        self.progress_tracker: ProgressTracker | None = None
        self.stop_event = stop_event

    def orchestrate(self) -> Dict[str, str]:
        self.ensure_agent_workspace_dirs()
        self.ensure_branch()
        failures = 0
        cycle = 0
        self.run_start_monotonic = time.monotonic()
        self._init_helpers()
        self.event_logger.log("RUN_STARTED", {"run_id": self.charter.run_id, "branch": self.branch_name})

        while True:
            if self._max_minutes_exceeded():
                return self.finalize("blocked", "max_minutes reached")
            if cycle >= self.charter.stop_conditions.max_cycles:
                return self.finalize("blocked", "max_cycles reached")
            if self._stop_requested():
                return self.finalize("blocked", "stopped by request")

            plan = self.maestro_make_plan()
            self.ensure_branch(plan)
            self.event_logger.log("PLAN_LOADED", {"plan_id": plan.plan_id, "milestones": len(plan.milestones)})

            for milestone in plan.milestones:
                milestone_failed = False
                for step in milestone.steps:
                    cycle += 1
                    if self._max_minutes_exceeded():
                        return self.finalize("blocked", "max_minutes reached")
                    if cycle >= self.charter.stop_conditions.max_cycles:
                        return self.finalize("blocked", "max_cycles reached")

                    if self.step_requires_approval(step):
                        self._log_event(
                            "APPROVAL_REQUESTED",
                            {
                                "milestone": milestone.milestone_id,
                                "step": step.step_id,
                                "risk_tags": step.risk_tags,
                            },
                        )
                        decision = self.request_user_approval(step)
                        self._log_event(
                            "APPROVAL_DECISION",
                            {
                                "step": step.step_id,
                                "milestone": milestone.milestone_id,
                                "decision": "approved" if decision else "denied",
                            },
                        )
                        if not decision:
                            return self.finalize("blocked", "approval denied")

                    self.validate_step(step)
                    step_started_at = now_iso()
                    tool_results: List[Dict[str, Any]] = []
                    step_failed = False

                    self.event_logger.log("STEP_STARTED", {"milestone": milestone.milestone_id, "step": step.step_id})
                    for call in step.tool_calls:
                        if not self.tool_allowed(call.tool):
                            tool_results.append(self.tool_result_denied(call))
                            report = self.build_step_report(
                                plan,
                                milestone,
                                step,
                                step_started_at,
                                now_iso(),
                                "failed",
                                tool_results,
                                self.collect_repo_state(),
                                blocked_reason="tool not allowed",
                                failure_count=failures,
                                cycle_index=cycle,
                            )
                            self.persist_step_report(step, milestone, plan, report)
                            return self.finalize("failed", "tool not allowed")

                        clamped_call = self.apply_call_clamps(call)
                        self.event_logger.log(
                            "TOOL_CALLED",
                            {
                                "call_id": clamped_call.call_id,
                                "tool": clamped_call.tool,
                                "args": clamped_call.args,
                            },
                        )
                        result = self.tool_invoker.invoke(clamped_call, self.charter)
                        tool_results.append(
                            {
                                "call_id": clamped_call.call_id,
                                "tool": clamped_call.tool,
                                "ok": bool(result.get("ok")),
                                "error": result.get("error"),
                                "result": result.get("result"),
                            }
                        )

                        if not result.get("ok"):
                            self.event_logger.log("TOOL_FAILURE", {"call_id": clamped_call.call_id, "tool": clamped_call.tool, "error": result.get("error")})
                            failures += 1
                            self.maybe_rollback(step)
                            report = self.build_step_report(
                                plan,
                                milestone,
                                step,
                                step_started_at,
                                now_iso(),
                                "failed",
                                tool_results,
                                self.collect_repo_state(),
                                failure_count=failures,
                                cycle_index=cycle,
                            )
                            self.persist_step_report(step, milestone, plan, report)
                            env_block = self._check_environment_error(result)
                            if env_block:
                                return env_block
                            blocked_reason = self._check_failure_signature(result)
                            if blocked_reason:
                                return blocked_reason
                            if failures >= self.charter.stop_conditions.max_failures:
                                return self.finalize("failed", "max_failures exceeded")
                            step_failed = True
                            break

                    if step_failed:
                        milestone_failed = True
                        break

                    verification = self.run_quality_gates(step, milestone)
                    self._log_event("GATES_RUN", {"step": step.step_id, "milestone": milestone.milestone_id, "verification": verification})
                    diff_summary = self.collect_diff_summary(step)
                    status = "ok" if verification["overall_pass"] else "failed"
                    if status != "ok":
                        failures += 1
                        self.maybe_rollback(step)

                    repo_state = self.collect_repo_state(diff_summary.get("paths") if diff_summary else None)
                    report = self.build_step_report(
                        plan,
                        milestone,
                        step,
                        step_started_at,
                        now_iso(),
                        status,
                        tool_results,
                        repo_state,
                        diff_summary=diff_summary,
                        verification=verification,
                        failure_count=failures,
                        cycle_index=cycle,
                    )
                    self.persist_step_report(step, milestone, plan, report)
                    self._log_event("STEP_REPORT_WRITTEN", {"step": step.step_id, "milestone": milestone.milestone_id, "status": status})
                    progress_block = self._check_progress(repo_state, verification, step.step_id)
                    if progress_block:
                        return progress_block

                    if status != "ok":
                        if failures >= self.charter.stop_conditions.max_failures:
                            return self.finalize("failed", "max_failures exceeded")
                        milestone_failed = True
                        break

            if milestone_failed:
                break

            final_state = self.maestro_review_progress(plan)
            if final_state == "done":
                return self.finalize("done", "all milestones satisfied")

    def _stop_requested(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def ensure_agent_workspace_dirs(self) -> None:
        self.agent_root.mkdir(parents=True, exist_ok=True)
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.run_plans_dir.mkdir(parents=True, exist_ok=True)
        self.step_reports_base.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.persist_run_charter()

    def _max_minutes_exceeded(self) -> bool:
        elapsed_minutes = (time.monotonic() - self.run_start_monotonic) / 60
        return elapsed_minutes >= self.charter.stop_conditions.max_minutes

    def ensure_branch(self, plan: Optional[Plan] = None) -> None:
        slug = self.charter.branch_slug(plan)
        template = self.charter.branch_strategy.name_template
        try:
            self.branch_name = template.format(run_id=self.charter.run_id, slug=slug)
        except Exception:
            self.branch_name = template
        self.ensure_branch_state()

    def ensure_branch_state(self) -> None:
        # TODO: use git_status/git_branch_create/git_checkout once tooling is wired up.
        check_call = ToolCall(
            call_id="BRANCH000",
            tool="git_status",
            args={"repo_dir": str(self.repo_dir), "porcelain": "v2", "include_untracked": True},
        )
        try:
            self.tool_invoker.invoke(check_call, self.charter)
        except Exception:
            pass

    def maestro_make_plan(self) -> Plan:
        plan_path, data = self._load_plan_data()
        validate_plan(data)
        plan = Plan.model_validate(data)
        self.ensure_plan_semantics(plan)
        self.persist_plan(plan)
        return plan

    def ensure_plan_semantics(self, plan: Plan) -> None:
        if plan.run_id != self.charter.run_id:
            raise ValueError("plan.run_id does not match charter run_id")

        allowed_tools = self.charter.tool_allowlist()
        approval_tags = set(self.charter.policies.require_approval_for)

        milestone_ids: Set[str] = set()
        for milestone in plan.milestones:
            if milestone.milestone_id in milestone_ids:
                raise ValueError(f"duplicate milestone_id {milestone.milestone_id}")
            milestone_ids.add(milestone.milestone_id)

            step_ids: Set[str] = set()
            for step in milestone.steps:
                if step.step_id in step_ids:
                    raise ValueError(
                        f"duplicate step_id {step.step_id} in milestone {milestone.milestone_id}"
                    )
                step_ids.add(step.step_id)

                if not step.tool_calls:
                    raise ValueError(f"step {step.step_id} has no tool_calls")

                call_ids: Set[str] = set()
                for call in step.tool_calls:
                    if call.call_id in call_ids:
                        raise ValueError(f"duplicate tool_call.call_id {call.call_id} in {step.step_id}")
                    call_ids.add(call.call_id)
                    if allowed_tools and call.tool not in allowed_tools:
                        raise ValueError(f"tool {call.tool} not allowed by charter")
                    ref = call.args.get("ref")
                    if isinstance(ref, str) and ref.startswith("-"):
                        raise ValueError("ref must not start with '-'")

                if approval_tags and set(step.risk_tags) & approval_tags:
                    step.requires_approval = True

    def persist_plan(self, plan: Plan) -> None:
        plan_path = self.run_plans_dir / f"{plan.plan_id}.json"
        with plan_path.open("w", encoding="utf-8") as handle:
            json.dump(plan.model_dump(), handle, indent=2)

    def persist_run_charter(self) -> None:
        target = self.run_root / "charter.json"
        with target.open("w", encoding="utf-8") as handle:
            json.dump(self.charter.model_dump(), handle, indent=2)

    def _load_plan_data(self) -> tuple[Path, Dict[str, Any]]:
        if not self.plans_dir.exists():
            raise FileNotFoundError(f"{self.plans_dir} does not exist")
        matches = []
        for path in self.plans_dir.glob("*.json"):
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("run_id") == self.charter.run_id:
                matches.append((path, data))
        if not matches:
            raise FileNotFoundError("no plan found for run")
        if len(matches) > 1:
            raise ValueError("multiple plan candidates found")
        return matches[0]

    def step_requires_approval(self, step: Step) -> bool:
        if step.requires_approval:
            return True
        for tag in step.risk_tags:
            if tag in self.charter.policies.require_approval_for:
                return True
        return False

    def request_user_approval(self, step: Step) -> bool:
        return self.approval_handler(step)

    def tool_allowed(self, tool: str) -> bool:
        allowlist = self.charter.tool_allowlist()
        if not allowlist:
            return True
        return tool in allowlist

    def tool_result_denied(self, call: ToolCall) -> Dict[str, Any]:
        return {
            "call_id": call.call_id,
            "tool": call.tool,
            "ok": False,
            "error": {"code": "tool_runner.TOOL_NOT_ALLOWED", "message": "tool not allowed"},
            "result": None,
        }

    def apply_call_clamps(self, call: ToolCall) -> ToolCall:
        args = dict(call.args)
        timeout_target = (
            call.timeout_ms_override
            if call.timeout_ms_override is not None
            else args.get("timeout_ms", DEFAULT_CALL_TIMEOUT_MS)
        )
        args["timeout_ms"] = min(timeout_target, DEFAULT_CALL_TIMEOUT_MS)
        output_target = (
            call.max_output_bytes_override
            if call.max_output_bytes_override is not None
            else args.get("max_output_bytes", DEFAULT_CALL_OUTPUT_BYTES)
        )
        args["max_output_bytes"] = min(output_target, DEFAULT_CALL_OUTPUT_BYTES)
        return call.model_copy(update={"args": args})

    def validate_step(self, step: Step) -> None:
        if not step.tool_calls:
            raise ValueError("step has no tool_calls")
        enriched_calls: List[ToolCall] = []
        seen: Set[str] = set()
        for call in step.tool_calls:
            if call.call_id in seen:
                raise ValueError("duplicate tool call id")
            seen.add(call.call_id)
            enriched_call = call.model_copy(
                update={
                    "run_id": self.charter.run_id,
                    "repo_dir": str(self.repo_dir),
                }
            )
            enriched_calls.append(enriched_call)
            validate_tool_call_envelope(enriched_call.model_dump(exclude_none=True))
        step.tool_calls = enriched_calls

    def collect_repo_state(self, changed_files: Optional[List[str]] = None) -> Dict[str, Any]:
        repo_state = {
            "branch": self.branch_name,
            "head_oid": "0000000000000000000000000000000000000000",
            "is_clean": True,
            "changed_files": changed_files or [],
        }
        git_call = ToolCall(
            call_id="STATUS000",
            tool="git_status",
            args={"repo_dir": str(self.repo_dir), "porcelain": "v2", "include_untracked": True},
        )
        try:
            status = self.tool_invoker.invoke(git_call, self.charter)
            if status.get("ok"):
                payload = status.get("result") or {}
                branch_info = payload.get("branch") or {}
                repo_state["branch"] = branch_info.get("name") or self.branch_name
                repo_state["head_oid"] = branch_info.get("head_oid") or repo_state["head_oid"]
                repo_state["is_clean"] = payload.get("is_clean", repo_state["is_clean"])
                if changed_files is None:
                    staged = payload.get("staged") or []
                    unstaged = payload.get("unstaged") or []
                    untracked = payload.get("untracked") or []
                    repo_state["changed_files"] = staged + unstaged + untracked
                else:
                    repo_state["changed_files"] = changed_files
                return repo_state
        except Exception:
            pass
        return repo_state

    def collect_diff_summary(self, step: Step) -> Dict[str, Any]:
        summary = {"paths": [], "truncated": False}
        artifact_dir = self.artifacts_dir / step.step_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "git_diff.json"
        with artifact_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        return summary

    def run_quality_gates(self, step: Step, milestone: Milestone) -> Dict[str, Any]:
        gates = milestone.quality_gates_override or self.charter.quality_gates.default
        results: List[Dict[str, Any]] = []
        overall = True
        for idx, gate in enumerate(gates, start=1):
            gate_call = ToolCall(
                call_id=f"GATE{idx:03}",
                tool=gate.tool,
                args=gate.args,
            )
            clamped = self.apply_call_clamps(gate_call)
            result = self.tool_invoker.invoke(clamped, self.charter)
            ok = bool(result.get("ok"))
            results.append(
                {
                    "name": gate.name,
                    "tool": gate.tool,
                    "ok": ok,
                    "summary": result.get("result") or {},
                    "raw": result,
                }
            )
            if gate.required and not ok:
                overall = False
        return {"overall_pass": overall, "gates": results}

    def maybe_rollback(self, step: Step) -> None:
        # Placeholder: real implementations might revert commits, reset, etc.
        pass

    def build_step_report(
        self,
        plan: Plan,
        milestone: Milestone,
        step: Step,
        started_at: str,
        finished_at: str,
        status: str,
        tool_results: List[Dict[str, Any]],
        repo_state: Dict[str, Any],
        diff_summary: Optional[Dict[str, Any]] = None,
        verification: Optional[Dict[str, Any]] = None,
        blocked_reason: Optional[str] = None,
        failure_count: Optional[int] = None,
        cycle_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": self.charter.run_id,
            "plan_id": plan.plan_id,
            "milestone_id": milestone.milestone_id,
            "step_id": step.step_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "tool_results": tool_results,
            "repo_state": repo_state,
        }
        if failure_count is not None:
            report["failure_count"] = failure_count
        if cycle_index is not None:
            report["cycle_index"] = cycle_index
        if blocked_reason:
            report["blocked_reason"] = blocked_reason
        if diff_summary is not None:
            report["diff_summary"] = diff_summary
        if verification is not None:
            report["verification"] = verification
        return report

    def persist_step_report(
        self,
        step: Step,
        milestone: Milestone,
        plan: Plan,
        report: Dict[str, Any],
    ) -> None:
        milestone_dir = self.step_reports_base / milestone.milestone_id
        milestone_dir.mkdir(parents=True, exist_ok=True)
        report_path = milestone_dir / f"{step.step_id}.json"
        validate_step_report(report)
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

    def maestro_review_progress(self, plan: Plan) -> str:
        return "done" if plan.complete else "continue"

    def finalize(self, status: str, reason: str) -> Dict[str, str]:
        print("finalize called", status, reason)
        self._log_event("RUN_FINALIZED", {"status": status, "reason": reason})
        return {"status": status, "reason": reason}

    def _init_helpers(self) -> None:
        if self.event_logger is None:
            self.event_logger = EventLogger(self.run_root)
        if self.failure_tracker is None:
            self.failure_tracker = FailureFingerprintTracker(self.run_root)
        if self.progress_tracker is None:
            self.progress_tracker = ProgressTracker(self.run_root)

    def _log_event(self, event_type: str, data: Mapping[str, Any] | None = None) -> None:
        if self.event_logger:
            self.event_logger.log(event_type, data or {})

    def _check_failure_signature(self, result: Dict[str, Any]) -> Dict[str, str] | None:
        if not self.failure_tracker:
            return None
        fingerprint = self.failure_tracker.fingerprint(result)
        repeated, signature = self.failure_tracker.record(fingerprint)
        if repeated:
            self._log_event("STUCK_LOOP_DETECTED", {"signature": signature})
            return self.finalize("blocked", "Stuck loop detected: same failure repeated 3x")
        return None

    def _check_environment_error(self, result: Dict[str, Any]) -> Dict[str, str] | None:
        error = result.get("error") or {}
        message = (error.get("message") or "").lower()
        keywords = [
            "winerror 5",
            "access is denied",
            "permission denied",
            "basetemp",
            "cannot delete",
        ]
        for keyword in keywords:
            if keyword in message:
                msg = f"Environment error detected ({keyword}); manual cleanup may be required."
                self._log_event("ENVIRONMENT_BLOCKED", {"message": msg, "keyword": keyword})
                return self.finalize("blocked", msg)
        return None

    def _gates_hash(self, verification: Optional[Dict[str, Any]]) -> str:
        if not verification:
            return "none"
        gates = verification.get("gates") or []
        key_parts = [f"{verification.get('overall_pass')}"]
        key_parts.extend(f"{gate.get('name')}:{gate.get('ok')}" for gate in gates)
        return "|".join(key_parts)

    def _check_progress(self, repo_state: Dict[str, Any], verification: Dict[str, Any], step_id: str) -> Dict[str, str] | None:
        if not self.progress_tracker:
            return None
        gates_hash = self._gates_hash(verification)
        head_oid = repo_state.get("head_oid", "")
        changed_files = repo_state.get("changed_files") or []
        progress, blocked = self.progress_tracker.observe(
            head_oid=head_oid,
            changed_files=changed_files,
            gates_hash=gates_hash,
            step_id=step_id,
        )
        if blocked:
            self._log_event(
                "PROGRESS_BLOCKED",
                {
                    "step_id": step_id,
                    "head_oid": head_oid,
                    "changed_files": changed_files,
                    "gates_hash": gates_hash,
                },
            )
            return self.finalize("blocked", "No progress across 3 cycles; likely environmental issue or spec ambiguity.")
        return None


def load_and_validate_run_charter(repo_dir: Path, charter_path: Optional[Path]) -> RunCharter:
    if charter_path:
        path = charter_path
    else:
        path = repo_dir / ".agentmaestro" / "run_charter.json"
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    validate_run_charter(data)
    return RunCharter.model_validate(data)


def orchestrate(
    repo_dir: str,
    charter_path: Optional[str] = None,
    toolrunner_invoke: Optional[Callable[[Dict[str, Any], RunCharter], Dict[str, Any]]] = None,
    tool_invoker: Optional[ToolInvoker] = None,
    approval_handler: Optional[ApprovalHandler] = None,
) -> Dict[str, str]:
    invoker: ToolInvoker
    if tool_invoker:
        invoker = tool_invoker
    elif toolrunner_invoke:
        invoker = CallableToolInvoker(toolrunner_invoke)
    else:
        invoker = DefaultToolInvoker()
    orchestrator = Orchestrator(
        Path(repo_dir),
        Path(charter_path) if charter_path else None,
        tool_invoker=invoker,
        approval_handler=approval_handler,
    )
    return orchestrator.orchestrate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal Orchestrator Loop")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--charter", help="path to run_charter.json")
    args = parser.parse_args()
    result = orchestrate(args.repo_dir, args.charter)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
