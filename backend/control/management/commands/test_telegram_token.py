"""Verify a Telegram bot token via the `getMe` endpoint."""

from __future__ import annotations

import os

import httpx
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Call Telegram's getMe to ensure the provided bot token is valid."

    def add_arguments(self, parser):
        parser.add_argument(
            "--token",
            "-t",
            help="Telegram bot token. Falls back to TELEGRAM_BOT_TOKEN env var if omitted.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=10.0,
            help="HTTP timeout when calling Telegram (seconds).",
        )

    def handle(self, *args, **options):
        token = options["token"] or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise CommandError("No Telegram bot token provided via --token or TELEGRAM_BOT_TOKEN env.")

        timeout = options["timeout"]
        url = f"https://api.telegram.org/bot{token}/getMe"

        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_detail = self._extract_error_detail(exc)
            raise CommandError(f"Telegram returned {exc.response.status_code}: {error_detail}")
        except httpx.RequestError as exc:
            raise CommandError(f"Unable to reach Telegram: {exc}") from exc

        payload = response.json()
        result = payload.get("result") or {}
        bot_id = result.get("id")
        first_name = result.get("first_name")
        username = result.get("username")
        is_bot = result.get("is_bot")

        self.stdout.write("Telegram token validated.")
        self.stdout.write(f"  bot_id={bot_id}")
        self.stdout.write(f"  first_name={first_name}")
        self.stdout.write(f"  username={username}")
        self.stdout.write(f"  is_bot={is_bot}")

    def _extract_error_detail(self, exc: httpx.HTTPStatusError) -> str:
        if not exc.response.headers.get("content-type", "").startswith("application/json"):
            return exc.response.text or exc.response.reason_phrase or str(exc)
        try:
            payload = exc.response.json()
        except ValueError:
            return exc.response.text or exc.response.reason_phrase or str(exc)

        description = payload.get("description")
        return description or exc.response.text or exc.response.reason_phrase or str(exc)
