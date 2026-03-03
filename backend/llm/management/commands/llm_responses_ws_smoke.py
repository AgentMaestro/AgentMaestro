import asyncio
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from llm.services.providers.openai_client import OpenAIClient


class Command(BaseCommand):
    help = "Smoke test OpenAI Responses WebSocket transport (single-turn)."

    def handle(self, *args, **options):
        os.environ["OPENAI_TRANSPORT"] = "ws"
        client = OpenAIClient()
        model = getattr(settings, "LLM_DEFAULT_MODEL", None) or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        prompt = "Return the string OK_WS_SMOKE"
        response = asyncio.run(
            client.complete(
                [{"role": "user", "content": prompt}],
                model=model,
            )
        )
        text = (response.get("text") or "").strip()
        response_id = response.get("response_id")
        self.stdout.write(f"response_id={response_id}")
        self.stdout.write(f"text={text}")
        if "OK_WS_SMOKE" not in text:
            raise CommandError("OpenAI WebSocket response did not contain OK_WS_SMOKE")
