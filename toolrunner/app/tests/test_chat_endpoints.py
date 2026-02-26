from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from toolrunner.app.main import app

client = TestClient(app)


def _run_root(run_id: str) -> Path:
    return Path(__file__).resolve().parents[2] / ".agentmaestro" / "runs" / run_id


def test_chat_endpoints_persist_transcript_and_history():
    response = client.post("/v1/runs", json={"slug": "chat-endpoint"})
    run_id = response.json().get("run_id")
    assert run_id

    initial = client.post("/v1/runs/{}/chat".format(run_id), json={"message": "Hello Maestro"})
    assert initial.status_code == 200
    assert initial.json()["ok"] is True

    transcript_path = _run_root(run_id) / "chat" / "transcript.jsonl"
    assert transcript_path.exists()
    assert "Hello Maestro" in transcript_path.read_text(encoding="utf-8")

    client.post("/v1/runs/{}/chat".format(run_id), json={"message": "lock project summary overview"})

    history = client.get(f"/v1/runs/{run_id}/chat/history")
    assert history.json()["ok"] is True
    assert len(history.json()["messages"]) == 4

    paged = client.get(f"/v1/runs/{run_id}/chat/history?since=2")
    assert paged.status_code == 200
    assert len(paged.json()["messages"]) == 2


def test_chat_applies_srs_updates():
    response = client.post("/v1/runs", json={"slug": "chat-srs"})
    run_id = response.json().get("run_id")
    assert run_id

    client.post("/v1/runs/{}/chat".format(run_id), json={"message": "lock project summary final summary"})

    run_root = _run_root(run_id)
    srs_md = run_root / "srs" / "SRS.md"
    lock_file = run_root / "srs" / "SRS.lock.json"
    assert srs_md.exists()
    assert lock_file.exists()

    lock_content = json.loads(lock_file.read_text(encoding="utf-8"))
    assert lock_content.get("locked_sections") and "project_summary" in lock_content["locked_sections"]

    events = run_root / "events.jsonl"
    assert events.exists()
    event_types = [json.loads(line).get("type") for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "CHAT_MESSAGE" in event_types
    assert "SRS_SECTION_LOCKED" in event_types
