from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import timedelta
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from logging_utils import scrub_sensitive_text, scrub_sensitive_value

from agents.models import Agent
from comms.models import PendingPairing, Transport, TransportEndpoint
from comms.services.agent_chat_bridge import send_run_transport_message
from comms.services import outbound as comms_outbound
from core.models import Workspace
from llm.models import LLMRun
from llm.services.runner import LLMRunner
from llm.services.registry import _CLIENTS
from llm.services.tool_schemas import get_tool_schemas
from llm.system_context import build_system_context
from runs.models import AgentRun


DEFAULT_CONFIG_PATH = Path(settings.BASE_DIR).parent / "smoke" / "llm_provider_matrix.json"


@dataclass(slots=True)
class MatrixTarget:
    provider: str
    model: str
    transport: str
    label: str
    weight: float = 1.0


@dataclass(slots=True)
class MatrixScenario:
    name: str
    kind: str
    prompt: str = ""
    expected_text: str = ""
    expected_contains: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    source_scenario: str = ""
    target_ms: int = 5000
    weight: float = 1.0
    max_tool_rounds: int = 4


@dataclass(slots=True)
class ScenarioOutcome:
    name: str
    kind: str
    status: str
    elapsed_ms: float
    speed_score: float
    quality_score: float
    overall_score: float
    text: str
    run_id: str = ""
    llm_run_id: str = ""
    tool_calls_executed: int = 0
    tool_names: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    telegram_rendered: str = ""
    telegram_delivered: bool = False


@dataclass(slots=True)
class TargetOutcome:
    label: str
    provider: str
    model: str
    transport: str
    speed_score: float
    quality_score: float
    overall_score: float
    grade: str
    scenario_results: list[ScenarioOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _normalize_name(value: str) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _normalize_text_for_compare(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


def _grade_for_score(value: float) -> str:
    if value >= 90:
        return "A"
    if value >= 80:
        return "B"
    if value >= 70:
        return "C"
    if value >= 60:
        return "D"
    return "F"


def _speed_score(elapsed_ms: float, target_ms: int) -> float:
    if elapsed_ms <= 0:
        return 100.0
    target = max(int(target_ms or 1), 1)
    return _clamp_score((target / elapsed_ms) * 100.0)


def _score_direct_reply(outcome: ScenarioOutcome, expected_text: str) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.0
    if outcome.status == "completed":
        score += 50.0
    else:
        notes.append(f"status={outcome.status}")
    normalized = _normalize_text_for_compare(outcome.text)
    expected = _normalize_text_for_compare(expected_text)
    if normalized == expected:
        score += 50.0
    else:
        notes.append("final text did not match the expected normalized reply")
    return _clamp_score(score), notes


def _score_tool_lookup(outcome: ScenarioOutcome, required_tools: Sequence[str], expected_contains: Sequence[str]) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.0
    if outcome.status == "completed":
        score += 35.0
    else:
        notes.append(f"status={outcome.status}")

    tool_set = {tool.strip() for tool in outcome.tool_names if tool.strip()}
    required = {tool.strip() for tool in required_tools if tool.strip()}
    if required.issubset(tool_set):
        score += 35.0
    else:
        missing = sorted(required - tool_set)
        notes.append(f"missing tools: {', '.join(missing) if missing else 'none'}")

    text = outcome.text.lower()
    matched = [needle for needle in expected_contains if needle.lower() in text]
    if len(matched) == len([needle for needle in expected_contains if needle.strip()]):
        score += 30.0
    else:
        missing = [needle for needle in expected_contains if needle.lower() not in text]
        notes.append(f"final text missing: {', '.join(missing) if missing else 'none'}")
    return _clamp_score(score), notes


def _score_telegram_delivery(outcome: ScenarioOutcome, source_text: str, agent_name: str) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.0
    if outcome.telegram_delivered:
        score += 45.0
    else:
        notes.append("telegram delivery did not complete")
    rendered = outcome.telegram_rendered.strip()
    expected_prefix = f"<i>{agent_name.strip().lower()}</i>"
    if rendered.startswith(expected_prefix):
        score += 30.0
    else:
        notes.append("rendered telegram author label did not match")
    if source_text and source_text.strip() and source_text.strip() in rendered:
        score += 25.0
    else:
        notes.append("rendered telegram text did not include the source reply")
    return _clamp_score(score), notes


def _parse_targets(raw: object) -> list[MatrixTarget]:
    if not isinstance(raw, list) or not raw:
        raise CommandError("Matrix config must contain at least one target")
    targets: list[MatrixTarget] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise CommandError("Each matrix target must be an object")
        provider = str(entry.get("provider") or "").strip().lower()
        model = str(entry.get("model") or "").strip()
        transport = str(entry.get("transport") or "").strip().lower()
        label = str(entry.get("label") or "").strip() or f"{provider}:{model}:{transport}"
        if not provider or not model or not transport:
            raise CommandError("Each matrix target must define provider, model, and transport")
        weight = float(entry.get("weight") or 1.0)
        targets.append(MatrixTarget(provider=provider, model=model, transport=transport, label=label, weight=weight))
    return targets


def _parse_scenarios(raw: object) -> list[MatrixScenario]:
    if not isinstance(raw, list) or not raw:
        raise CommandError("Matrix config must contain at least one scenario")
    scenarios: list[MatrixScenario] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise CommandError("Each matrix scenario must be an object")
        name = str(entry.get("name") or "").strip()
        kind = str(entry.get("kind") or "").strip().lower()
        prompt = str(entry.get("prompt") or "").strip()
        expected_text = str(entry.get("expected_text") or "").strip()
        expected_contains = [str(item).strip() for item in entry.get("expected_contains") or [] if str(item).strip()]
        required_tools = [str(item).strip() for item in entry.get("required_tools") or [] if str(item).strip()]
        source_scenario = str(entry.get("source_scenario") or "").strip()
        target_ms = int(entry.get("target_ms") or 5000)
        weight = float(entry.get("weight") or 1.0)
        max_tool_rounds = int(entry.get("max_tool_rounds") or 4)
        if not name or not kind:
            raise CommandError("Each matrix scenario must define name and kind")
        scenarios.append(
            MatrixScenario(
                name=name,
                kind=kind,
                prompt=prompt,
                expected_text=expected_text,
                expected_contains=expected_contains,
                required_tools=required_tools,
                source_scenario=source_scenario,
                target_ms=target_ms,
                weight=weight,
                max_tool_rounds=max_tool_rounds,
            )
        )
    return scenarios


def _load_matrix_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CommandError(f"Matrix config not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - user-config error
        raise CommandError(f"Matrix config is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CommandError("Matrix config must be a JSON object")
    raw["targets"] = _parse_targets(raw.get("targets"))
    raw["scenarios"] = _parse_scenarios(raw.get("scenarios"))
    rating = raw.get("rating") or {}
    if not isinstance(rating, dict):
        raise CommandError("Matrix config rating block must be an object")
    raw["rating"] = {
        "speed_weight": float(rating.get("speed_weight") or 0.4),
        "quality_weight": float(rating.get("quality_weight") or 0.6),
    }
    return raw


@contextmanager
def _temporary_provider_transport(provider: str, transport: str):
    provider_key = str(provider or "").strip().lower()
    env_name = "OPENAI_TRANSPORT" if provider_key == "openai" else "GEMINI_TRANSPORT" if provider_key == "gemini" else ""
    previous = os.environ.get(env_name) if env_name else None
    if env_name:
        os.environ[env_name] = str(transport or "").strip().lower() or "http"
    _CLIENTS.pop(provider_key, None)
    try:
        yield
    finally:
        if env_name:
            if previous is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous
        _CLIENTS.pop(provider_key, None)


def _ensure_seed_objects(label: str, model: str) -> tuple[Workspace, Agent, AgentRun]:
    suffix = _normalize_name(label)[:32] or uuid.uuid4().hex[:8]
    User = get_user_model()
    owner = User.objects.create_user(username=f"matrix_{suffix}_{uuid.uuid4().hex[:6]}")
    workspace = Workspace.objects.create(name=f"llm-matrix-{suffix}-{uuid.uuid4().hex[:6]}")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name=f"Matrix {label}",
        default_model=model,
        soul="Keep replies concise and grounded.",
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=owner,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.API,
        execution_mode=AgentRun.ExecutionMode.INTERACTIVE,
        trigger_kind=AgentRun.TriggerKind.SYSTEM,
        input_text="llm provider matrix smoke",
        started_at=timezone.now(),
    )
    return workspace, agent, run


def _ensure_telegram_pairing(agent: Agent, label: str) -> None:
    transport, _ = Transport.objects.get_or_create(
        key="telegram",
        defaults={"display_name": "Telegram", "mode": "polling"},
    )
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"bot_username": f"matrix_{_normalize_name(label)}"},
    )
    PendingPairing.objects.create(
        agent=agent,
        endpoint=endpoint,
        status=PendingPairing.STATUS_CLAIMED,
        claimed_chat_id=f"matrix-{agent.slug}",
        claimed_at=timezone.now(),
        expires_at=timezone.now() + timedelta(minutes=30),
    )


def _build_messages(agent: Agent, model: str, transport: str, tools: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    system_context = build_system_context(
        agent,
        model_name=model,
        transport=transport,
        tool_names=[str(tool.get("name") or "") for tool in tools if str(tool.get("name") or "").strip()],
        authenticated_user=agent.owner,
    )
    return [{"role": "system", "content": system_context}]


def _run_llm_scenario(
    *,
    runner: LLMRunner,
    target: MatrixTarget,
    agent: Agent,
    orchestration_run_id: str,
    scenario: MatrixScenario,
) -> tuple[ScenarioOutcome, LLMRun | None]:
    tool_schemas: list[dict[str, Any]] = get_tool_schemas() if scenario.kind == "tool" else []
    messages = _build_messages(agent, target.model, target.transport, tool_schemas)
    tools = tool_schemas or None
    start = time.perf_counter()
    try:
        result = async_to_sync(runner.run)(
            prompt=scenario.prompt,
            provider=target.provider,
            model_name=target.model,
            messages=messages,
            tools=tools,
            orchestration_run_id=orchestration_run_id,
            purpose=scenario.name,
            max_tool_rounds=scenario.max_tool_rounds,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        llm_run = LLMRun.objects.get(id=result["run_id"])
        tool_names = [str(name) for name in llm_run.tool_calls.values_list("tool_name", flat=True)]
        outcome = ScenarioOutcome(
            name=scenario.name,
            kind=scenario.kind,
            status=str(result.get("status") or ""),
            elapsed_ms=elapsed_ms,
            speed_score=_speed_score(elapsed_ms, scenario.target_ms),
            quality_score=0.0,
            overall_score=0.0,
            text=str(result.get("text") or "").strip(),
            llm_run_id=str(llm_run.id),
            tool_calls_executed=int(result.get("tool_calls_executed") or 0),
            tool_names=tool_names,
        )
        return outcome, llm_run
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        notes = [f"runner exception: {exc}"]
        outcome = ScenarioOutcome(
            name=scenario.name,
            kind=scenario.kind,
            status="failed",
            elapsed_ms=elapsed_ms,
            speed_score=_speed_score(elapsed_ms, scenario.target_ms),
            quality_score=0.0,
            overall_score=0.0,
            text=str(exc),
            notes=notes,
        )
        return outcome, None


def _run_telegram_scenario(
    *,
    agent: Agent,
    agent_run: AgentRun,
    label: str,
    source_text: str,
    source_scenario: MatrixScenario,
    target_ms: int,
) -> ScenarioOutcome:
    captured: dict[str, Any] = {}

    def _fake_send_transport_message(endpoint, chat_id, text, **kwargs):  # noqa: ANN001
        captured.update(
            {
                "endpoint_key": getattr(getattr(endpoint, "transport", None), "key", ""),
                "chat_id": chat_id,
                "text": text,
                "kwargs": kwargs,
            }
        )
        return {"ok": True, "result": {"message_id": "matrix-telegram"}}

    start = time.perf_counter()
    with patch.object(comms_outbound, "send_transport_message", side_effect=_fake_send_transport_message):
        delivered = send_run_transport_message(
            run_id=str(agent_run.id),
            text=source_text,
            author_label=(agent.name or label or "assistant").strip().lower(),
            mirror_to_control=False,
            parse_mode="HTML",
        )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    rendered = str(captured.get("text") or "")
    outcome = ScenarioOutcome(
        name=source_scenario.name + "_telegram",
        kind="telegram",
        status="completed" if delivered else "failed",
        elapsed_ms=elapsed_ms,
        speed_score=_speed_score(elapsed_ms, target_ms),
        quality_score=0.0,
        overall_score=0.0,
        text=rendered,
        run_id=str(agent_run.id),
        telegram_rendered=rendered,
        telegram_delivered=bool(delivered),
    )
    return outcome


def _evaluate_outcomes(
    target: MatrixTarget,
    scenarios: Sequence[MatrixScenario],
    outcomes: list[ScenarioOutcome],
    agent_name: str,
    rating: dict[str, float],
) -> TargetOutcome:
    scenario_map = {scenario.name: scenario for scenario in scenarios}
    for outcome in outcomes:
        scenario = scenario_map.get(outcome.name.replace("_telegram", "")) or scenario_map.get(outcome.name)
        if scenario is None:
            outcome.notes.append("scenario config not found")
            continue
        if scenario.kind == "chat":
            outcome.quality_score, notes = _score_direct_reply(outcome, scenario.expected_text)
        elif scenario.kind == "tool":
            outcome.quality_score, notes = _score_tool_lookup(outcome, scenario.required_tools, scenario.expected_contains)
        elif scenario.kind == "telegram":
            source_text = next((item.text for item in outcomes if item.name == scenario.source_scenario), "")
            outcome.quality_score, notes = _score_telegram_delivery(outcome, source_text, agent_name)
        else:
            outcome.quality_score = 0.0
            notes = [f"unsupported scenario kind: {scenario.kind}"]
        outcome.notes.extend(notes)
        outcome.overall_score = _clamp_score(
            (outcome.speed_score * rating["speed_weight"]) + (outcome.quality_score * rating["quality_weight"])
        )

    weighted_speed = 0.0
    weighted_quality = 0.0
    weighted_overall = 0.0
    total_weight = 0.0
    for outcome in outcomes:
        scenario = scenario_map.get(outcome.name.replace("_telegram", "")) or scenario_map.get(outcome.name)
        weight = float(scenario.weight if scenario else 1.0)
        weighted_speed += outcome.speed_score * weight
        weighted_quality += outcome.quality_score * weight
        weighted_overall += outcome.overall_score * weight
        total_weight += weight
    if total_weight <= 0:
        total_weight = float(len(outcomes) or 1)
    return TargetOutcome(
        label=target.label,
        provider=target.provider,
        model=target.model,
        transport=target.transport,
        speed_score=_clamp_score(weighted_speed / total_weight),
        quality_score=_clamp_score(weighted_quality / total_weight),
        overall_score=_clamp_score(weighted_overall / total_weight),
        grade=_grade_for_score(weighted_overall / total_weight if total_weight else 0.0),
        scenario_results=outcomes,
    )


class Command(BaseCommand):
    help = "Run a small provider/model/transport smoke matrix and score speed plus quality."

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            default=str(DEFAULT_CONFIG_PATH),
            help="Path to the matrix JSON config. Defaults to smoke/llm_provider_matrix.json.",
        )
        parser.add_argument(
            "--output",
            default="",
            help="Optional output path for the JSON report.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        config_path = Path(str(options.get("config") or DEFAULT_CONFIG_PATH)).resolve()
        config = _load_matrix_config(config_path)
        targets: list[MatrixTarget] = config["targets"]
        scenarios: list[MatrixScenario] = config["scenarios"]
        rating = config["rating"]

        runner = LLMRunner()
        report_targets: list[TargetOutcome] = []
        for target in targets:
            with _temporary_provider_transport(target.provider, target.transport):
                _, agent, agent_run = _ensure_seed_objects(target.label, target.model)
                _ensure_telegram_pairing(agent, target.label)
                outcomes: list[ScenarioOutcome] = []
                for scenario in scenarios:
                    if scenario.kind in {"chat", "tool"}:
                        outcome, _ = _run_llm_scenario(
                            runner=runner,
                            target=target,
                            agent=agent,
                            orchestration_run_id=str(agent_run.id),
                            scenario=scenario,
                        )
                    elif scenario.kind == "telegram":
                        source_text = next((item.text for item in outcomes if item.name == scenario.source_scenario), "")
                        outcome = _run_telegram_scenario(
                            agent=agent,
                            agent_run=agent_run,
                            label=target.label,
                            source_text=source_text,
                            source_scenario=scenario,
                            target_ms=scenario.target_ms,
                        )
                    else:
                        raise CommandError(f"Unsupported scenario kind: {scenario.kind}")
                    outcomes.append(outcome)

                target_outcome = _evaluate_outcomes(target, scenarios, outcomes, agent.name, rating)
                target_outcome.overall_score = _clamp_score(
                    (target_outcome.speed_score * rating["speed_weight"]) + (target_outcome.quality_score * rating["quality_weight"])
                )
                target_outcome.grade = _grade_for_score(target_outcome.overall_score)
                report_targets.append(target_outcome)

        report = self._build_report(config_path, rating, report_targets)
        self._emit_summary(report)
        output_path = str(options.get("output") or "").strip()
        if output_path:
            path = Path(output_path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            self.stdout.write(scrub_sensitive_text(f"report_written={path}"))
        self.stdout.write(scrub_sensitive_text(json.dumps(scrub_sensitive_value(report), indent=2, ensure_ascii=False)))

    def _build_report(
        self,
        config_path: Path,
        rating: dict[str, float],
        targets: Sequence[TargetOutcome],
    ) -> dict[str, Any]:
        ordered_targets = sorted(targets, key=lambda item: item.overall_score, reverse=True)
        return {
            "config_path": str(config_path),
            "rating": rating,
            "targets": [
                {
                    "label": target.label,
                    "provider": target.provider,
                    "model": target.model,
                    "transport": target.transport,
                    "speed_score": target.speed_score,
                    "quality_score": target.quality_score,
                    "overall_score": target.overall_score,
                    "grade": target.grade,
                    "scenario_results": [
                        {
                            "name": scenario.name,
                            "kind": scenario.kind,
                            "status": scenario.status,
                            "elapsed_ms": round(scenario.elapsed_ms, 2),
                            "speed_score": scenario.speed_score,
                            "quality_score": scenario.quality_score,
                            "overall_score": scenario.overall_score,
                            "text": scenario.text,
                            "tool_calls_executed": scenario.tool_calls_executed,
                            "tool_names": scenario.tool_names,
                            "notes": scenario.notes,
                            "telegram_delivered": scenario.telegram_delivered,
                        }
                        for scenario in target.scenario_results
                    ],
                    "notes": target.notes,
                }
                for target in ordered_targets
            ],
        }

    def _emit_summary(self, report: dict[str, Any]) -> None:
        targets = report.get("targets") or []
        scenario_count = len(targets[0].get("scenario_results") or []) if targets else 0
        self.stdout.write(
            scrub_sensitive_text(f"LLM provider matrix smoke summary ({len(targets)} targets x {scenario_count} scenarios)")
        )
        self.stdout.write("  rank | target | model | transport | speed | quality | overall | grade")
        for index, target in enumerate(targets, start=1):
            self.stdout.write(
                scrub_sensitive_text(
                    f"  {index:>4} | {target['label']} | {target['model']} | {target['transport']} | "
                    f"{target['speed_score']:.2f} | {target['quality_score']:.2f} | "
                    f"{target['overall_score']:.2f} | {target['grade']}"
                )
            )
