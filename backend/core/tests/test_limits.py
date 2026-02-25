import pytest

from core.services.limits import LimitExceeded, LimitKey, LIMIT_CONFIGS, QuotaManager


class _SimplePipeline:
    def __init__(self, redis_impl):
        self.redis_impl = redis_impl
        self.commands = []

    def incr(self, key):
        self.commands.append(("incr", key))
        return self

    def expire(self, key, seconds):
        self.commands.append(("expire", key, seconds))
        return self

    def execute(self):
        results = []
        for command in self.commands:
            if command[0] == "incr":
                key = command[1]
                value = self.redis_impl.storage.get(key, 0) + 1
                self.redis_impl.storage[key] = value
                results.append(value)
            elif command[0] == "expire":
                results.append(True)
        self.commands.clear()
        return results


class _SimpleRedis:
    def __init__(self):
        self.storage: dict[str, object] = {}

    def pipeline(self):
        return _SimplePipeline(self)

    def get(self, key):
        value = self.storage.get(key)
        if isinstance(value, int):
            return value
        return None

    def delete(self, key):
        self.storage.pop(key, None)

    def expire(self, key, seconds):
        return True

    def scard(self, key):
        value = self.storage.get(key)
        if isinstance(value, set):
            return len(value)
        return 0

    def sadd(self, key, member):
        value = self.storage.get(key)
        if value is None or not isinstance(value, set):
            value = set()
        added = 0
        if member not in value:
            value.add(member)
            added = 1
        self.storage[key] = value
        return added

    def srem(self, key, member):
        value = self.storage.get(key)
        if isinstance(value, set) and member in value:
            value.remove(member)
            self.storage[key] = value
            return 1
        return 0


@pytest.fixture
def quota_manager():
    return QuotaManager(redis_client=_SimpleRedis())


def test_run_creation_limit_enforced(quota_manager):
    config = LIMIT_CONFIGS[LimitKey.RUN_CREATION]
    workspace_id = "ws-limit"
    for _ in range(config.max_requests):
        quota_manager.record_request(workspace_id, LimitKey.RUN_CREATION)
    with pytest.raises(LimitExceeded) as exc:
        quota_manager.record_request(workspace_id, LimitKey.RUN_CREATION)
    assert exc.value.limit.key == LimitKey.RUN_CREATION


def test_current_usage_and_reset(quota_manager):
    workspace_id = "usage-ws"
    quota_manager.record_request(workspace_id, LimitKey.SNAPSHOT)
    assert quota_manager.current_usage(workspace_id, LimitKey.SNAPSHOT) == 1
    quota_manager.reset(workspace_id, LimitKey.SNAPSHOT)
    assert quota_manager.current_usage(workspace_id, LimitKey.SNAPSHOT) == 0


def test_unknown_limit_key_raises(quota_manager):
    with pytest.raises(KeyError):
        quota_manager.record_request("any", "unknown_limit")


def test_concurrency_tracking(quota_manager):
    limit = LIMIT_CONFIGS[LimitKey.CONCURRENT_PARENT_RUNS]
    workspace_id = "ws-concurrency"
    members = [f"run-{i}" for i in range(limit.max_concurrency)]
    for member in members:
        quota_manager.acquire_concurrency(workspace_id, LimitKey.CONCURRENT_PARENT_RUNS, member)
    with pytest.raises(LimitExceeded):
        quota_manager.acquire_concurrency(workspace_id, LimitKey.CONCURRENT_PARENT_RUNS, "overflow")
    quota_manager.release_concurrency(workspace_id, LimitKey.CONCURRENT_PARENT_RUNS, members[0])
    quota_manager.acquire_concurrency(workspace_id, LimitKey.CONCURRENT_PARENT_RUNS, "new-run")


def test_run_slot_acquisition_and_release(quota_manager):
    workspace_id = "slot-ws"
    parent_limit = LIMIT_CONFIGS[LimitKey.CONCURRENT_PARENT_RUNS].max_concurrency
    parents = [f"parent-{i}" for i in range(parent_limit)]
    for parent in parents:
        quota_manager.acquire_run_slots(workspace_id, parent, include_parent=True)
    with pytest.raises(LimitExceeded):
        quota_manager.acquire_run_slots(workspace_id, "parent-extra", include_parent=True)
    quota_manager.release_run_slots(workspace_id, parents[0], include_parent=True)
    quota_manager.acquire_run_slots(workspace_id, "parent-extra", include_parent=True)
    for parent in parents[1:]:
        quota_manager.release_run_slots(workspace_id, parent, include_parent=True)
    quota_manager.release_run_slots(workspace_id, "parent-extra", include_parent=True)
