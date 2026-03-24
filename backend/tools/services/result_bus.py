from __future__ import annotations

import json
import inspect
import os
from typing import Any, Dict, List, Optional

import redis
from django.conf import settings
from django.utils import timezone

from core.utils.redis_checks import validate_redis_db
from logging_utils import get_app_logger

logger = get_app_logger(__name__)


def _get_redis_url() -> str:
    candidates = [
        os.getenv("REDIS_TOOL_BUS_URL"),
        getattr(settings, "REDIS_TOOL_BUS_URL", None),
        getattr(settings, "CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"),
    ]
    url = next((candidate for candidate in candidates if candidate), "")
    if not url:
        url = candidates[-1]
    validate_redis_db(url, 0, "result_bus")
    logger.info(f"result_bus:_get_redis_url return: {url}")

    return url


_redis_client: Optional[redis.Redis] = None
_redis_settings_url = getattr(settings, "REDIS_TOOL_BUS_URL", None)
if _redis_settings_url:
    try:
        validate_redis_db(_redis_settings_url, 0, "result_bus")
        _redis_client = redis.from_url(_redis_settings_url, decode_responses=False)
        logger.info("result_bus: initialized Redis client from settings.REDIS_TOOL_BUS_URL")
    except Exception:
        logger.exception(
            "result_bus: failed to initialize Redis client from settings.REDIS_TOOL_BUS_URL",
            exc_info=True,
        )
else:
    try:
        fallback_url = _get_redis_url()
        _redis_client = redis.from_url(fallback_url, decode_responses=False)
        logger.info("result_bus: initialized Redis client from fallback url %s", fallback_url)
    except Exception:
        logger.exception("result_bus: failed to initialize Redis client from fallback url", exc_info=True)


def get_redis_client(requestor="") -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(_get_redis_url(), decode_responses=False)
    logger.debug(f"result_bus:get_redis_client()  REQUESTOR: {requestor}  CLIENT: {_redis_client}")
    return _redis_client


def _request_redis_client(requestor: str) -> redis.Redis:
    getter = get_redis_client
    try:
        signature = inspect.signature(getter)
    except (TypeError, ValueError):
        signature = None
    if signature and len(signature.parameters) == 0:
        client = getter()
        logger.debug("result_bus:_request_redis_client() fallback without requestor=%s client=%s", requestor, client)
        return client
    return getter(requestor)


def make_tool_result_key(tool_call_id: str) -> str:
    return f"tool_result:{tool_call_id}"


def make_run_pending_list_key(run_id: str) -> str:
    return f"run:{run_id}:pending_tool_results"


def store_tool_result(
    run_id: str,
    tool_call_id: str,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int = 3600,
) -> None:
    client = _request_redis_client("store_tool_result")
    key = make_tool_result_key(tool_call_id)
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    list_key = make_run_pending_list_key(run_id)
    pipe = client.pipeline()
    pipe.set(key, payload_json)
    pipe.expire(key, ttl_seconds)
    pipe.rpush(list_key, tool_call_id)
    pipe.expire(list_key, ttl_seconds)
    pipe_size = pipe.execute()
    logger.info(
        "stored tool result run=%s tool_call_id=%s provider_call_id=%s queue_before=%s ttl=%s",
        run_id,
        tool_call_id,
        payload.get("provider_call_id"),
        client.llen(list_key),
        ttl_seconds,
    )


def get_tool_result(tool_call_id: str) -> Optional[Dict[str, Any]]:
    client = _request_redis_client("get_tool_result")
    payload = client.get(make_tool_result_key(tool_call_id))
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        logger.exception("Invalid JSON payload for tool_call_id=%s", tool_call_id)
        return None


def delete_tool_result(tool_call_id: str) -> None:
    client = _request_redis_client("delete_tool_result")
    client.delete(make_tool_result_key(tool_call_id))


def pop_pending_tool_results(run_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    client = _request_redis_client("pop_pending_tool_results")
    logger.debug("result_bus.pop_pending_tool_results redis client = %s", client)

    list_key = make_run_pending_list_key(run_id)
    tool_call_ids: List[str] = []
    for _ in range(limit):
        value = client.lpop(list_key)
        if not value:
            break
        tool_call_ids.append(value.decode() if isinstance(value, (bytes, bytearray)) else value)
    if not tool_call_ids:
        return []
    keys = [make_tool_result_key(tool_call_id) for tool_call_id in tool_call_ids]
    payloads = client.mget(keys)
    client.delete(*keys)
    results: List[Dict[str, Any]] = []
    for tool_call_id, payload in zip(tool_call_ids, payloads):
        if not payload:
            continue
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            logger.exception(
                "Failed to decode pending tool result run=%s tool_call_id=%s",
                run_id,
                tool_call_id,
            )
            continue
        decoded["tool_call_id"] = tool_call_id
        results.append(decoded)
    logger.info(
        "popped %d pending tool results for run=%s tool_call_ids=%s results=%s ts=%s",
        len(results),
        run_id,
        tool_call_ids,
        results,
        timezone.now().isoformat(),
    )
    return results


def peek_pending_tool_results(run_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    client = _request_redis_client("peek_pending_tool_results")
    list_key = make_run_pending_list_key(run_id)
    ids = client.lrange(list_key, 0, limit - 1)
    if not ids:
        return []
    keys = [make_tool_result_key(tool_call_id.decode()) for tool_call_id in ids]
    payloads = client.mget(keys)
    results: List[Dict[str, Any]] = []
    for tool_call_id, payload in zip(ids, payloads):
        decoded_payload = None
        if payload:
            try:
                decoded_payload = json.loads(payload)
            except json.JSONDecodeError:
                logger.exception(
                    "Failed to decode pending tool resu lt peek run=%s tool_call_id=%s",
                    run_id,
                    tool_call_id,
                )
        if decoded_payload:
            decoded_payload["tool_call_id"] = tool_call_id.decode()
            results.append(decoded_payload)
    return results
