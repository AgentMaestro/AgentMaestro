from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import time
from datetime import datetime, timezone

import httpx
from django.conf import settings

from logging_utils import get_app_logger

logger = get_app_logger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(slots=True)
class GoogleBridgeHttpConfig:
    timeout_seconds: float
    retry_attempts: int
    retry_backoff_seconds: float
    retry_max_backoff_seconds: float


def load_google_bridge_http_config() -> GoogleBridgeHttpConfig:
    timeout_seconds = float(getattr(settings, "GOOGLE_BRIDGE_TIMEOUT_SECONDS", 30.0) or 30.0)
    retry_attempts = int(getattr(settings, "GOOGLE_BRIDGE_RETRY_ATTEMPTS", 2) or 2)
    retry_backoff_seconds = float(getattr(settings, "GOOGLE_BRIDGE_RETRY_BACKOFF_SECONDS", 1.0) or 1.0)
    retry_max_backoff_seconds = float(getattr(settings, "GOOGLE_BRIDGE_RETRY_MAX_BACKOFF_SECONDS", 8.0) or 8.0)
    return GoogleBridgeHttpConfig(
        timeout_seconds=max(1.0, timeout_seconds),
        retry_attempts=max(0, retry_attempts),
        retry_backoff_seconds=max(0.1, retry_backoff_seconds),
        retry_max_backoff_seconds=max(0.1, retry_max_backoff_seconds),
    )


def request_with_retries(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    json: dict | None = None,
    data: dict | None = None,
) -> httpx.Response:
    config = load_google_bridge_http_config()
    last_error: Exception | None = None

    for attempt in range(config.retry_attempts + 1):
        with httpx.Client(timeout=httpx.Timeout(config.timeout_seconds), headers=headers) as client:
            try:
                response = client.request(method, url, params=params, json=json, data=data)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt >= config.retry_attempts:
                    raise
                delay = _sleep_before_retry(config, attempt)
                logger.warning(
                    "Retrying Google Bridge request after transport error method=%s url=%s attempt=%s/%s delay=%.2f",
                    method,
                    url,
                    attempt + 1,
                    config.retry_attempts + 1,
                    delay,
                )
                continue

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < config.retry_attempts:
            delay = _sleep_before_retry(config, attempt, response=response)
            logger.warning(
                "Retrying Google Bridge request after status=%s method=%s url=%s attempt=%s/%s delay=%.2f",
                response.status_code,
                method,
                url,
                attempt + 1,
                config.retry_attempts + 1,
                delay,
            )
            continue
        return response

    if last_error is not None:
        raise last_error
    raise httpx.TransportError("Google Bridge request failed.")


def _sleep_before_retry(
    config: GoogleBridgeHttpConfig,
    attempt: int,
    *,
    response: httpx.Response | None = None,
) -> float:
    retry_after = _parse_retry_after_seconds(response)
    if retry_after is None:
        delay = min(config.retry_backoff_seconds * (2**attempt), config.retry_max_backoff_seconds)
    else:
        delay = min(retry_after, config.retry_max_backoff_seconds)
    if delay > 0:
        time.sleep(delay)
    return delay


def _parse_retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    retry_after = str(response.headers.get("Retry-After") or "").strip()
    if not retry_after:
        return None
    if retry_after.isdigit():
        return float(int(retry_after))
    try:
        retry_at = parsedate_to_datetime(retry_after)
    except (TypeError, ValueError, IndexError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    if retry_at is None or retry_at.tzinfo is None:
        return None
    now = datetime.now(timezone.utc).astimezone(retry_at.tzinfo)
    delay = (retry_at - now).total_seconds()
    return max(0.0, delay)
