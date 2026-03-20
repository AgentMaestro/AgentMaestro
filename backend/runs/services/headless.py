from __future__ import annotations

import logging

from agentmaestro.celery import app
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from comms.services.agent_chat_bridge import send_run_transport_message
from agents.utils import normalize_provider_for_model
from llm.models import LLMRun
from llm.services.runner import LLMRunner
from llm.system_context import build_system_context
from memory.models import MemoryRecord, ScheduledTask
from memory.scheduled_approvals import (
    HEADLESS_APPROVAL_REASON_DRIFT,
    build_scheduled_task_approval_context,
    create_headless_approval_request,
    record_inherited_headless_approval_use,
)
from memory.services import remember
from runs.models import AgentRun, AgentStep, RunEvent
from runs.services.event_builders import build_assistant_message_payload, build_chat_message_payload
from runs.services.events import append_event
from runs.services.memory import append_run_note, get_or_create_run_memory, update_run_memory
from runs.services.memory_bootstrap import bootstrap_memory_for_first_turn
from runs.services.state import FINAL_RUN_STATUSES, transition_run
from runs.services.steps import append_step
from tools.policy import get_effective_tools

logger = logging.getLogger(__name__)

HEADLESS_RUN_COMPLETED_SOURCE_KIND = "headless_run_completed"
HEADLESS_RUN_FAILED_SOURCE_KIND = "headless_run_failed"
HEADLESS_RUN_LOG_PREFIX = "[HEADLESS-RUN]"
DEFAULT_HEADLESS_MAX_TOOL_ROUNDS = 4
DEFAULT_HEADLESS_RUN_MAX_DURATION_SECONDS = 1800
HEADLESS_RUN_TIME_LIMIT_EXCEEDED = "headless_run_time_limit_exceeded"
MAX_RESULT_SUMMARY_CHARS = 2000
HEADLESS_APPROVAL_REQUESTED_EVENT = "scheduled_headless_approval_requested"
HEADLESS_APPROVAL_INHERITED_EVENT = "scheduled_headless_approval_inherited"
HEADLESS_APPROVAL_GRANTED_EVENT = "scheduled_headless_approval_granted"
HEADLESS_APPROVAL_DENIED_EVENT = "scheduled_headless_approval_denied"
HEADLESS_APPROVAL_DRIFT_EVENT = "scheduled_headless_approval_drifted"
HEADLESS_FAILURE_DIAGNOSTICS_EVENT = "headless_run_failure_diagnostics"
RETRYABLE_HEADLESS_FAILURE_CLASSIFICATIONS = {
    "network_error",
    "ratelimit",
    "connection_limit",
    "prev_not_found",
}


def _build_headless_constraints_prompt(*, max_tool_rounds: int, max_elapsed_seconds: int) -> str:
    max_elapsed_minutes = max(int(max_elapsed_seconds // 60), 1)
    return "\n".join(
        [
            "Headless run constraints:",
            f"- This run has a hard wall-clock limit of {max_elapsed_minutes} minutes ({max_elapsed_seconds} seconds).",
            f"- This run has a hard limit of {max_tool_rounds} tool rounds.",
            "- Do not do open-ended exploration or broad background research.",
            "- Prefer the shortest path to a useful result.",
            "- Gather only the information needed to complete the task.",
            "- If the task is too large, produce the best concrete partial result and clearly state what remains.",
            "- Before using tools, make a short bounded plan that fits within these limits.",
        ]
    )


def _build_headless_failure_details(exc: Exception) -> dict[str, object]:
    classification = str(getattr(exc, "classification", "") or "").strip()
    if not classification:
        classification = str(getattr(exc, "error_type", "") or "internal_error").strip()
    code = str(getattr(exc, "code", "") or "").strip()
    param = str(getattr(exc, "param", "") or "").strip()
    request_id = str(getattr(exc, "request_id", "") or "").strip()
    status = getattr(exc, "status", None)
    retryable = classification in RETRYABLE_HEADLESS_FAILURE_CLASSIFICATIONS
    summary_parts = [str(exc) or "Headless run failed.", f"classification={classification}"]
    if request_id:
        summary_parts.append(f"request_id={request_id}")
    if code:
        summary_parts.append(f"code={code}")
    if status not in (None, ""):
        summary_parts.append(f"status={status}")
    if param:
        summary_parts.append(f"param={param}")
    summary_parts.append(f"retryable={'yes' if retryable else 'no'}")
    return {
        "summary": "; ".join(summary_parts),
        "classification": classification,
        "code": code,
        "param": param,
        "status": status,
        "request_id": request_id,
        "retryable": retryable,
        "recommended_action": (
            "Retry the subrun once, then continue with partial results if it fails again."
            if retryable
            else "Review the error details before retrying; this does not look transient."
        ),
    }


def _build_headless_failure_details_from_provider_meta(
    provider_meta: dict[str, object] | None,
    *,
    fallback_error: str = "",
) -> dict[str, object]:
    meta = dict(provider_meta or {})
    classification = str(meta.get("classification") or meta.get("error_type") or "internal_error").strip()
    code = str(meta.get("code") or "").strip()
    param = str(meta.get("param") or "").strip()
    request_id = str(meta.get("request_id") or "").strip()
    status = meta.get("status", None)
    retryable = classification in RETRYABLE_HEADLESS_FAILURE_CLASSIFICATIONS
    summary_text = str(meta.get("error") or fallback_error or "Headless run failed.").strip()
    summary_parts = [summary_text, f"classification={classification}"]
    if request_id:
        summary_parts.append(f"request_id={request_id}")
    if code:
        summary_parts.append(f"code={code}")
    if status not in (None, ""):
        summary_parts.append(f"status={status}")
    if param:
        summary_parts.append(f"param={param}")
    summary_parts.append(f"retryable={'yes' if retryable else 'no'}")
    return {
        "summary": "; ".join(summary_parts),
        "classification": classification,
        "code": code,
        "param": param,
        "status": status,
        "request_id": request_id,
        "retryable": retryable,
        "recommended_action": (
            "Retry the subrun once, then continue with partial results if it fails again."
            if retryable
            else "Review the error details before retrying; this does not look transient."
        ),
    }


def get_headless_failure_details(run: AgentRun | str) -> dict[str, object] | None:
    run_id = str(run.id if isinstance(run, AgentRun) else run)
    event = (
        RunEvent.objects.filter(run_id=run_id, event_type=HEADLESS_FAILURE_DIAGNOSTICS_EVENT)
        .order_by("-seq")
        .first()
    )
    if not event or not isinstance(event.payload, dict):
        return None
    return dict(event.payload)


def build_scheduled_task_objective(scheduled_task: ScheduledTask) -> tuple[str, str]:
    payload = dict(scheduled_task.execution_payload or {})
    title = scheduled_task.title or scheduled_task.task_type.replace("_", " ")
    delivery_hint = "deliver it to the paired transport conversation when available"
    objective = f"Complete the scheduled task '{title}' and {delivery_hint}."
    extra_objective = str(payload.get("objective") or "").strip()
    extra_notes = str(payload.get("notes") or "").strip()
    prompt_lines = [
        f"Run the scheduled task '{title}'.",
        "Use the task payload and available tools to complete the work.",
    ]
    if extra_objective:
        prompt_lines.append(f"Primary objective: {extra_objective}")
    if extra_notes:
        prompt_lines.append(f"Notes: {extra_notes}")
    prompt_lines.append("Produce a final response suitable for direct delivery to the user.")
    prompt = (
        " ".join(prompt_lines)
    )
    return objective, prompt


def _execute_google_bridge_scheduled_task(run: AgentRun, scheduled_task: ScheduledTask) -> AgentRun | None:
    payload = dict(scheduled_task.execution_payload or {})
    if str(payload.get("integration_kind") or "").strip().lower() != "google":
        return None

    from google_bridge.services.bridge import execute_google_task

    try:
        result = execute_google_task(
            payload=payload,
            workspace=run.workspace,
            owner=run.started_by or run.agent.owner,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "%s google_bridge_failure run=%s scheduled_task=%s error=%s",
            HEADLESS_RUN_LOG_PREFIX,
            run.id,
            scheduled_task.id,
            exc,
        )
        append_event(
            run_id=str(run.id),
            event_type="google_bridge_task_failed",
            payload={
                "scheduled_task_id": str(scheduled_task.id),
                "integration_kind": "google",
                "error": str(exc),
            },
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
        return finalize_headless_run(
            run_id=str(run.id),
            success=False,
            error_text=str(exc),
            scheduled_task=scheduled_task,
        )

    summary_text = str(result.get("summary_text") or "").strip() or "Google bridge task completed."
    accounts = result.get("accounts") or result.get("account") or {}
    append_event(
        run_id=str(run.id),
        event_type="google_bridge_task_completed",
        payload={
            "scheduled_task_id": str(scheduled_task.id),
            "integration_kind": str(result.get("integration_kind") or "google"),
            "resource_kind": str(result.get("resource_kind") or ""),
            "action_kind": str(result.get("action_kind") or ""),
            "operation": str(result.get("operation") or ""),
            "summary_text": summary_text,
            "result": result.get("result") or {},
            "accounts": accounts,
        },
        broadcast_to_run=False,
        correlation_id=run.correlation_id,
    )
    append_step(
        run_id=str(run.id),
        kind=AgentStep.Kind.ACTION,
        payload={
            "integration_kind": "google",
            "resource_kind": str(result.get("resource_kind") or ""),
            "action_kind": str(result.get("action_kind") or ""),
            "operation": str(result.get("operation") or ""),
            "summary_text": summary_text,
            "result": result.get("result") or {},
            "accounts": accounts,
        },
        correlation_id=str(run.correlation_id),
    )
    return finalize_headless_run(
        run_id=str(run.id),
        success=True,
        final_text=summary_text,
        scheduled_task=scheduled_task,
    )


def create_headless_run(
    *,
    agent,
    workspace,
    objective: str,
    initial_user_message: str,
    trigger_kind: str,
    trigger_ref: str = "",
    started_by=None,
    delivery_target: str = "",
) -> AgentRun:
    started_at = timezone.now()
    with transaction.atomic():
        run = AgentRun.objects.create(
            workspace=workspace,
            agent=agent,
            started_by=started_by,
            status=AgentRun.Status.PENDING,
            channel=AgentRun.Channel.API,
            execution_mode=AgentRun.ExecutionMode.HEADLESS,
            trigger_kind=trigger_kind,
            trigger_ref=str(trigger_ref or "").strip(),
            delivery_target=str(delivery_target or "").strip(),
            input_text=str(initial_user_message or "").strip(),
            started_at=started_at,
        )
        get_or_create_run_memory(run)
        update_run_memory(run, objective=objective)
        append_event(
            run_id=str(run.id),
            event_type="headless_run_created",
            payload={
                "execution_mode": run.execution_mode,
                "trigger_kind": run.trigger_kind,
                "trigger_ref": run.trigger_ref,
                "objective": objective,
                "delivery_target": run.delivery_target,
            },
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
        append_event(
            run_id=str(run.id),
            event_type="chat_message",
            payload=build_chat_message_payload("user", initial_user_message),
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
    return run


@transaction.atomic
def launch_scheduled_task_run(scheduled_task_id: str) -> tuple[ScheduledTask, AgentRun, bool]:
    scheduled_task = (
        ScheduledTask.objects.select_for_update()
        .select_related("agent", "workspace", "owner")
        .get(id=scheduled_task_id)
    )
    active_run = scheduled_task.active_run
    if active_run and active_run.status not in FINAL_RUN_STATUSES:
        logger.info(
            "Skipping duplicate scheduled headless launch task=%s active_run=%s status=%s",
            scheduled_task.id,
            active_run.id,
            active_run.status,
        )
        return scheduled_task, active_run, False
    if active_run and active_run.status in FINAL_RUN_STATUSES:
        scheduled_task.active_run = None

    objective, prompt = build_scheduled_task_objective(scheduled_task)
    run = create_headless_run(
        agent=scheduled_task.agent,
        workspace=scheduled_task.workspace,
        objective=objective,
        initial_user_message=prompt,
        trigger_kind=AgentRun.TriggerKind.SCHEDULED_TASK,
        trigger_ref=str(scheduled_task.id),
        started_by=scheduled_task.owner,
        delivery_target=scheduled_task.delivery_target,
    )
    scheduled_task.active_run = run
    scheduled_task.last_run = run
    scheduled_task.save(update_fields=["active_run", "last_run", "updated_at"])

    approval_context = build_scheduled_task_approval_context(scheduled_task)
    run.approval_fingerprint = approval_context.fingerprint
    if approval_context.approval is not None:
        record_inherited_headless_approval_use(approval=approval_context.approval, run=run)
        run.approval_mode = AgentRun.ApprovalMode.INHERITED
        run.approval_source_ref = str(approval_context.approval.id)
        run.save(update_fields=["approval_mode", "approval_fingerprint", "approval_source_ref", "updated_at"])
        append_event(
            run_id=str(run.id),
            event_type=HEADLESS_APPROVAL_INHERITED_EVENT,
            payload={
                "scheduled_task_id": str(scheduled_task.id),
                "approval_id": str(approval_context.approval.id),
                "approval_fingerprint": approval_context.fingerprint,
                "approval_expires_at": approval_context.approval.expires_at.isoformat() if approval_context.approval.expires_at else "",
                "fingerprint_version": approval_context.fingerprint_version,
            },
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
        append_run_note(run, f"Scheduled task {scheduled_task.id} inherited prior approval {approval_context.approval.id}.")
        return scheduled_task, run, True

    if run.status == AgentRun.Status.PENDING:
        transition_run(run_id=str(run.id), new_status=AgentRun.Status.RUNNING)
    approval_gate = create_headless_approval_request(run=run, scheduled_task=scheduled_task, context=approval_context)
    run.approval_mode = AgentRun.ApprovalMode.REQUESTED
    run.approval_source_ref = str(approval_gate.id)
    run.save(update_fields=["approval_mode", "approval_fingerprint", "approval_source_ref", "updated_at"])
    append_event(
        run_id=str(run.id),
        event_type=HEADLESS_APPROVAL_REQUESTED_EVENT,
        payload={
            "scheduled_task_id": str(scheduled_task.id),
            "approval_tool_call_id": str(approval_gate.id),
            "approval_reason": approval_context.reason,
            "approval_fingerprint": approval_context.fingerprint,
            "previous_approval_id": str(approval_context.previous_approval.id) if approval_context.previous_approval else "",
            "previous_fingerprint": approval_context.previous_approval.fingerprint if approval_context.previous_approval else "",
            "fingerprint_version": approval_context.fingerprint_version,
        },
        broadcast_to_run=False,
        correlation_id=run.correlation_id,
    )
    append_run_note(run, f"Scheduled task {scheduled_task.id} is waiting for approval before headless execution.")
    return scheduled_task, run, True


def continue_headless_run_after_approval_gate(tool_call) -> dict[str, object]:
    from memory.scheduled_approvals import activate_headless_approval_from_tool_call
    from runs.tasks import execute_headless_run_task

    run = tool_call.run
    scheduled_task = ScheduledTask.objects.select_related("agent", "workspace", "owner").get(id=tool_call.args.get("scheduled_task_id"))
    approval, approval_context, drift_reason = activate_headless_approval_from_tool_call(tool_call)
    if drift_reason == HEADLESS_APPROVAL_REASON_DRIFT:
        append_event(
            run_id=str(run.id),
            event_type=HEADLESS_APPROVAL_DRIFT_EVENT,
            payload={
                "scheduled_task_id": str(scheduled_task.id),
                "approval_tool_call_id": str(tool_call.id),
                "requested_fingerprint": str(tool_call.args.get("approval_fingerprint") or ""),
                "current_fingerprint": approval_context.fingerprint,
                "fingerprint_version": approval_context.fingerprint_version,
            },
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
        finalize_headless_run(
            run_id=str(run.id),
            success=False,
            error_text=HEADLESS_APPROVAL_REASON_DRIFT,
            scheduled_task=scheduled_task,
        )
        return {
            "run_id": str(run.id),
            "scheduled_task_id": str(scheduled_task.id),
            "continued": False,
            "error": HEADLESS_APPROVAL_REASON_DRIFT,
        }

    run.approval_mode = AgentRun.ApprovalMode.MANUAL
    run.approval_fingerprint = approval_context.fingerprint
    run.approval_source_ref = str(approval.id)
    run.save(update_fields=["approval_mode", "approval_fingerprint", "approval_source_ref", "updated_at"])
    append_event(
        run_id=str(run.id),
        event_type=HEADLESS_APPROVAL_GRANTED_EVENT,
        payload={
            "scheduled_task_id": str(scheduled_task.id),
            "approval_tool_call_id": str(tool_call.id),
            "approval_id": str(approval.id),
            "approval_fingerprint": approval_context.fingerprint,
            "fingerprint_version": approval_context.fingerprint_version,
            "approval_expires_at": approval.expires_at.isoformat() if approval.expires_at else "",
            "approved_by": getattr(tool_call.approved_by, "username", None),
        },
        broadcast_to_run=False,
        correlation_id=run.correlation_id,
    )
    append_run_note(run, f"Scheduled task {scheduled_task.id} was manually approved for headless execution.")
    execute_headless_run_task.delay(str(run.id))
    return {
        "run_id": str(run.id),
        "scheduled_task_id": str(scheduled_task.id),
        "approval_id": str(approval.id),
        "continued": True,
    }


def handle_headless_approval_denial(tool_call, *, reason: str) -> AgentRun:
    run = tool_call.run
    scheduled_task = None
    if run.trigger_kind == AgentRun.TriggerKind.SCHEDULED_TASK and run.trigger_ref:
        scheduled_task = ScheduledTask.objects.select_related("agent", "workspace", "owner").filter(id=run.trigger_ref).first()
    append_event(
        run_id=str(run.id),
        event_type=HEADLESS_APPROVAL_DENIED_EVENT,
        payload={
            "scheduled_task_id": str(scheduled_task.id) if scheduled_task else str(tool_call.args.get("scheduled_task_id") or ""),
            "approval_tool_call_id": str(tool_call.id),
            "reason": reason,
            "approval_fingerprint": str(tool_call.args.get("approval_fingerprint") or ""),
        },
        broadcast_to_run=False,
        correlation_id=run.correlation_id,
    )
    return finalize_headless_run(
        run_id=str(run.id),
        success=False,
        error_text=reason or "headless_approval_denied",
        scheduled_task=scheduled_task,
    )


def execute_headless_run(run_id: str) -> AgentRun:
    run = AgentRun.objects.select_related("agent", "workspace", "started_by").get(id=run_id)
    scheduled_task = None
    if run.trigger_kind == AgentRun.TriggerKind.SCHEDULED_TASK and run.trigger_ref:
        scheduled_task = ScheduledTask.objects.select_related("agent", "workspace", "owner").filter(id=run.trigger_ref).first()

    if run.status == AgentRun.Status.WAITING_FOR_APPROVAL:
        append_run_note(run, "Headless execution skipped because the run is still waiting for approval.")
        return run

    if run.status == AgentRun.Status.PENDING:
        transition_run(run_id=str(run.id), new_status=AgentRun.Status.RUNNING)

    append_event(
        run_id=str(run.id),
        event_type="headless_run_started",
        payload={
            "trigger_kind": run.trigger_kind,
            "trigger_ref": run.trigger_ref,
            "delivery_target": run.delivery_target,
            "approval_mode": run.approval_mode,
            "approval_fingerprint": run.approval_fingerprint,
            "approval_source_ref": run.approval_source_ref,
        },
        broadcast_to_run=False,
        correlation_id=run.correlation_id,
    )
    google_bridge_run = _execute_google_bridge_scheduled_task(run, scheduled_task) if scheduled_task is not None else None
    if google_bridge_run is not None:
        return google_bridge_run
    append_step(
        run_id=str(run.id),
        kind=AgentStep.Kind.MODEL_CALL,
        payload={
            "mode": "headless",
            "trigger_kind": run.trigger_kind,
            "trigger_ref": run.trigger_ref,
            "approval_mode": run.approval_mode,
        },
        correlation_id=str(run.correlation_id),
    )

    effective_tools = get_effective_tools(run.agent, run.started_by or run.agent.owner)
    tool_payloads = [
        {
            "name": entry.tool.name,
            "description": entry.description,
            "parameters": entry.args_schema or {},
        }
        for entry in effective_tools
    ]
    provider, model_name = _resolve_run_model(run.agent)
    system_context = build_system_context(
        run.agent,
        model_name=model_name,
        transport="ws",
        tool_names=[entry.tool.name for entry in effective_tools],
        authenticated_user=run.started_by or run.agent.owner,
    )
    max_tool_rounds = int(getattr(settings, "HEADLESS_RUN_MAX_TOOL_ROUNDS", DEFAULT_HEADLESS_MAX_TOOL_ROUNDS))
    max_elapsed_seconds = int(
        getattr(settings, "HEADLESS_RUN_MAX_DURATION_SECONDS", DEFAULT_HEADLESS_RUN_MAX_DURATION_SECONDS)
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_context}]
    messages.append(
        {
            "role": "system",
            "content": _build_headless_constraints_prompt(
                max_tool_rounds=max_tool_rounds,
                max_elapsed_seconds=max_elapsed_seconds,
            ),
        }
    )
    bootstrap = bootstrap_memory_for_first_turn(run, run.agent, run.input_text)
    if bootstrap and bootstrap.summary_text:
        messages.append({"role": "system", "content": bootstrap.summary_text})
    messages.append({"role": "user", "content": run.input_text})

    run_memory = get_or_create_run_memory(run)
    runner = LLMRunner()
    logger.info(
        "%s execute_start run=%s parent_run=%s trigger_kind=%s trigger_ref=%s model=%s tools=%d input_chars=%d bootstrap_applied=%s max_tool_rounds=%d max_elapsed_seconds=%d",
        HEADLESS_RUN_LOG_PREFIX,
        run.id,
        run.parent_run_id or "",
        run.trigger_kind,
        run.trigger_ref,
        model_name,
        len(tool_payloads),
        len(run.input_text or ""),
        bool(bootstrap and bootstrap.summary_text),
        max_tool_rounds,
        max_elapsed_seconds,
    )
    try:
        result = async_to_sync(runner.run)(
            prompt="",
            agent_name=run.agent.name or run.agent.slug,
            provider=provider,
            model_name=model_name,
            temperature=float(run.agent.temperature) if run.agent.temperature is not None else None,
            tools=tool_payloads,
            backup_models=run.agent.get_backup_models(),
            backup_retry_policy=run.agent.get_backup_retry_policy(),
            orchestration_run_id=str(run.id),
            purpose=run_memory.objective or run.input_text[:200],
            max_tool_rounds=max_tool_rounds,
            max_elapsed_seconds=max_elapsed_seconds,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s execute_exception run=%s parent_run=%s", HEADLESS_RUN_LOG_PREFIX, run.id, run.parent_run_id or "")
        details = _build_headless_failure_details(exc)
        append_event(
            run_id=str(run.id),
            event_type=HEADLESS_FAILURE_DIAGNOSTICS_EVENT,
            payload=details,
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
        return finalize_headless_run(
            run_id=str(run.id),
            success=False,
            error_text=str(details["summary"]),
            scheduled_task=scheduled_task,
        )

    final_text = str(result.get("text") or "").strip()
    llm_run_id = str(result.get("run_id") or "").strip()
    response_id = _fetch_response_id(llm_run_id)
    run_status = str(result.get("status") or "").strip().lower()
    logger.info(
        "%s execute_result run=%s parent_run=%s llm_run_id=%s status=%s response_id=%s error=%s",
        HEADLESS_RUN_LOG_PREFIX,
        run.id,
        run.parent_run_id or "",
        llm_run_id,
        run_status,
        response_id,
        str(result.get("error") or "").strip(),
    )
    if final_text:
        payload = build_assistant_message_payload(final_text, model=model_name, provider_response_id=response_id)
        append_event(
            run_id=str(run.id),
            event_type="assistant_message",
            payload=payload,
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
    if run_status != "completed":
        llm_run = LLMRun.objects.filter(id=llm_run_id).only("provider_meta", "error").first() if llm_run_id else None
        failure_details = _build_headless_failure_details_from_provider_meta(
            dict(getattr(llm_run, "provider_meta", {}) or {}),
            fallback_error=str(result.get("error") or getattr(llm_run, "error", "") or "").strip(),
        )
        append_event(
            run_id=str(run.id),
            event_type=HEADLESS_FAILURE_DIAGNOSTICS_EVENT,
            payload=failure_details,
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
        logger.warning(
            "%s diagnostics_emitted run=%s parent_run=%s classification=%s status=%s request_id=%s summary=%s",
            HEADLESS_RUN_LOG_PREFIX,
            run.id,
            run.parent_run_id or "",
            failure_details.get("classification"),
            failure_details.get("status"),
            failure_details.get("request_id"),
            failure_details.get("summary"),
        )
    return finalize_headless_run(
        run_id=str(run.id),
        success=run_status == "completed",
        final_text=final_text,
        error_text=str(result.get("error") or "").strip(),
        scheduled_task=scheduled_task,
        provider_response_id=response_id,
    )


def finalize_headless_run(
    *,
    run_id: str,
    success: bool,
    final_text: str = "",
    error_text: str = "",
    scheduled_task: ScheduledTask | None = None,
    provider_response_id: str = "",
) -> AgentRun:
    with transaction.atomic():
        run = AgentRun.objects.select_for_update().select_related("agent", "workspace").get(id=run_id)
        scheduled_task = scheduled_task or (
            ScheduledTask.objects.select_for_update().filter(id=run.trigger_ref).first()
            if run.trigger_kind == AgentRun.TriggerKind.SCHEDULED_TASK and run.trigger_ref
            else None
        )
        summary_text = _trim_text(final_text or error_text, MAX_RESULT_SUMMARY_CHARS)
        run.final_text = final_text or ""
        run.error_summary = error_text or ""
        run.ended_at = timezone.now()
        if provider_response_id:
            run.previous_response_id = provider_response_id
        update_fields = ["final_text", "error_summary", "ended_at", "updated_at"]
        if provider_response_id:
            update_fields.append("previous_response_id")
        run.save(update_fields=update_fields)

        event_type = "headless_run_completed" if success else "headless_run_failed"
        append_event(
            run_id=str(run.id),
            event_type=event_type,
            payload={
                "trigger_kind": run.trigger_kind,
                "trigger_ref": run.trigger_ref,
                "delivery_target": run.delivery_target,
                "summary": summary_text,
                "approval_mode": run.approval_mode,
                "approval_fingerprint": run.approval_fingerprint,
                "approval_source_ref": run.approval_source_ref,
            },
            broadcast_to_run=False,
            correlation_id=run.correlation_id,
        )
        append_step(
            run_id=str(run.id),
            kind=AgentStep.Kind.FINAL,
            payload={
                "status": AgentRun.Status.COMPLETED if success else AgentRun.Status.FAILED,
                "summary": summary_text,
            },
            correlation_id=str(run.correlation_id),
        )
        transition_run(
            run_id=str(run.id),
            new_status=AgentRun.Status.COMPLETED if success else AgentRun.Status.FAILED,
        )
        append_run_note(run, f"Headless run finished with status={'completed' if success else 'failed' }.")
        logger.log(
            logging.INFO if success else logging.WARNING,
            "%s finalize run=%s parent_run=%s status=%s provider_response_id=%s error_summary=%s final_text_chars=%d",
            HEADLESS_RUN_LOG_PREFIX,
            run.id,
            run.parent_run_id or "",
            AgentRun.Status.COMPLETED if success else AgentRun.Status.FAILED,
            provider_response_id,
            _trim_text(error_text, 300),
            len(final_text or ""),
        )

        if scheduled_task is not None:
            scheduled_task.last_run = run
            scheduled_task.active_run = None
            if success:
                scheduled_task.last_success_at = timezone.now()
                scheduled_task.last_result_summary = summary_text
                scheduled_task.last_error = ""
                scheduled_task.failure_count = 0
            else:
                scheduled_task.failure_count = int(scheduled_task.failure_count or 0) + 1
                scheduled_task.last_error = _trim_text(error_text or "Headless run failed.", MAX_RESULT_SUMMARY_CHARS)
            scheduled_task.save(
                update_fields=[
                    "last_run",
                    "active_run",
                    "last_success_at",
                    "last_result_summary",
                    "last_error",
                    "failure_count",
                    "updated_at",
                ]
            )

    _remember_headless_run_outcome(run, scheduled_task=scheduled_task, success=success, summary_text=summary_text)
    if success and scheduled_task is not None:
        delivered = send_run_transport_message(
            run_id=str(run.id),
            text=final_text,
            author_label=(run.agent.name or run.agent.slug or "assistant").strip().lower(),
            mirror_to_control=False,
        )
        if delivered:
            append_run_note(run, "Delivered headless run result to paired transport conversation.")
        else:
            logger.info(
                "Headless run completed without paired transport delivery run=%s scheduled_task=%s",
                run.id,
                scheduled_task.id,
            )
            append_run_note(run, "No paired transport conversation was available for delivery.")
    return AgentRun.objects.select_related("agent", "workspace", "started_by").get(id=run_id)


def _remember_headless_run_outcome(
    run: AgentRun,
    *,
    scheduled_task: ScheduledTask | None,
    success: bool,
    summary_text: str,
) -> None:
    tags = ["headless-run", "success" if success else "failure"]
    content = (
        f"Headless run {run.id} for trigger {run.trigger_kind}:{run.trigger_ref or 'n/a'} "
        f"finished with status {'completed' if success else 'failed'} at {timezone.now().isoformat()}. "
        f"Summary: {summary_text}"
    )
    if scheduled_task is not None:
        tags.extend(["scheduled-task", scheduled_task.task_type.replace("_", "-")])
    remember(
        agent=run.agent,
        memory_kind=MemoryRecord.MemoryKind.EPISODIC,
        content=content,
        tags=tags,
        summary=(
            f"Headless run completed for {scheduled_task.title or scheduled_task.task_type}"
            if success and scheduled_task is not None
            else f"Headless run failed for {scheduled_task.title or scheduled_task.task_type}"
            if scheduled_task is not None
            else f"Headless run {'completed' if success else 'failed'}"
        ),
        importance=0.45 if success else 0.60,
        source_kind=HEADLESS_RUN_COMPLETED_SOURCE_KIND if success else HEADLESS_RUN_FAILED_SOURCE_KIND,
        source_ref=str(run.id),
        dedupe_mode="none",
    )


def _resolve_run_model(agent) -> tuple[str, str]:
    model_name = str(getattr(agent, "default_model", "") or "").strip()
    if not model_name:
        raise RuntimeError("Headless run requires agent.default_model")
    provider = normalize_provider_for_model(getattr(settings, "LLM_PROVIDER", "openai"), model_name)
    return provider, model_name


def _fetch_response_id(llm_run_id: str) -> str:
    if not llm_run_id:
        return ""
    llm_run = LLMRun.objects.filter(id=llm_run_id).first()
    if llm_run is None:
        return ""
    meta = dict(llm_run.provider_meta or {})
    response_id = str(
        meta.get("provider_response_id") or meta.get("openai_response_id") or ""
    ).strip()
    return response_id


def _trim_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    suffix = "..."
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: max(limit - len(suffix), 0)].rstrip() + suffix


def mark_headless_run_stale(
    run_id: str,
    *,
    scheduled_task: ScheduledTask | None = None,
    reason: str = "stale_headless_run_timeout",
) -> AgentRun:
    run = AgentRun.objects.filter(id=run_id).first()
    if run is None:
        raise AgentRun.DoesNotExist(f"Headless run {run_id} does not exist.")
    if run.status in FINAL_RUN_STATUSES:
        return run
    if run.current_task_id:
        try:
            app.control.revoke(run.current_task_id, terminate=True, signal="SIGTERM")
        except Exception as e:  # pragma: no cover
            logger.warning("Failed to revoke stale headless run task run=%s task_id=%s error=%s", run.id, run.current_task_id, e, exc_info=True)
    return finalize_headless_run(
        run_id=str(run.id),
        success=False,
        error_text=reason,
        scheduled_task=scheduled_task,
    )
