"""Send a quick 'hi' message to a Telegram chat using the configured bot."""

from __future__ import annotations

import json
import os

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from logging_utils import scrub_sensitive_text


class Command(BaseCommand):
    help = "Deliver a short 'hi' message via the Telegram bot token to a chat ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--token",
            "-t",
            help="Telegram bot token. Falls back to TELEGRAM_BOT_TOKEN env var if omitted.",
        )
        parser.add_argument(
            "--chat-id",
            "-c",
            help="Telegram chat ID that should receive the message. Falls back to TELEGRAM_CHAT_ID env var.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=10.0,
            help="HTTP timeout when calling Telegram (seconds).",
        )

    def handle(self, *args, **options):
        token = (
            options["token"]
            or getattr(settings, "TELEGRAM_BOT_TOKEN", None)
            or os.environ.get("TELEGRAM_BOT_TOKEN")
        )
        if not token:
            raise CommandError("No Telegram bot token provided via --token or TELEGRAM_BOT_TOKEN env.")

        chat_id = (
            options["chat_id"]
            or getattr(settings, "TELEGRAM_CHAT_ID", None)
            or os.environ.get("TELEGRAM_CHAT_ID")
        )
        if not chat_id:
            raise CommandError("No chat ID provided via --chat-id or TELEGRAM_CHAT_ID env.")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": "hi...what's going on?  This is Scott sending this."}
        try:
            response = httpx.post(url, json=payload, timeout=options["timeout"])
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_detail = self._extract_error_detail(exc)
            raise CommandError(f"Telegram returned {exc.response.status_code}: {error_detail}")
        except httpx.RequestError as exc:
            raise CommandError(f"Unable to reach Telegram: {exc}") from exc

        result = (response.json() or {}).get("result", {})
        message_id = result.get("message_id")
        chat = result.get("chat") or {}
        self.stdout.write(scrub_sensitive_text("Telegram message sent."))
        self.stdout.write(scrub_sensitive_text(f"  chat_id={chat_id}"))
        self.stdout.write(scrub_sensitive_text(f"  message_id={message_id}"))
        if chat:
            self.stdout.write(scrub_sensitive_text("  chat=" + json.dumps(chat)))

    def _extract_error_detail(self, exc: httpx.HTTPStatusError) -> str:
        if not exc.response or not exc.response.headers.get("content-type", "").startswith("application/json"):
            return exc.response.text or exc.response.reason_phrase or str(exc)
        try:
            payload = exc.response.json()
        except ValueError:
            return exc.response.text or exc.response.reason_phrase or str(exc)
        return payload.get("description") or exc.response.text or exc.response.reason_phrase or str(exc)
