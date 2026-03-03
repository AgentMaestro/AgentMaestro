from __future__ import annotations

from typing import Mapping

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Validate a Telegram bot token using getMe."

    def add_arguments(self, parser):
        parser.add_argument("--token", help="Bot token to validate", default=None)

    def handle(self, *args, **options):
        token = options.get("token") or settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError("Telegram bot token must be set (pass --token or TELEGRAM_BOT_TOKEN).")

        url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            response = httpx.get(url, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CommandError(f"Telegram rejected the token: {exc}") from exc

        payload: Mapping[str, object] = response.json()
        if not payload.get("ok"):
            raise CommandError("Telegram reported token validation failed.")

        user = payload.get("result") or {}
        self.stdout.write("Telegram token validated.")
        self.stdout.write(f"  bot_id={user.get('id')}")
        self.stdout.write(f"  first_name={user.get('first_name')}")
        self.stdout.write(f"  username={user.get('username')}")
        self.stdout.write(f"  is_bot={user.get('is_bot')}")
