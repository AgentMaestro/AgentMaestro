from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from toolrunner.app.main import app

client = TestClient(app)


def test_srs_draft_and_lock_persists():
    response = client.post("/v1/runs", json={"slug": "srs-test"})
    run_id = response.json().get("run_id")
    assert run_id

    sections_response = client.get(f"/v1/runs/{run_id}/srs/sections")
    assert sections_response.status_code == 200
    sections = sections_response.json()
    assert sections

    section_id = sections[0]["section_id"]

    prompt_response = client.get(f"/v1/runs/{run_id}/srs/sections/{section_id}/prompt")
    assert prompt_response.status_code == 200
    prompt_data = prompt_response.json()
    title = prompt_data["title"]
    assert "meaning" in prompt_data

    client.post(
        f"/v1/runs/{run_id}/srs/sections/{section_id}",
        json={"content": "Draft content", "action": "draft"},
    )

    client.post(
        f"/v1/runs/{run_id}/srs/sections/{section_id}",
        json={"content": "Final content", "action": "lock"},
    )

    root = Path(__file__).resolve().parents[2]
    run_root = root / ".agentmaestro" / "runs" / run_id
    srs_md_path = run_root / "srs" / "SRS.md"
    assert srs_md_path.exists()
    assert f"## {title}" in srs_md_path.read_text()

    lock_content = json.loads((run_root / "srs" / "SRS.lock.json").read_text())
    locked_sections = lock_content.get("locked_sections", {})
    assert section_id in locked_sections
