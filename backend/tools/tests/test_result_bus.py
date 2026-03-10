from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

import pytest

from tools.services import result_bus


class FakeRedisPipeline:
    def __init__(self, client: "FakeRedisClient"):
        self.client = client
        self.results = []

    def set(self, key: str, value: bytes):
        self.results.append(self.client.set(key, value))
        return self

    def expire(self, key: str, ttl: int):
        self.results.append(self.client.expire(key, ttl))
        return self

    def rpush(self, key: str, value: str):
        self.results.append(self.client.rpush(key, value))
        return self

    def execute(self) -> List[Any]:
        results = list(self.results)
        self.results.clear()
        return results


class FakeRedisClient:
    def __init__(self):
        self.storage: Dict[str, bytes] = {}
        self.lists: Dict[str, List[str]] = defaultdict(list)

    def pipeline(self):
        return FakeRedisPipeline(self)

    def set(self, key: str, value: bytes):
        self.storage[key] = value
        return True

    def expire(self, key: str, ttl: int):
        return True

    def rpush(self, key: str, value: str):
        self.lists[key].append(value)
        return len(self.lists[key])

    def llen(self, key: str):
        return len(self.lists.get(key, []))

    def lpop(self, key: str):
        lst = self.lists.get(key)
        if not lst:
            return None
        value = lst.pop(0)
        return value.encode()

    def mget(self, keys: List[str]):
        return [self.storage.get(key) for key in keys]

    def delete(self, *keys: str):
        for key in keys:
            self.storage.pop(key, None)
        return len(keys)

    def get(self, key: str):
        return self.storage.get(key)

    def lrange(self, key: str, start: int, end: int):
        lst = self.lists.get(key, [])
        return [value.encode() for value in lst[start : end + 1]]


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = FakeRedisClient()
    monkeypatch.setattr(result_bus, "get_redis_client", lambda: client)
    return client


def test_store_and_pop_pending_tool_result(fake_redis):
    run_id = "run-a"
    tool_call_id = "call-123"
    payload = {"result": {"ok": True}}

    result_bus.store_tool_result(run_id, tool_call_id, payload, ttl_seconds=60)

    popped = result_bus.pop_pending_tool_results(run_id)
    assert len(popped) == 1
    assert popped[0]["tool_call_id"] == tool_call_id
    assert popped[0]["result"] == payload["result"]
    assert fake_redis.llen(result_bus.make_run_pending_list_key(run_id)) == 0


def test_peek_pending_tool_results(fake_redis):
    run_id = "run-b"
    tool_call_id = "call-456"
    payload = {"result": {"ok": False}}

    result_bus.store_tool_result(run_id, tool_call_id, payload, ttl_seconds=60)

    peeked = result_bus.peek_pending_tool_results(run_id)
    assert len(peeked) == 1
    assert peeked[0]["tool_call_id"] == tool_call_id
    assert peeked[0]["result"] == payload["result"]

    # ensure peek does not remove the payload
    popped_again = result_bus.pop_pending_tool_results(run_id)
    assert len(popped_again) == 1

