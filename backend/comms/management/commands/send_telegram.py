from __future__ import annotations

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import os

from logging_utils import get_app_logger, scrub_sensitive_text

logger = get_app_logger(__name__)


class Command(BaseCommand):
    help = "Send a simple 'hi' message to the Telegram chat."

    def add_arguments(self, parser):
        parser.add_argument("--token", help="Bot token to use", default=None)
        parser.add_argument("--chat-id", help="Telegram chat ID to send to", default=None)
        parser.add_argument("--message", help="Message to send (default: hi)", default=None)

    def handle(self, *args, **options):
        token = options.get("token")
        if token is None:
            token = settings.TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN")

        chat_id = options.get("chat_id")
        if chat_id is None:
            chat_id = settings.TELEGRAM_CHAT_ID or os.getenv("TELEGRAM_CHAT_ID")

        message = options.get("message") or "hi"

        logger.info("Token=%s", scrub_sensitive_text(token))
        logger.info("Chat_ID=%s", scrub_sensitive_text(chat_id))
        logger.info("Message=%s", scrub_sensitive_text(message))

        if not token or not chat_id:
            raise CommandError("Bot token and chat ID must be provided (via options or TELEGRAM_* settings).")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        try:
            response = httpx.post(url, json=payload, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CommandError(f"Telegram API error: {exc.response.text}") from exc

        data = response.json()
        if not data.get("ok"):
            raise CommandError(f"Telegram reported failure: {data}")

        message_id = data.get("result", {}).get("message_id")
        self.stdout.write(scrub_sensitive_text(f"Sent '{message}' to chat {chat_id} (message_id={message_id})."))
