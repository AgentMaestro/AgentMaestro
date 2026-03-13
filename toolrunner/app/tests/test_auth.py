import hmac
import hashlib
import json
import time

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from toolrunner.app import app
from toolrunner.app.config import SECRET, TIMESTAMP_SKEW_SECONDS
from toolrunner.app.tools import git_log as git_log_module

client = TestClient(app)


def signer(body: bytes, timestamp: str | None = None) -> tuple[str, str]:
    ts = timestamp or str(int(time.time()))
    message = ts.encode("utf-8") + b"." + body
    return ts, hmac.new(SECRET, message, hashlib.sha256).hexdigest()


def _fixture_payload():
    return {
        "request_id": "req-123",
        "workspace_id": "ws-1",
        "run_id": "run-1",
        "tool_name": "shell_exec",
        "args": {"cmd": ["python", "-c", "print('ok')"], "cwd": ".", "env": {}},
        "policy": {"intent": "echo"},
        "limits": {"timeout_s": 5, "max_output_bytes": 1024},
    }


def test_missing_signature():
    payload = json.dumps(_fixture_payload()).encode("utf-8")
    timestamp, _ = signer(payload)
    response = client.post(
        "/v1/run/tool",
        data=payload,
        headers={"X-AM-Timestamp": timestamp},
    )
    assert response.status_code == 401


def test_invalid_signature():
    payload = json.dumps(_fixture_payload()).encode("utf-8")
    timestamp, _ = signer(payload)
    response = client.post(
        "/v1/run/tool",
        data=payload,
        headers={"X-AM-Timestamp": timestamp, "X-AM-Signature": "bad"},
    )
    assert response.status_code == 401


def test_valid_signature():
    payload = json.dumps(_fixture_payload()).encode("utf-8")
    timestamp, signature = signer(payload)
    response = client.post(
        "/v1/run/tool",
        data=payload,
        headers={
            "X-AM-Signature": signature,
            "X-AM-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-123"
    assert body["status"] in {"COMPLETED", "FAILED"}
    assert "stdout" in body


def test_stale_timestamp_rejected():
    payload = json.dumps(_fixture_payload()).encode("utf-8")
    stale_timestamp = str(int(time.time()) - TIMESTAMP_SKEW_SECONDS - 5)
    timestamp, signature = signer(payload, timestamp=stale_timestamp)
    response = client.post(
        "/v1/run/tool",
        data=payload,
        headers={
            "X-AM-Signature": signature,
            "X-AM-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Stale timestamp"


def test_request_limits_apply_to_git_log_timeout(monkeypatch):
    captured: dict[str, int] = {}

    def fake_run_command(run_dir, run_args):
        captured["timeout_ms"] = run_args.timeout_ms
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 0,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "oid1\u0000Alice\u0000alice@example.com\u00001600000000\u0000Fix bug\n",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(git_log_module, "run_command", fake_run_command)
    payload = {
        "request_id": "req-git-log",
        "workspace_id": "ws-timeout",
        "run_id": "run-timeout",
        "tool_name": "git_log",
        "args": {
            "repo_dir": ".",
            "max_count": 5,
        },
        "limits": {
            "timeout_s": 5,
            "max_output_bytes": 1024,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    timestamp, signature = signer(body)

    response = client.post(
        "/v1/run/tool",
        data=body,
        headers={
            "X-AM-Signature": signature,
            "X-AM-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert captured["timeout_ms"] == 5000

