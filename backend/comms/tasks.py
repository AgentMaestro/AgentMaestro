from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable, Mapping, Optional, Tuple

import redis
import httpx
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from comms.models import TransportEndpoint
from comms.services.ingest import ingest_normalized_event
from comms.services.remote_ops import expire_remote_approval_tickets
from comms.transports.base import NormalizedEvent
from comms.transports.telegram import TelegramAdapter
from httpcore import ReadTimeout as HttpcoreReadTimeout
from logging_utils import get_app_logger

logger = get_app_logger(__name__)
_POLL_LOCK_BUSY = object()


class _RedisLockHandle:
    def __init__(self, client: redis.Redis, lock: redis.lock.Lock):
        self.client = client
        self.lock = lock

    def release(self) -> None:
        try:
            self.lock.release()
        except redis.exceptions.LockError:
            logger.debug("Redis lock already released for telegram poll.")
        finally:
            try:
                self.client.close()
            except redis.exceptions.RedisError as exc:
                logger.debug("Failed to close redis client after releasing telegram lock: %s", exc)


def _acquire_redis_lock(endpoint_id: int) -> Optional[_RedisLockHandle] | object:
    poll_lock_url = settings.TELEGRAM_POLL_LOCK_REDIS_URL
    if not poll_lock_url:
        return None
    try:
        client = redis.from_url(poll_lock_url, decode_responses=True)
    except redis.RedisError as exc:
        logger.debug("Unable to create redis client for telegram poll lock: %s", exc)
        return None

    lock_name = f"comms:telegram:poll:{endpoint_id}"
    lock = client.lock(lock_name, timeout=settings.TELEGRAM_POLL_LOCK_TIMEOUT_SECONDS)
    try:
        if not lock.acquire(blocking=False):
            client.close()
            logger.debug("Telegram poll lock is already held for endpoint %s.", endpoint_id)
            return _POLL_LOCK_BUSY
        logger.debug("Acquired redis lock %s for telegram endpoint %s.", lock_name, endpoint_id)
        return _RedisLockHandle(client, lock)
    except redis.RedisError as exc:
        logger.warning("Error while trying to acquire redis lock for telegram poll: %s", exc)
        client.close()
        return None


MAX_TELEGRAM_POLL_TIMEOUT_RETRIES = int(
    os.getenv("TELEGRAM_POLL_TIMEOUT_RETRIES", getattr(settings, "TELEGRAM_POLL_TIMEOUT_RETRIES", 1))
)


def _fetch_updates(
    endpoint: TransportEndpoint, offset: int, timeout: int
) -> Tuple[list[Mapping[str, Any]], list[NormalizedEvent]]:

    async def _poll() -> Tuple[list[Mapping[str, Any]], list[NormalizedEvent]]:
        async with TelegramAdapter() as adapter:
            attempts = 0
            while True:
                try:
                    raw = await adapter.poll_updates(endpoint, offset=offset, timeout=timeout)
                    normalized: list[NormalizedEvent] = []
                    for update in raw:
                        normalized.extend(adapter.normalize_update(update))
                    return raw, normalized
                except (httpx.ReadTimeout, HttpcoreReadTimeout):
                    attempts += 1
                    logger.warning(
                        "Telegram poll timed out for endpoint %s (attempt %s/%s)",
                        endpoint.id,
                        attempts,
                        MAX_TELEGRAM_POLL_TIMEOUT_RETRIES,
                    )
                    if attempts >= MAX_TELEGRAM_POLL_TIMEOUT_RETRIES:
                        logger.error(
                            "Telegram endpoint %s exceeded timeout retries (%s).",
                            endpoint.id,
                            MAX_TELEGRAM_POLL_TIMEOUT_RETRIES,
                        )
                        return [], []
                    await asyncio.sleep(0.5)

    return asyncio.run(_poll())


def _telegram_polling_disabled(endpoint: TransportEndpoint) -> bool:
    config = endpoint.config or {}
    return bool(config.get("telegram_polling_disabled"))


def _disable_telegram_polling(endpoint: TransportEndpoint, *, reason: str) -> None:
    config = dict(endpoint.config or {})
    config["telegram_polling_disabled"] = True
    config["telegram_polling_disabled_reason"] = reason
    config["telegram_polling_disabled_at"] = timezone.now().isoformat()
    endpoint.config = config
    endpoint.save(update_fields=["config"])
    logger.warning(
        "Disabled Telegram polling for endpoint %s reason=%s",
        endpoint.id,
        reason,
    )


def _run_poll(endpoint: TransportEndpoint) -> None:
    config = dict(endpoint.config or {})
    last_update_id = int(config.get("last_update_id") or 0)
    offset = last_update_id + 1
    raw_updates, normalized_events = _fetch_updates(
        endpoint, offset, settings.TELEGRAM_POLL_TIMEOUT_SECONDS
    )

    max_update_id = last_update_id
    for update in raw_updates:
        update_id = update.get("update_id")
        if update_id is None:
            continue
        try:
            current = int(update_id)
        except (TypeError, ValueError):
            continue
        if current > max_update_id:
            max_update_id = current

    for event in normalized_events:
        ingest_normalized_event(endpoint.transport.key, endpoint.id, event)

    if max_update_id > last_update_id:
        config["last_update_id"] = max_update_id
        endpoint.config = config
        endpoint.save(update_fields=["config"])


def _telegram_endpoint_signature(endpoint: TransportEndpoint) -> str:
    config = endpoint.config or {}
    bot_id = str(config.get("bot_id") or "").strip()
    if bot_id:
        return f"bot_id:{bot_id}"
    bot_username = str(config.get("bot_username") or "").strip().lower()
    if bot_username:
        return f"bot_username:{bot_username}"
    bot_token_env = str(config.get("bot_token_env") or "").strip()
    if bot_token_env:
        return f"bot_token_env:{bot_token_env}"
    return f"endpoint:{endpoint.id}"


def _canonical_telegram_endpoint_ids() -> list[int]:
    chosen_by_signature: dict[str, int] = {}
    endpoint_ids: list[int] = []
    endpoints = (
        TransportEndpoint.objects.filter(
            transport__key="telegram",
            transport__is_enabled=True,
            kind="bot",
        )
        .select_related("transport")
        .order_by("-id")
    )
    for endpoint in endpoints:
        if _telegram_polling_disabled(endpoint):
            logger.debug("Skipping disabled Telegram polling endpoint %s.", endpoint.id)
            continue
        signature = _telegram_endpoint_signature(endpoint)
        if signature in chosen_by_signature:
            logger.debug(
                "Skipping duplicate Telegram polling endpoint %s for signature %s.",
                endpoint.id,
                signature,
            )
            continue
        chosen_by_signature[signature] = endpoint.id
        endpoint_ids.append(endpoint.id)
    return endpoint_ids


@shared_task
def telegram_poll_once(endpoint_id: int) -> None:
    lock_handle = _acquire_redis_lock(endpoint_id)
    if lock_handle is _POLL_LOCK_BUSY:
        logger.debug("Skipping telegram poll for endpoint %s because another poll is already active.", endpoint_id)
        return
    if lock_handle:
        try:
            try:
                endpoint = TransportEndpoint.objects.select_related("transport").get(id=endpoint_id)
            except TransportEndpoint.DoesNotExist:
                logger.warning("Telegram endpoint %s not found while holding redis lock.", endpoint_id)
                return
            try:
                _run_poll(endpoint)
            except httpx.HTTPStatusError as exc:
                status_code = getattr(exc.response, "status_code", None)
                if status_code in {401, 403}:
                    _disable_telegram_polling(
                        endpoint,
                        reason=f"telegram_http_{status_code}",
                    )
                    return
                raise
        finally:
            lock_handle.release()
        return

    with transaction.atomic():
        try:
            endpoint = (
                TransportEndpoint.objects.select_related("transport")
                .select_for_update()
                .get(id=endpoint_id)
            )
        except TransportEndpoint.DoesNotExist:
            logger.warning("Telegram endpoint %s not found for polling.", endpoint_id)
            return
        try:
            _run_poll(endpoint)
        except httpx.HTTPStatusError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in {401, 403}:
                _disable_telegram_polling(
                    endpoint,
                    reason=f"telegram_http_{status_code}",
                )
                return
            raise


@shared_task
def telegram_poll_scheduler() -> None:
    endpoint_ids = _canonical_telegram_endpoint_ids()
    for endpoint_id in endpoint_ids:
        telegram_poll_once.delay(endpoint_id)

@shared_task
def expire_remote_approval_tickets_task() -> int:
    expired = expire_remote_approval_tickets()
    if expired:
        logger.info("Expired %s remote approval ticket(s).", expired)
    return expired

