import asyncio
import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional, Tuple

import httpx
import redis

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "agentmaestro.settings.dev")

import django  # noqa: E402

django.setup()

from agents.models import Agent  # noqa: E402
from core.models import Workspace, WorkspaceMembership  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from runs.models import AgentRun, AgentStep  # noqa: E402
from runs.services.steps import append_step  # noqa: E402

DEFAULT_DEV_WORKSPACE = "Dev Workspace"
DEFAULT_DEV_AGENT = "Dev Agent"
DEFAULT_DEV_OPERATOR = "dev-operator"
DEV_SYSTEM_PROMPT = "You are the Dev Runner. Follow instructions carefully."

SendFunc = Callable[[], Awaitable[httpx.Response]]
SendFuncFactory = Callable[[], SendFunc]


@dataclass
class BurstResult:
    burst_size: int
    duration: float
    throughput: float
    p95: Optional[float]
    errors: int
    success: int
    queue_start: int
    queue_end: int
    error_messages: List[str] = field(default_factory=list)


def compute_p95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    sorted_values = sorted(values)
    idx = math.ceil(len(sorted_values) * 0.95) - 1
    idx = max(0, min(idx, len(sorted_values) - 1))
    return sorted_values[idx]


def describe_response(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    message = ""
    if isinstance(payload, dict):
        message = payload.get("error") or payload.get("detail") or ""
    if not message:
        message = response.text.strip()
    if not message:
        message = f"HTTP {response.status_code}"
    return message


def ensure_dev_workspace_and_agent():
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=DEFAULT_DEV_OPERATOR, defaults={"is_active": True})
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    if not user.has_usable_password():
        user.set_unusable_password()
        user.save(update_fields=["password"])

    workspace, _ = Workspace.objects.get_or_create(name=DEFAULT_DEV_WORKSPACE, defaults={"is_active": True})
    WorkspaceMembership.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.OWNER},
    )
    agent, _ = Agent.objects.get_or_create(
        workspace=workspace,
        name=DEFAULT_DEV_AGENT,
        defaults={"system_prompt": DEV_SYSTEM_PROMPT},
    )
    return user, workspace, agent


def create_parent_runs(user, workspace, agent) -> Tuple[AgentRun, AgentRun]:
    parent = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        input_text="burst-parent",
    )
    snapshot_run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        input_text="burst-snapshot",
    )
    append_step(
        run_id=str(snapshot_run.id),
        kind=AgentStep.Kind.PLAN,
        payload={"plan": "snapshot baseline"},
    )
    append_step(
        run_id=str(snapshot_run.id),
        kind=AgentStep.Kind.ACTION,
        payload={"action": "noop"},
    )
    return parent, snapshot_run


def start_process(cmd: List[str], name: str) -> subprocess.Popen:
    env = os.environ.copy()
    proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    print(f"[{name}] started pid={proc.pid}")
    return proc


def stop_process(proc: subprocess.Popen, name: str) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print(f"[{name}] stopped")


async def wait_for_server_ready(client: httpx.AsyncClient, timeout: float = 30.0) -> httpx.Response:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = await client.get("/ui/dev/start-run/", timeout=2.0)
            if response.status_code == 200:
                return response
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("Django dev server did not become ready in time")


async def measure_burst(
    burst_size: int,
    send_func: SendFunc,
    redis_conn: redis.Redis,
) -> BurstResult:
    queue_start = redis_conn.llen("celery")

    async def single_request() -> Tuple[float, httpx.Response]:
        start = time.perf_counter()
        response = await send_func()
        elapsed = time.perf_counter() - start
        return elapsed, response

    tasks = [asyncio.create_task(single_request()) for _ in range(burst_size)]
    start = time.perf_counter()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    duration = time.perf_counter() - start
    queue_end = redis_conn.llen("celery")

    errors = 0
    latencies: List[float] = []
    success = 0
    messages: List[str] = []
    for item in results:
        if isinstance(item, Exception):
            errors += 1
            messages.append(str(item))
            continue
        latency, response = item
        latencies.append(latency)
        if response.status_code != 200:
            errors += 1
            messages.append(describe_response(response))
        else:
            success += 1

    throughput = success / duration if duration > 0 else 0.0
    p95 = compute_p95(latencies)
    return BurstResult(
        burst_size=burst_size,
        duration=duration,
        throughput=throughput,
        p95=p95,
        errors=errors,
        success=success,
        queue_start=queue_start,
        queue_end=queue_end,
        error_messages=messages,
    )


async def run_threshold(
    name: str,
    send_factory: SendFuncFactory,
    burst_sizes: List[int],
    redis_conn: redis.Redis,
    target_ms: Optional[float],
) -> Tuple[Optional[BurstResult], BurstResult, Optional[int], Optional[int]]:
    stable: Optional[BurstResult] = None
    failure: Optional[BurstResult] = None
    first_error_burst: Optional[int] = None
    queue_growth_burst: Optional[int] = None

    for burst in burst_sizes:
        result = await measure_burst(burst, send_factory(), redis_conn)
        note = f", note={result.error_messages[0]}" if result.error_messages else ""
        print(
            f"[{name}] burst={burst}, p95={(result.p95 or 0)*1000:.1f}ms, "
            f"errors={result.errors}/{burst}, queue={result.queue_start}->{result.queue_end}{note}"
        )

        if result.queue_end > result.queue_start and queue_growth_burst is None:
            queue_growth_burst = burst
        if result.errors and first_error_burst is None:
            first_error_burst = burst

        sloviolation = False
        if target_ms is not None and result.p95 is not None:
            sloviolation = (result.p95 * 1000) > target_ms
        elif target_ms is not None and result.p95 is None:
            sloviolation = True

        if result.errors == 0 and not sloviolation:
            stable = result
            failure = result
            continue
        failure = result
        break

    if not failure and stable:
        failure = stable

    if not failure:
        raise RuntimeError(f"No measurements for {name}")

    return stable, failure, first_error_burst, queue_growth_burst


def make_start_run_sender(client: httpx.AsyncClient, workspace_id: str, agent_id: str, auth_cookies: dict[str, str]) -> SendFunc:
    async def sender() -> httpx.Response:
        payload = {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "input_text": f"burst run {time.perf_counter()}",
        }
        return await client.post("/api/runs/", json=payload, timeout=10.0, cookies=auth_cookies)

    return sender


def make_spawn_sender(client: httpx.AsyncClient, parent_run_id: str, auth_cookies: dict[str, str]) -> SendFunc:
    async def sender() -> httpx.Response:
        payload = {
            "input_text": "burst subrun",
            "options": {"join_policy": "WAIT_ANY"},
        }
        return await client.post(
            f"/api/runs/{parent_run_id}/spawn_subrun/", json=payload, timeout=10.0, cookies=auth_cookies
        )

    return sender


def make_snapshot_sender(client: httpx.AsyncClient, snapshot_run_id: str, auth_cookies: dict[str, str], since_seq: int = 0) -> SendFunc:
    async def sender() -> httpx.Response:
        params = {"since_seq": since_seq}
        return await client.get(
            f"/api/runs/{snapshot_run_id}/snapshot/", params=params, timeout=10.0, cookies=auth_cookies
        )

    return sender


def compute_quota(rate: float) -> float:
    return rate * 0.25


async def main():
    redis_conn = redis.Redis.from_url("redis://127.0.0.1:6379/0")
    redis_conn.ping()

    server = start_process([sys.executable, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"], "django")
    worker = start_process(
        [sys.executable, "-m", "celery", "-A", "agentmaestro", "worker", "--loglevel=info", "--pool=solo", "--concurrency=1"],
        "celery",
    )
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
            user, workspace, agent = await asyncio.to_thread(ensure_dev_workspace_and_agent)
            ready_response = await wait_for_server_ready(client)
            auth_cookies = {
                "sessionid": ready_response.cookies.get("sessionid"),
                "csrftoken": ready_response.cookies.get("csrftoken"),
            }

            parent_run, snapshot_run = await asyncio.to_thread(
                create_parent_runs, user, workspace, agent
            )
            workspace_id = str(workspace.id)
            agent_id = str(agent.id)
            parent_run_id = str(parent_run.id)
            snapshot_run_id = str(snapshot_run.id)

            test_payload = {
                "workspace_id": str(workspace.id),
                "agent_id": str(agent.id),
                "input_text": "initial test run",
            }
            test_res = await client.post("/api/runs/", json=test_payload, cookies=auth_cookies)
            print("[auth-test] start run status", test_res.status_code, test_res.text)

            create_factory = lambda: make_start_run_sender(
                client, workspace_id, agent_id, auth_cookies
            )
            spawn_factory = lambda: make_spawn_sender(client, parent_run_id, auth_cookies)
            snapshot_factory = lambda: make_snapshot_sender(
                client, snapshot_run_id, auth_cookies
            )

            create_sizes = [1, 2, 3, 5, 10, 20, 40, 80]
            spawn_sizes = [1, 2, 3, 5, 10, 20, 40]
            snapshot_sizes = [5, 10, 20, 40]

            print("\nMeasuring POST /api/runs/ (target p95 <= 300ms)")
            create_stable, create_failure, create_error_burst, create_queue_burst = await run_threshold(
                "create_run",
                create_factory,
                create_sizes,
                redis_conn,
                target_ms=300.0,
            )

            print("\nMeasuring POST /api/runs/<id>/spawn_subrun/ (target p95 <= 200ms)")
            spawn_stable, spawn_failure, spawn_error_burst, spawn_queue_burst = await run_threshold(
                "spawn_subrun",
                spawn_factory,
                spawn_sizes,
                redis_conn,
                target_ms=200.0,
            )

            print("\nMeasuring GET /api/runs/<id>/snapshot/")
            snapshot_stable, snapshot_failure, _, snapshot_queue_burst = await run_threshold(
                "snapshot",
                snapshot_factory,
                snapshot_sizes,
                redis_conn,
                target_ms=None,
            )

    finally:
        stop_process(worker, "celery")
        stop_process(server, "django")

    def describe_result(name: str, stable: Optional[BurstResult], failure: BurstResult, error_burst: Optional[int], queue_burst: Optional[int], target_ms: Optional[float]):
        stable_rate = stable.throughput if stable else 0.0
        stable_p95 = (stable.p95 or 0.0) * 1000 if stable else 0.0
        print(f"\n{name} summary:")
        if stable:
            print(f"  Stable rate: {stable_rate:.2f} req/sec (p95 {stable_p95:.1f}ms) across burst={stable.burst_size}")
        if failure:
            failure_p95 = (failure.p95 or 0.0) * 1000
            print(
                f"  Failure burst: {failure.burst_size} (p95 {failure_p95:.1f}ms, "
                f"errors {failure.errors}/{failure.burst_size})"
            )
            if failure.error_messages:
                print(f"  Error note: {failure.error_messages[0]}")
        if error_burst:
            print(f"  First errors observed at burst={error_burst}")
        if queue_burst:
            print(f"  Redis queue started growing at burst={queue_burst}")
        if target_ms:
            print(f"  Target: p95 <= {target_ms}ms")

    describe_result(
        "POST /api/runs/",
        create_stable,
        create_failure,
        create_error_burst,
        create_queue_burst,
        target_ms=300.0,
    )
    describe_result(
        "POST /api/runs/<id>/spawn_subrun/",
        spawn_stable,
        spawn_failure,
        spawn_error_burst,
        spawn_queue_burst,
        target_ms=200.0,
    )
    describe_result(
        "GET /api/runs/<id>/snapshot/",
        snapshot_stable,
        snapshot_failure,
        None,
        snapshot_queue_burst,
        target_ms=None,
    )

    def print_quota(name: str, result: Optional[BurstResult]):
        rate = result.throughput if result else 0.0
        quota = compute_quota(rate)
        print(f"{name} max stable rate: {rate:.2f} req/sec -> workspace quota (25%) = {quota:.2f} req/sec")

    print("\nSuggested per-workspace quotas (25% of stable rate):")
    print_quota("POST /api/runs/", create_stable)
    print_quota("POST /api/runs/<id>/spawn_subrun/", spawn_stable)
    print_quota("GET /api/runs/<id>/snapshot/", snapshot_stable)


if __name__ == "__main__":
    asyncio.run(main())
