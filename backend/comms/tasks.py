from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Iterable, Mapping, Optional, Tuple

import redis
from celery import shared_task
from django.conf import settings
from django.db import transaction

from comms.models import TransportEndpoint
from comms.services.ingest import ingest_normalized_event
from comms.transports.base import NormalizedEvent
from comms.transports.telegram import TelegramAdapter
import httpx
from httpcore import ReadTimeout as HttpcoreReadTimeout

logger = logging.getLogger(__name__)


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


def _acquire_redis_lock(endpoint_id: int) -> Optional[_RedisLockHandle]:
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
            return None
        logger.debug("Acquired redis lock %s for telegram endpoint %s.", lock_name, endpoint_id)
        return _RedisLockHandle(client, lock)
    except redis.RedisError as exc:
        logger.warning("Error while trying to acquire redis lock for telegram poll: %s", exc)
        client.close()
        return None


MAX_TELEGRAM_POLL_TIMEOUT_RETRIES = int(
    os.getenv("TELEGRAM_POLL_TIMEOUT_RETRIES", getattr(settings, "TELEGRAM_POLL_TIMEOUT_RETRIES", 3))
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


@shared_task
def telegram_poll_once(endpoint_id: int) -> None:
    lock_handle = _acquire_redis_lock(endpoint_id)
    if lock_handle:
        try:
            try:
                endpoint = TransportEndpoint.objects.select_related("transport").get(id=endpoint_id)
            except TransportEndpoint.DoesNotExist:
                logger.warning("Telegram endpoint %s not found while holding redis lock.", endpoint_id)
                return
            _run_poll(endpoint)
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
        _run_poll(endpoint)


@shared_task
def telegram_poll_scheduler() -> None:
    endpoint_ids = list(
        TransportEndpoint.objects.filter(
            transport__key="telegram", transport__is_enabled=True
        )
        .values_list("id", flat=True)
    )
    for endpoint_id in endpoint_ids:
        telegram_poll_once.delay(endpoint_id)
