from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import redis
from django.conf import settings


class LimitType:
    RATE = "rate"
    CONCURRENCY = "concurrency"


class LimitKey:
    RUN_CREATION = "run_creation"
    SPAWN_SUBRUN = "spawn_subrun"
    SNAPSHOT = "snapshot"
    RUN_TICK = "run_tick"
    CONCURRENT_PARENT_RUNS = "concurrent_parent_runs"
    CONCURRENT_TOTAL_RUNS = "concurrent_total_runs"
    CONCURRENT_TOOL_CALLS_WS = "concurrent_tool_calls_workspace"
    CONCURRENT_TOOL_CALLS_RUN = "concurrent_tool_calls_per_run"
    WS_CONNECTIONS_WORKSPACE = "ws_connections_workspace"
    WS_CONNECTIONS_USER = "ws_connections_user"


@dataclass(frozen=True)
class LimitConfig:
    key: str
    name: str
    limit_type: str
    requests_per_second: float = 0.0
    window_seconds: int = 1
    concurrency_limit: int = 0
    description: str = ""

    @property
    def max_requests(self) -> int:
        if self.limit_type != LimitType.RATE:
            return 0
        return max(1, int(self.requests_per_second * self.window_seconds))

    @property
    def max_concurrency(self) -> int:
        if self.limit_type != LimitType.CONCURRENCY:
            return 0
        return max(1, self.concurrency_limit)


LIMIT_CONFIGS: Dict[str, LimitConfig] = {
    LimitKey.RUN_CREATION: LimitConfig(
        key=LimitKey.RUN_CREATION,
        name="run creation (POST /api/runs/)",
        limit_type=LimitType.RATE,
        requests_per_second=10.29,
        window_seconds=1,
        description="Per-workspace rate limit for starting runs (25% of measured stable throughput).",
    ),
    LimitKey.SPAWN_SUBRUN: LimitConfig(
        key=LimitKey.SPAWN_SUBRUN,
        name="spawn subrun (POST /api/runs/<run_id>/spawn_subrun/)",
        limit_type=LimitType.RATE,
        requests_per_second=2.14,
        window_seconds=1,
        description="Rate limit for spawn requests (25% of burst SLO).",
    ),
    LimitKey.SNAPSHOT: LimitConfig(
        key=LimitKey.SNAPSHOT,
        name="snapshot poll (GET /api/runs/<run_id>/snapshot/)",
        limit_type=LimitType.RATE,
        requests_per_second=18.49,
        window_seconds=1,
        description="Snapshot poll cap (25% of stable throughput).",
    ),
    LimitKey.RUN_TICK: LimitConfig(
        key=LimitKey.RUN_TICK,
        name="run tick worker",
        limit_type=LimitType.RATE,
        requests_per_second=41.0,
        window_seconds=1,
        description="Safety-net rate for Celery tick executions per workspace.",
    ),
    LimitKey.CONCURRENT_PARENT_RUNS: LimitConfig(
        key=LimitKey.CONCURRENT_PARENT_RUNS,
        name="concurrent parent runs",
        limit_type=LimitType.CONCURRENCY,
        concurrency_limit=5,
        window_seconds=60,
        description="Max parent runs a workspace can have live at once.",
    ),
    LimitKey.CONCURRENT_TOTAL_RUNS: LimitConfig(
        key=LimitKey.CONCURRENT_TOTAL_RUNS,
        name="concurrent total runs",
        limit_type=LimitType.CONCURRENCY,
        concurrency_limit=12,
        window_seconds=60,
        description="Max total runs (parents + children) per workspace.",
    ),
    LimitKey.CONCURRENT_TOOL_CALLS_WS: LimitConfig(
        key=LimitKey.CONCURRENT_TOOL_CALLS_WS,
        name="concurrent tool calls (workspace)",
        limit_type=LimitType.CONCURRENCY,
        concurrency_limit=6,
        window_seconds=60,
        description="Max pending tool calls (workspace-level).",
    ),
    LimitKey.CONCURRENT_TOOL_CALLS_RUN: LimitConfig(
        key=LimitKey.CONCURRENT_TOOL_CALLS_RUN,
        name="concurrent tool calls (run)",
        limit_type=LimitType.CONCURRENCY,
        concurrency_limit=1,
        window_seconds=60,
        description="Max pending tool calls within a single run.",
    ),
    LimitKey.WS_CONNECTIONS_WORKSPACE: LimitConfig(
        key=LimitKey.WS_CONNECTIONS_WORKSPACE,
        name="WebSocket connections (workspace)",
        limit_type=LimitType.CONCURRENCY,
        concurrency_limit=20,
        window_seconds=60,
        description="Max live WebSocket connections a workspace can open.",
    ),
    LimitKey.WS_CONNECTIONS_USER: LimitConfig(
        key=LimitKey.WS_CONNECTIONS_USER,
        name="WebSocket connections (user)",
        limit_type=LimitType.CONCURRENCY,
        concurrency_limit=5,
        window_seconds=60,
        description="Max live WebSocket connections per user.",
    ),
}


class LimitExceeded(RuntimeError):
    def __init__(self, limit: LimitConfig, current: int):
        super().__init__(f"Limit {limit.name} exceeded ({current}/{limit.max_requests or limit.max_concurrency})")
        self.limit = limit
        self.current = current


class QuotaManager:
    def __init__(self, redis_client: redis.Redis | None = None):
        self.redis = redis_client or redis.Redis.from_url(
            getattr(settings, "CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
        )
        self.namespace = getattr(settings, "AGENTMAESTRO_QUOTA_NAMESPACE", "agentmaestro:quota")

    def _key(self, workspace_id: str, limit_key: str) -> str:
        return f"{self.namespace}:{workspace_id}:{limit_key}"

    def _concurrency_key(self, scope_id: str, limit_key: str) -> str:
        return f"{self.namespace}:concurrent:{scope_id}:{limit_key}"

    def _get_limit(self, limit_key: str) -> LimitConfig:
        if limit_key not in LIMIT_CONFIGS:
            raise KeyError(f"Unknown quota key {limit_key}")
        return LIMIT_CONFIGS[limit_key]

    def record_request(self, workspace_id: str, limit_key: str) -> int:
        limit = self._get_limit(limit_key)
        if limit.limit_type != LimitType.RATE:
            raise ValueError(f"{limit_key} is not a rate limit")
        key = self._key(workspace_id, limit_key)
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, limit.window_seconds)
        count, _ = pipe.execute()
        if count > limit.max_requests:
            if getattr(settings, "AGENTMAESTRO_DISABLE_RATE_LIMITS", False):
                return count
            raise LimitExceeded(limit=limit, current=count)
        return count

    def acquire_run_slots(self, workspace_id: str, run_id: str, include_parent: bool) -> None:
        acquired: list[tuple[str, str, str]] = []
        try:
            self.acquire_concurrency(workspace_id, LimitKey.CONCURRENT_TOTAL_RUNS, run_id)
            acquired.append((workspace_id, LimitKey.CONCURRENT_TOTAL_RUNS, run_id))
            if include_parent:
                self.acquire_concurrency(workspace_id, LimitKey.CONCURRENT_PARENT_RUNS, run_id)
                acquired.append((workspace_id, LimitKey.CONCURRENT_PARENT_RUNS, run_id))
        except LimitExceeded:
            for scope_id, limit_key, member in acquired:
                self.release_concurrency(scope_id, limit_key, member)
            raise

    def release_run_slots(self, workspace_id: str, run_id: str, include_parent: bool) -> None:
        self.release_concurrency(workspace_id, LimitKey.CONCURRENT_TOTAL_RUNS, run_id)
        if include_parent:
            self.release_concurrency(workspace_id, LimitKey.CONCURRENT_PARENT_RUNS, run_id)

    def acquire_concurrency(self, scope_id: str, limit_key: str, member: str) -> int:
        limit = self._get_limit(limit_key)
        if limit.limit_type != LimitType.CONCURRENCY:
            raise ValueError(f"{limit_key} is not a concurrency limit")
        key = self._concurrency_key(scope_id, limit_key)
        current = self.redis.scard(key)
        if current >= limit.max_concurrency:
            raise LimitExceeded(limit=limit, current=current)
        added = self.redis.sadd(key, member)
        if added:
            self.redis.expire(key, limit.window_seconds)
        return self.redis.scard(key)

    def release_concurrency(self, scope_id: str, limit_key: str, member: str) -> int:
        limit = self._get_limit(limit_key)
        if limit.limit_type != LimitType.CONCURRENCY:
            raise ValueError(f"{limit_key} is not a concurrency limit")
        key = self._concurrency_key(scope_id, limit_key)
        self.redis.srem(key, member)
        return self.redis.scard(key)

    def current_usage(self, workspace_id: str, limit_key: str) -> int:
        key = self._key(workspace_id, limit_key)
        value = self.redis.get(key)
        return int(value or 0)

    def reset(self, workspace_id: str, limit_key: str) -> None:
        key = self._key(workspace_id, limit_key)
        self.redis.delete(key)


QUOTA_MANAGER = QuotaManager()
