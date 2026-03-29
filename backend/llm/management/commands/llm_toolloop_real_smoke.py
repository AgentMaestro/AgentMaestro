from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from logging_utils import scrub_sensitive_text

from agents.models import Agent
from google_bridge.models import GoogleAccount
from llm.models import AgentRole, LLMModelProfile, LLMRun, MessageRole
from llm.services.runner import LLMRunner
from llm.services.tool_schemas import get_tool_arg_templates, get_tool_schemas
from runs.models import AgentRun


# RUN INSTRUCTIONS:
#   python manage.py llm_toolloop_real_smoke
# Ensure ToolRunner is running and OPENAI_API_KEY + related env vars are set.

ScenarioVerify = Callable[[dict[str, Any], list[str], list[str], LLMRun], list[str]]


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


@dataclass
class Scenario:
    name: str
    prompt: str
    verify: ScenarioVerify
    max_tool_rounds: int = 5


def _verify_repo_tree(
    result: dict[str, Any], tool_names: list[str], denials: list[str], run: LLMRun
) -> list[str]:
    text = (result.get("text") or "").strip()
    fails: list[str] = []
    if result.get("tool_calls_executed", 0) < 1:
        fails.append("tool_calls_executed < 1")
    if "repo_tree" not in tool_names:
        fails.append("repo_tree not executed")
    if len(text) <= 20:
        fails.append("final output too short")
    if result.get("status") != "completed":
        fails.append(f"status {result.get('status')} != completed")
    if denials:
        fails.append(f"policy denials present ({', '.join(denials)})")
    return fails


def _verify_search_code(
    result: dict[str, Any], tool_names: list[str], denials: list[str], run: LLMRun
) -> list[str]:
    text = (result.get("text") or "").lower()
    fails: list[str] = []
    if result.get("tool_calls_executed", 0) < 2:
        fails.append("tool_calls_executed < 2")
    if "search_code" not in tool_names or "file_read" not in tool_names:
        fails.append("required tools not executed (search_code + file_read)")
    if "agentmaestro/settings/base.py" not in text and "settings/base.py" not in text:
        fails.append("final output missing settings reference")
    if result.get("status") != "completed":
        fails.append(f"status {result.get('status')} != completed")
    if denials:
        fails.append(f"policy denials present ({', '.join(denials)})")
    return fails


def _verify_sandbox_patch(
    result: dict[str, Any], tool_names: list[str], denials: list[str], run: LLMRun
) -> list[str]:
    text = (result.get("text") or "").lower()
    fails: list[str] = []
    required = {"file_write", "file_patch", "file_read"}
    if not required.issubset(set(tool_names)):
        fails.append(f"missing required tools: {required - set(tool_names)}")
    if "updated" not in text:
        fails.append("final output missing 'updated'")
    if result.get("status") != "completed":
        fails.append(f"status {result.get('status')} != completed")
    if denials:
        fails.append(f"policy denials present ({', '.join(denials)})")
    return fails


def _strip_code_fence(text: str) -> str:
    fence_pattern = re.compile(r"```(?:json\\s*)?(.*?)```", re.S | re.I)
    match = fence_pattern.search(text)
    if match:
        return match.group(1).strip()
    return text


def _verify_shell(
    result: dict[str, Any], tool_names: list[str], denials: list[str], run: LLMRun
) -> list[str]:
    fails: list[str] = []
    if "shell_exec" not in tool_names:
        fails.append("shell_exec not executed")
    if "file_write" not in tool_names:
        fails.append("file_write not executed before shell_exec")
    shell_call = run.tool_calls.filter(tool_name="shell_exec").order_by("created_at").last()
    shell_stdout = ""
    if shell_call:
        tool_message = (
            run.messages.filter(role=MessageRole.TOOL, meta__tool_call_id=shell_call.id)
            .order_by("created_at")
            .last()
        )
        if tool_message:
            try:
                payload = json.loads(tool_message.content or "{}")
            except json.JSONDecodeError:
                payload = {}
            meta = payload.get("meta") or {}
            tool_result_payload = payload.get("result", {}).get("tool_result") or {}
            shell_stdout = (tool_result_payload.get("stdout") or "").strip()
            if not shell_stdout and isinstance(meta, dict):
                shell_stdout = (meta.get("stdout") or "").strip()
            if not shell_stdout:
                shell_stdout = (payload.get("result", {}).get("stdout") or "").strip()
    if shell_stdout.lower() != "probe":
        fails.append("shell_exec stdout != probe")
    if result.get("status") != "completed":
        fails.append(f"status {result.get('status')} != completed")
    if denials:
        fails.append(f"policy denials present ({', '.join(denials)})")
    return fails


EXPECTED_DIGEST = hashlib.sha256(b"AgentMaestro").hexdigest()


def _verify_python_digest(
    result: dict[str, Any], tool_names: list[str], denials: list[str], run: LLMRun
) -> list[str]:
    text = (result.get("text") or "").strip().lower()
    fails: list[str] = []
    if "python_exec" not in tool_names:
        fails.append("python_exec not executed")
    match = re.search(r"[0-9a-f]{60,64}", text)
    if not match:
        fails.append("final output missing hex digest")
    else:
        digest = match.group(0)
        if not EXPECTED_DIGEST.startswith(digest):
            fails.append("digest mismatch")
    if result.get("status") != "completed":
        fails.append(f"status {result.get('status')} != completed")
    if denials:
        fails.append(f"policy denials present ({', '.join(denials)})")
    return fails


def _verify_gmail_or_fanout(
    result: dict[str, Any], tool_names: list[str], denials: list[str], run: LLMRun
) -> list[str]:
    text = (result.get("text") or "").lower()
    fails: list[str] = []
    if "google_bridge" not in tool_names:
        fails.append("google_bridge not executed")
    bridge_call = run.tool_calls.filter(tool_name="google_bridge").order_by("created_at").last()
    strategy = ""
    call_count = 0
    if bridge_call and isinstance(bridge_call.result, dict):
        payload = dict(bridge_call.result.get("result") or bridge_call.result)
        strategy = str(payload.get("execution_strategy") or "").strip().lower()
        query_plan = dict(payload.get("query_plan") or {})
        call_count = int(query_plan.get("call_count") or 0)
    if strategy and strategy != "query_fanout":
        fails.append(f"expected query_fanout strategy, got {strategy}")
    if call_count < 3:
        fails.append(f"query_plan call_count too small: {call_count}")
    if "gmail" not in text:
        fails.append("final output missing gmail reference")
    if result.get("status") != "completed":
        fails.append(f"status {result.get('status')} != completed")
    if denials:
        fails.append(f"policy denials present ({', '.join(denials)})")
    return fails


def _ensure_google_orchestration_run(label: str, model_name: str) -> tuple[GoogleAccount, AgentRun]:
    account = (
        GoogleAccount.objects.select_related("workspace", "owner")
        .filter(is_active=True)
        .order_by("-last_synced_at", "-updated_at", "email", "google_subject")
        .first()
    )
    if account is None:
        raise CommandError("Gmail OR smoke requires at least one active GoogleAccount in the database.")

    suffix = uuid.uuid4().hex[:6]
    owner = account.owner
    agent = Agent.objects.create(
        workspace=account.workspace,
        owner=owner,
        name=f"Gmail smoke {label} {suffix}",
        default_model=model_name,
        soul="Keep replies concise and grounded.",
    )
    run = AgentRun.objects.create(
        workspace=account.workspace,
        agent=agent,
        started_by=owner,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.API,
        execution_mode=AgentRun.ExecutionMode.INTERACTIVE,
        trigger_kind=AgentRun.TriggerKind.SYSTEM,
        input_text=f"llm toolloop real smoke: {label}",
        started_at=timezone.now(),
    )
    return account, run


BACKEND_ROOT = str(settings.BASE_DIR.resolve())
SANDBOX_ROOT = str(Path(getattr(settings, "TOOLRUNNER_SANDBOX_ROOT", "/tmp/agentmaestro/sandbox")).resolve())
PROMPT_INSTRUCTION = (
    "You have access to repo_tree, search_code, file_read, file_write, file_patch, shell_exec, and python_exec. "
    "When a scenario asks you to use a tool, execute that tool before summarizing; do not invent data."
)
SCENARIOS: list[Scenario] = [
    Scenario(
        name="Repository orientation",
        prompt=(
            "Step 1: Immediately call repo_tree with absolute_root="
            f"{BACKEND_ROOT} root='.' max_depth=3 include_entries=true. Step 2: After receiving the tree, summarize the main Django directories and apps in one paragraph, mentioning 'llm' and 'manage.py'. Do not answer before completing the tool call."
        ),
        verify=_verify_repo_tree,
    ),
    Scenario(
        name="Targeted code lookup",
        prompt=(
            "Step 1: Use search_code with absolute_root="
            f"{BACKEND_ROOT} query='TOOLRUNNER_BASE_URL' include_globs=['**/*.py'] to find the file defining the URL. Step 2: Run file_read on that file and report exactly: 'The toolrunner URL is set in <file> as <value>'. Do not respond before performing both tool calls."
        ),
        verify=_verify_search_code,
    ),
    Scenario(
        name="Sandbox patch flow",
        prompt=(
            "Step 1: Within the sandbox root, call file_write with path='real_payload.txt', content='initial', and make_dirs=true (use the exact path key). "
            "Step 2: Call file_patch with a unified diff that replaces 'initial' with 'updated' in that file. "
            "Step 3: Call file_read to confirm the file now contains 'updated' and stop. "
            "No additional tool calls are needed once the read succeeds."
        ),
        verify=_verify_sandbox_patch,
        max_tool_rounds=12,
    ),
    Scenario(
        name="Shell + parse",
        prompt=(
            "Step 1: Within the sandbox ROOT, call file_write with path='shell_list_probe.txt' and content='probe'. "
            "Step 2: Run shell_exec with cmd=['powershell','-NoProfile','-Command','Get-Content -Raw -LiteralPath shell_list_probe.txt'] and cwd='.'. "
            "After the shell finishes, deliver exactly whatever stdout produced (no markdown)."
        ),
        verify=_verify_shell,
        max_tool_rounds=6,
    ),
    Scenario(
        name="Python exec compute",
        prompt=(
            "Use python_exec to run a script that prints hashlib.sha256(b'AgentMaestro').hexdigest(). "
            "Return ONLY the 64-character lowercase hex digest with no extra text or punctuation."
        ),
        verify=_verify_python_digest,
        max_tool_rounds=5,
    ),
    Scenario(
        name="Gmail OR fan-out",
        prompt=(
            "Use google_bridge once to run a Gmail list search with account_scope=all, include_read=true, max_results=50, "
            "and a query of from:(kayak.com OR hulumail.com OR ally.com). "
            "Your goal is to confirm the bridge fans this into separate Gmail clauses instead of treating it as a single literal string. "
            "After the tool call, report the query fan-out behavior and keep the answer short."
        ),
        verify=_verify_gmail_or_fanout,
        max_tool_rounds=4,
    ),
]


@dataclass
class ScenarioResult:
    name: str
    run_id: str
    tool_calls: list[str]
    final_output: str
    policy_denials: list[str]
    status: str
    tool_calls_executed: int


class Command(BaseCommand):
    help = "Runs real LLM prompts (via Maestro) against ToolRunner to validate the full wiring."

    def handle(self, *args: Any, **options: Any) -> None:
        api_key = os.getenv("OPENAI_API_KEY") or getattr(settings, "OPENAI_API_KEY", None)
        if not api_key:
            raise CommandError("OPENAI_API_KEY is required to run llm_toolloop_real_smoke")
        profile = self._ensure_profile()
        runner = LLMRunner()
        _google_account, google_run = _ensure_google_orchestration_run("gmail_or_fanout", profile.model)
        sanity = asyncio.run(runner.run(prompt="Say 'hi'", profile_name=profile.name, tools=None))
        if sanity["status"] != "completed":
            self.stdout.write(scrub_sensitive_text(f"Sanity check failed run_id={sanity['run_id']} error={sanity['error']}"))
            raise CommandError("Unable to complete a simple OpenAI call; check API/model/base_url")
        failures: list[str] = []
        for scenario in SCENARIOS:
            result = asyncio.run(
                runner.run(
                    prompt=f"{PROMPT_INSTRUCTION} {scenario.prompt}",
                    profile_name=profile.name,
                    tools=get_tool_schemas(),
                    max_tool_rounds=scenario.max_tool_rounds,
                    orchestration_run_id=str(google_run.id) if scenario.name == "Gmail OR fan-out" else None,
                )
            )
            run = LLMRun.objects.get(id=result["run_id"])
            tool_names = list(run.tool_calls.values_list("tool_name", flat=True))
            denials = [
                call.error
                for call in run.tool_calls.filter(error__startswith="tool_runner")
                if call.error
            ]
            fails = scenario.verify(result, tool_names, denials, run)
            tool_calls_label = ", ".join(tool_names)
            final_output = (result.get("text") or "").strip()
            self.stdout.write(scrub_sensitive_text(f"\nScenario: {scenario.name} {'PASS' if not fails else 'FAIL'}"))
            self.stdout.write(scrub_sensitive_text(f"  run_id={result['run_id']}"))
            self.stdout.write(scrub_sensitive_text(f"  tool_calls={result.get('tool_calls_executed')} [{tool_calls_label}]"))
            self.stdout.write(scrub_sensitive_text(f"  denials={len(denials)}"))
            self.stdout.write(scrub_sensitive_text(f"  final_output={final_output[:200]}"))
            if fails:
                for reason in fails:
                    self.stdout.write(scrub_sensitive_text(f"    failure: {reason}"))
                self.stdout.write(scrub_sensitive_text(f"  run_error={run.error}"))
                self.stdout.write(scrub_sensitive_text(f"  provider_meta={run.provider_meta}"))
                self.stdout.write("  tool call history:")
                for call in run.tool_calls.order_by("created_at"):
                    args_str = json.dumps(call.arguments or {}, ensure_ascii=False)
                    result_str = json.dumps(call.result or {}, ensure_ascii=False)
                    self.stdout.write(scrub_sensitive_text(f"    {call.tool_name}: args={_truncate(args_str, 300)}"))
                    self.stdout.write(scrub_sensitive_text(f"      error={call.error or '<none>'}"))
                    self.stdout.write(scrub_sensitive_text(f"      result={_truncate(result_str, 800)}"))
                    tool_message = (
                        run.messages.filter(role=MessageRole.TOOL, meta__tool_call_id=call.id)
                        .order_by("created_at")
                        .last()
                    )
                    if tool_message:
                        self.stdout.write(scrub_sensitive_text(f"      tool message={_truncate(tool_message.content or '', 800)}"))
                failures.append(scenario.name)
        if failures:
            raise CommandError(f"llm_toolloop_real_smoke failed: {len(failures)} scenarios failed")

    def _ensure_profile(self) -> LLMModelProfile:
        name = settings.LLM_DEFAULT_PROFILE_PLANNER
        provider = settings.LLM_PROVIDER
        defaults = {
            "agent_role": AgentRole.PLANNER,
            "provider": provider,
            "model": self._preferred_model(),
            "is_active": True,
        }
        profile, created = LLMModelProfile.objects.get_or_create(name=name, defaults=defaults)
        updated = False
        if profile.provider != provider:
            profile.provider = provider
            updated = True
        if profile.model in (None, "", "test-model"):
            profile.model = self._preferred_model()
            updated = True
        if updated:
            profile.save()
        return profile

    def _preferred_model(self) -> str:
        candidate = getattr(settings, "LLM_DEFAULT_MODEL", None)
        if candidate:
            return candidate
        candidate = os.getenv("LLM_DEFAULT_MODEL") or os.getenv("OPENAI_MODEL")
        if candidate:
            return candidate
        return "gpt-4o-mini"
