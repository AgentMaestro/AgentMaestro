from __future__ import annotations

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import os
import sys
import logging


def setup_console_logger(name: str = __name__, level: int = logging.DEBUG) -> logging.Logger:
    """
    Set up and return a logger that outputs to the console.

    :param name: Logger name (usually __name__)
    :param level: Logging level (e.g., logging.DEBUG, logging.INFO)
    :return: Configured Logger object
    """
    # Create a logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent adding multiple handlers if logger is reused
    if not logger.handlers:
        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)

        # Define log format
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(formatter)

        # Add handler to logger
        logger.addHandler(console_handler)

    return logger

log = setup_console_logger(__name__, logging.DEBUG)


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

        log.info(f"Token={token}")
        log.info(f"Chat_ID={chat_id}")
        log.info(f"Message={message}")

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
        self.stdout.write(f"Sent '{message}' to chat {chat_id} (message_id={message_id}).")
