import asyncio
import json
import os
from typing import List

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from llm.models import AgentRole, LLMModelProfile, LLMRun
from llm.services.runner import LLMRunner
from llm.services.tool_schemas import get_tool_schemas


class Command(BaseCommand):
    help = "Stage 2 smoke test: OpenAI Responses WebSocket tool loop."

    def handle(self, *args, **options):
        prev_transport = os.environ.get("OPENAI_TRANSPORT")
        os.environ["OPENAI_TRANSPORT"] = "ws"
        run_id = None
        try:
            profile = self._ensure_profile()
            runner = LLMRunner()
            prompt = (
                "Step 1: Call the file_write tool with "
                "path='smoke/shell_list_probe.txt', content='probe', overwrite=true, make_dirs=true. "
                "Step 2: Call shell_exec with cmd=['powershell','-NoProfile','-Command',"
                "\"Get-Content -Raw -LiteralPath 'smoke/shell_list_probe.txt'\"'], cwd='.'. "
                "Step 3: Output EXACTLY the raw stdout from shell_exec and nothing else (no markdown, no explanation)."
            )
            result = asyncio.run(
                runner.run(
                    prompt=prompt,
                    profile_name=profile.name,
                    tools=get_tool_schemas(),
                    max_tool_rounds=5,
                )
            )
            run_id = result.get("run_id")
        finally:
            if prev_transport is None:
                os.environ.pop("OPENAI_TRANSPORT", None)
            else:
                os.environ["OPENAI_TRANSPORT"] = prev_transport
        self.stdout.write(f"run_id={run_id}")
        try:
            run = LLMRun.objects.get(id=run_id)
        except LLMRun.DoesNotExist:
            raise CommandError(f"Run {run_id} not found")

        if result.get("status") != "completed":
            self._fail("WS tool smoke did not complete successfully.", run)
        if result.get("tool_calls_executed", 0) < 2:
            self._fail("Expected at least 2 tool calls during the WS smoke run.", run)

        tool_names = list(run.tool_calls.order_by("created_at").values_list("tool_name", flat=True))
        required_tools = {"file_write", "shell_exec"}
        if not required_tools.issubset(set(tool_names)):
            self._fail("Expected file_write and shell_exec tool calls.", run)

        shell_call = run.tool_calls.filter(tool_name="shell_exec").order_by("created_at").last()
        if not shell_call:
            self._fail("No shell_exec tool call found.", run)
        shell_result = shell_call.result or {}
        stdout = str(shell_result.get("stdout") or "").strip()
        if "probe" not in stdout.lower():
            self._fail(f"shell_exec stdout did not contain probe (stdout={stdout[:200]!r})", run)

        self.stdout.write("Stage 2 WS tool smoke succeeded.")

    def _fail(self, message: str, run: LLMRun) -> None:
        debug = self._format_debug(run)
        raise CommandError(f"{message}\n\nDebug:\n{debug}")

    def _format_debug(self, run: LLMRun) -> str:
        lines: List[str] = ["tool call history:"]
        for call in run.tool_calls.order_by("created_at"):
            args = json.dumps(call.arguments or {}, ensure_ascii=False)
            result = call.result or {}
            stdout = self._truncate(str(result.get("stdout") or ""))
            stderr = self._truncate(str(result.get("stderr") or ""))
            lines.append(
                f"  {call.tool_name} args={args} stdout={stdout!r} stderr={stderr!r}"
            )
        provider_meta = run.provider_meta or {}
        response_id = provider_meta.get("openai_response_id")
        lines.append(f"provider_meta.openai_response_id={response_id}")
        return "\n".join(lines)

    def _truncate(self, value: str, limit: int = 120) -> str:
        value = value.replace("\n", " ").strip()
        if len(value) <= limit:
            return value
        return value[:limit] + "..."

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
