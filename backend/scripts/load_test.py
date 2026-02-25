import argparse
import json
import os
import statistics
import threading
import time
import concurrent.futures
import sys
from dataclasses import dataclass, field
from typing import Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "agentmaestro.settings.dev")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership

FINAL_STATUSES = {
    "COMPLETED",
    "FAILED",
    "CANCELED",
}


@dataclass
class LoadMetrics:
    creation_latencies: List[float] = field(default_factory=list)
    spawn_latencies: List[float] = field(default_factory=list)
    poll_latencies: List[float] = field(default_factory=list)
    completion_times: List[float] = field(default_factory=list)
    ticks_per_run: List[int] = field(default_factory=list)
    events_per_run: List[int] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record_creation(self, latency: float):
        with self.lock:
            self.creation_latencies.append(latency)

    def record_spawn(self, latency: float):
        with self.lock:
            self.spawn_latencies.append(latency)

    def record_poll(self, latency: float):
        with self.lock:
            self.poll_latencies.append(latency)

    def record_completion(self, duration: float, tick_count: int, event_count: int):
        with self.lock:
            self.completion_times.append(duration)
            self.ticks_per_run.append(tick_count)
            self.events_per_run.append(event_count)

    def record_error(self, message: str):
        with self.lock:
            self.errors.append(message)

    def summarize(self) -> Dict[str, float]:
        summary: Dict[str, float] = {}
        if self.creation_latencies:
            summary["avg_run_creation_ms"] = statistics.mean(self.creation_latencies) * 1000.0
        if self.spawn_latencies:
            summary["avg_spawn_ms"] = statistics.mean(self.spawn_latencies) * 1000.0
        if self.poll_latencies:
            summary["avg_poll_ms"] = statistics.mean(self.poll_latencies) * 1000.0
        if self.completion_times:
            summary["avg_completion_s"] = statistics.mean(self.completion_times)
        if self.ticks_per_run:
            summary["avg_ticks_per_run"] = statistics.mean(self.ticks_per_run)
        if self.events_per_run:
            summary["avg_events_per_run"] = statistics.mean(self.events_per_run)
        summary["errors"] = len(self.errors)
        return summary


def setup_test_workspace():
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="load-test-user",
        defaults={"is_active": True},
    )
    workspace, _ = Workspace.objects.get_or_create(
        name="Load Test Workspace",
        defaults={"is_active": True},
    )
    membership, _ = WorkspaceMembership.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.OPERATOR},
    )
    agent, _ = Agent.objects.get_or_create(
        workspace=workspace,
        name="Load Test Agent",
        defaults={"system_prompt": "Load test agent."},
    )
    membership.role = WorkspaceMembership.Role.OPERATOR
    membership.save(update_fields=["role"])
    return {
        "user": user,
        "workspace": workspace,
        "agent": agent,
    }


def describe_response(response):
    hint = ""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        hint = payload.get("error") or payload.get("detail") or ""
    if not hint:
        hint = response.content.decode("utf-8", errors="ignore").strip()
    return hint or f"HTTP {response.status_code}"


def build_client(user):
    client = Client()
    client.force_login(user)
    return client


def dispatch_run_creation(client, workspace_id, agent_id, payload_overrides=None):
    payload = {
        "workspace_id": str(workspace_id),
        "agent_id": str(agent_id),
        "input_text": f"load-run-{time.time():.3f}",
    }
    if payload_overrides:
        payload.update(payload_overrides)
    start = time.perf_counter()
    response = client.post(
        "/api/runs/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    latency = time.perf_counter() - start
    if response.status_code != 200:
        response._load_test_note = describe_response(response)
    return response, latency


def dispatch_spawn(client, run_id, options):
    payload = {"input_text": options.get("input_text", "child load run")}
    if options:
        payload["options"] = {
            key: options[key] for key in options if key != "input_text"
        }
    start = time.perf_counter()
    response = client.post(
        f"/api/runs/{run_id}/spawn_subrun/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    latency = time.perf_counter() - start
    if response.status_code != 200:
        response._load_test_note = describe_response(response)
    return response, latency


def monitor_run_completion(client, run_id, poll_interval, timeout, metrics: LoadMetrics):
    start = time.perf_counter()
    deadline = start + timeout
    last_seq = 0
    total_events = 0
    total_ticks = 0
    status = "UNKNOWN"
    while time.perf_counter() < deadline:
        poll_start = time.perf_counter()
        response = client.get(
            f"/api/runs/{run_id}/snapshot/",
            {"since_seq": last_seq},
        )
        metrics.record_poll(time.perf_counter() - poll_start)
        if response.status_code != 200:
            metrics.record_error(
                f"snapshot failed for {run_id}: {response.status_code}"
            )
            break
        snapshot = response.json()
        run_record = snapshot.get("run", {})
        status = run_record.get("status", status)
        events = snapshot.get("events_since_seq", [])
        if events:
            last_seq = max(last_seq, max(evt.get("seq", 0) for evt in events))
            total_events += len(events)
            total_ticks += sum(
                1
                for evt in events
                if evt.get("event") == "step_created" or evt.get("event_type") == "step_created"
            )
        if status in FINAL_STATUSES:
            break
        time.sleep(poll_interval)
    duration = time.perf_counter() - start
    metrics.record_completion(duration, total_ticks, total_events)
    return status, duration, total_ticks, total_events


def simulate_run(metrics, context, args):
    client = build_client(context["user"])
    run_data = {}
    run_status = "UNKNOWN"
    response, creation_latency = dispatch_run_creation(
        client,
        context["workspace"].id,
        context["agent"].id,
    )
    metrics.record_creation(creation_latency)
    if response.status_code != 200:
        note = getattr(response, "_load_test_note", response.status_code)
        metrics.record_error(f"run creation failed: {note}")
        return
    run_data = response.json()
    run_id = run_data["run_id"]
    if args.children > 0:
        for child in range(args.children):
            options = {
                "input_text": f"spawn-child-{run_id}-{child}",
                "join_policy": "WAIT_ANY",
            }
            spawn_resp, spawn_latency = dispatch_spawn(client, run_id, options)
            metrics.record_spawn(spawn_latency)
            if spawn_resp.status_code != 200:
                note = getattr(spawn_resp, "_load_test_note", spawn_resp.status_code)
                metrics.record_error(f"spawn failed for {run_id}: {note}")
    monitor_run_completion(
        client,
        run_id,
        poll_interval=args.poll_interval,
        timeout=args.poll_timeout,
        metrics=metrics,
    )


def run_sequential(metrics, context, args):
    for idx in range(args.runs):
        simulate_run(metrics, context, args)
        if args.mode == "soak" and args.soak_interval > 0:
            time.sleep(args.soak_interval)


def run_burst(metrics, context, args):
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.burst_size) as executor:
        futures = [
            executor.submit(simulate_run, metrics, context, args) for _ in range(args.runs)
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def summarize(metrics: LoadMetrics, args):
    summary = metrics.summarize()
    print("\nLoad test summary")
    print(f"Mode: {args.mode}")
    print(f"Runs executed: {args.runs}")
    print(f"Children per run: {args.children}")
    print(f"Poll interval: {args.poll_interval}s")
    for key, value in sorted(summary.items()):
        if isinstance(value, float):
            unit = "ms" if "ms" in key else "s"
            print(f"  {key}: {value:.2f} {unit}")
        else:
            print(f"  {key}: {value}")
    if metrics.errors:
        print("\nErrors encountered:")
        for err in metrics.errors:
            print(f"  - {err}")


def parse_args():
    parser = argparse.ArgumentParser(description="AgentMaestro load test harness")
    parser.add_argument("--mode", choices=["soak", "burst", "smoke"], default="smoke")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--children", type=int, default=1)
    parser.add_argument("--burst-size", type=int, default=10)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--poll-timeout", type=float, default=20.0)
    parser.add_argument("--soak-interval", type=float, default=0.0)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.mode == "soak" and args.runs < 100:
        args.runs = 100
    if args.mode == "burst" and args.runs < 50:
        args.runs = 50
    context = setup_test_workspace()
    metrics = LoadMetrics()
    start = time.perf_counter()
    if args.mode == "burst":
        run_burst(metrics, context, args)
    else:
        run_sequential(metrics, context, args)
    total_duration = time.perf_counter() - start
    print(f"\nTotal load test duration: {total_duration:.2f}s")
    summarize(metrics, args)


if __name__ == "__main__":
    main()
