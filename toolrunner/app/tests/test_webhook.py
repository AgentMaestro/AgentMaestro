import hmac
import hashlib
import json
import time

from fastapi.testclient import TestClient

from toolrunner.app import app
from toolrunner.app.config import SECRET

client = TestClient(app)


def request_signature(body: bytes, timestamp: str | None = None) -> tuple[str, str]:
    ts = timestamp or str(int(time.time()))
    message = ts.encode("utf-8") + b"." + body
    return ts, hmac.new(SECRET, message, hashlib.sha256).hexdigest()


def test_webhook_missing_event():
    payload = {"run_id": "webhook", "tool": "webhook", "payload": {}}
    raw = json.dumps(payload).encode("utf-8")
    timestamp, signature = request_signature(raw)
    response = client.post(
        "/v1/webhook",
        data=raw,
        headers={
            "X-AM-Signature": signature,
            "X-AM-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
