from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
import uuid
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_toolrunner_config() -> types.ModuleType:
    config_path = REPO_ROOT / "toolrunner" / "app" / "config.py"
    spec = importlib.util.spec_from_file_location("toolrunner_script_config", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load ToolRunner config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


toolrunner_config = _load_toolrunner_config()


DEFAULT_RESULT_DIR = REPO_ROOT / "smoke" / "webhook"
DEFAULT_EVENT = "smoke-webhook"
DEFAULT_RUN_ID = "smoke-webhook-run"
DEFAULT_ENDPOINT = "/v1/run/tool/webhook"


def _base_url() -> str:
    configured = toolrunner_config._env_value("TOOLRUNNER_BASE_URL").strip()
    return (configured or "http://127.0.0.1:8001").rstrip("/")


def _result_path(token: str, result_dir: str | None) -> Path:
    base = Path(result_dir) if result_dir else DEFAULT_RESULT_DIR
    if not base.is_absolute():
        base = (REPO_ROOT / base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{token}.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _signed_request_body(event: str, run_id: str, payload: dict | None) -> tuple[bytes, str, str]:
    body_obj = {
        "event": event,
        "run_id": run_id,
        "payload": payload or {},
    }
    body = json.dumps(body_obj, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    message = timestamp.encode("utf-8") + b"." + body
    signature = hmac.new(toolrunner_config.SECRET, message, hashlib.sha256).hexdigest()
    return body, timestamp, signature


def _run_smoke(*, token: str, result_path: Path, event: str, run_id: str, endpoint: str, payload: dict | None) -> int:
    body, timestamp, signature = _signed_request_body(event, run_id, payload)
    url = f"{_base_url()}{endpoint}"
    started_at = time.time()
    pending = {
        "token": token,
        "state": "running",
        "passed": False,
        "url": url,
        "result_path": str(result_path),
        "started_at": started_at,
        "request": {
            "event": event,
            "run_id": run_id,
            "payload": payload or {},
            "timestamp": timestamp,
        },
    }
    _write_json(result_path, pending)
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-AM-Timestamp": timestamp,
            "X-AM-Signature": signature,
        },
        method="POST",
    )
    final = dict(pending)
    final["finished_at"] = None
    final["duration_ms"] = None
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw_body)
            final["response"] = {
                "status": response.status,
                "headers": dict(response.headers.items()),
                "body": raw_body,
                "parsed": parsed,
            }
            checks = {
                "ok": parsed.get("ok") is True,
                "accepted": parsed.get("result", {}).get("accepted") is True,
                "event": parsed.get("result", {}).get("event") == event,
                "run_id": parsed.get("result", {}).get("run_id") == run_id,
            }
            final["checks"] = checks
            final["passed"] = all(checks.values())
            final["state"] = "completed"
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        final["response"] = {
            "status": exc.code,
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "body": raw_body,
        }
        final["error"] = f"HTTPError({exc.code})"
        final["state"] = "completed"
        final["passed"] = False
    except Exception as exc:
        final["error"] = repr(exc)
        final["state"] = "completed"
        final["passed"] = False
    finally:
        finished_at = time.time()
        final["finished_at"] = finished_at
        final["duration_ms"] = int(round((finished_at - started_at) * 1000))
        _write_json(result_path, final)
    return 0 if final.get("passed") else 1


def _start_worker(*, token: str, result_path: Path, event: str, run_id: str, endpoint: str, payload: dict | None) -> int:
    _write_json(
        result_path,
        {
            "token": token,
            "state": "scheduled",
            "passed": False,
            "result_path": str(result_path),
            "event": event,
            "run_id": run_id,
        },
    )
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "worker",
        "--token",
        token,
        "--result-path",
        str(result_path),
        "--event",
        event,
        "--run-id",
        run_id,
        "--endpoint",
        endpoint,
        "--payload-json",
        json.dumps(payload or {}, separators=(",", ":")),
    ]
    kwargs: dict[str, object] = {
        "cwd": str(REPO_ROOT),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    print(
        json.dumps(
            {
                "started": True,
                "token": token,
                "result_path": str(result_path),
            }
        )
    )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local ToolRunner webhook smoke harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--token", default="")
    common.add_argument("--result-dir", default="")
    common.add_argument("--result-path", default="")
    common.add_argument("--event", default=DEFAULT_EVENT)
    common.add_argument("--run-id", default=DEFAULT_RUN_ID)
    common.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    common.add_argument("--payload-json", default="{}")

    subparsers.add_parser("start", parents=[common])
    subparsers.add_parser("worker", parents=[common])
    subparsers.add_parser("status", parents=[common])
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    token = args.token or uuid.uuid4().hex
    payload = json.loads(args.payload_json or "{}")
    result_path = Path(args.result_path) if args.result_path else _result_path(token, args.result_dir or None)

    if args.command == "start":
        return _start_worker(
            token=token,
            result_path=result_path,
            event=args.event,
            run_id=args.run_id,
            endpoint=args.endpoint,
            payload=payload,
        )
    if args.command == "worker":
        return _run_smoke(
            token=token,
            result_path=result_path,
            event=args.event,
            run_id=args.run_id,
            endpoint=args.endpoint,
            payload=payload,
        )
    if args.command == "status":
        if not result_path.exists():
            print(json.dumps({"token": token, "state": "missing", "result_path": str(result_path)}))
            return 1
        print(result_path.read_text(encoding="utf-8"))
        return 0
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
