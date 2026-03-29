import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from django.conf import settings
from scrubadub import Scrubber

_SCRUBBER = Scrubber()
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]{12,}")
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_SLACK_LEGACY_TOKEN_RE = re.compile(r"\bslack-token-[A-Za-z0-9._\-~+/=]{8,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{10,}\b")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_SECRET_LABEL_RE = re.compile(
    r"(?i)\b(token|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*([:=])\s*([^\s,;]+)"
)


def _protect_pattern(text: str, pattern: re.Pattern[str], placeholders: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        placeholder = f"__AM_PROTECTED_{len(placeholders)}_{uuid.uuid4().hex}__"
        placeholders[placeholder] = match.group(0)
        return placeholder

    return pattern.sub(_replace, text)


def scrub_sensitive_text(text: str | None) -> str:
    if text is None:
        return ""

    candidate = str(text)
    placeholders: dict[str, str] = {}
    protected = _protect_pattern(candidate, _EMAIL_RE, placeholders)
    protected = _protect_pattern(protected, _URL_RE, placeholders)
    protected = _BEARER_RE.sub("Bearer [REDACTED]", protected)
    protected = _OPENAI_KEY_RE.sub("[REDACTED]", protected)
    protected = _SLACK_TOKEN_RE.sub("[REDACTED]", protected)
    protected = _SLACK_LEGACY_TOKEN_RE.sub("[REDACTED]", protected)
    protected = _GITHUB_TOKEN_RE.sub("[REDACTED]", protected)
    protected = _TELEGRAM_TOKEN_RE.sub("[REDACTED]", protected)
    protected = _SECRET_LABEL_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", protected
    )

    if list(_SCRUBBER.iter_filth(protected)):
        protected = _SCRUBBER.clean(protected)

    for placeholder, original in placeholders.items():
        protected = protected.replace(placeholder, original)
    return protected


def scrub_sensitive_text_with_types(text: str | None) -> tuple[str, list[str]]:
    if text is None:
        return "", []

    candidate = str(text)
    placeholders: dict[str, str] = {}
    protected = _protect_pattern(candidate, _EMAIL_RE, placeholders)
    protected = _protect_pattern(protected, _URL_RE, placeholders)
    filths = list(_SCRUBBER.iter_filth(protected))
    sanitized = scrub_sensitive_text(candidate)

    secret_types: list[str] = []
    for filth in filths:
        type_name = getattr(filth, "filth_type", None) or getattr(filth, "type", None)
        if not type_name:
            type_name = filth.__class__.__name__
        secret_types.append(str(type_name))
    return sanitized, secret_types


def scrub_sensitive_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_sensitive_text(value)
    if isinstance(value, Mapping):
        return {key: scrub_sensitive_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_sensitive_value(item) for item in value)
    if isinstance(value, set):
        return {scrub_sensitive_value(item) for item in value}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return type(value)(scrub_sensitive_value(item) for item in value)
    return value


class ScrubbingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub_sensitive_text(super().format(record))


def get_app_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    debug_enabled = False
    if getattr(settings, "configured", False):
        debug_enabled = bool(getattr(settings, "DEBUG", False))
    level = logging.DEBUG if debug_enabled else logging.INFO
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(ScrubbingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.setLevel(level)
        logger.addHandler(handler)
        logger.propagate = False
    else:
        for handler in logger.handlers:
            handler.setLevel(level)
            if not isinstance(handler.formatter, ScrubbingFormatter):
                handler.setFormatter(
                    ScrubbingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
                )
    return logger
