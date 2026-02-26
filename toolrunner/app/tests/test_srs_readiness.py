from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from toolrunner.app.main import app

client = TestClient(app)


def _run_root(run_id: str) -> Path:
    return Path(__file__).resolve().parents[2] / ".agentmaestro" / "runs" / run_id


def _lock_section(run_id: str, section_id: str, content: str) -> None:
    response = client.post(
        f"/v1/runs/{run_id}/srs/sections/{section_id}",
        json={"content": content, "action": "lock"},
    )
    assert response.status_code == 200


def test_srs_readiness_generates_full_score():
    response = client.post("/v1/runs", json={"slug": "readiness-full"})
    run_id = response.json().get("run_id")
    assert run_id

    _lock_section(run_id, "project_summary", "Executive summary.\n- Key customer support\n")
    _lock_section(run_id, "goals_non_goals", "Goals and non-goals explained.\n- Goal 1\n- Non-goal A\n")
    _lock_section(
        run_id,
        "functional_requirements",
        "- FR1\n- FR2\n- FR3\n",
    )
    _lock_section(
        run_id,
        "acceptance_criteria",
        "- AC1\n- AC2\n",
    )
    _lock_section(run_id, "risks_assumptions", "Risk bracket\n")
    _lock_section(run_id, "interfaces", "Interfaces locked.\n")

    readiness_resp = client.get(f"/v1/runs/{run_id}/srs/readiness")
    assert readiness_resp.status_code == 200
    data = readiness_resp.json()
    assert data["score"] == 100
    assert data["missing"] == []
    assert data["warnings"] == []
    assert data["counts"]["functional_requirements_bullets"] == 3
    assert data["counts"]["acceptance_criteria_bullets"] == 2

    readiness_file = _run_root(run_id) / "srs" / "readiness.json"
    assert readiness_file.exists()
    persisted = json.loads(readiness_file.read_text(encoding="utf-8"))
    assert persisted["score"] == 100

    events_file = _run_root(run_id) / "events.jsonl"
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(evt["type"] == "SRS_READINESS_COMPUTED" for evt in events)


def test_srs_readiness_reports_missing_sections():
    response = client.post("/v1/runs", json={"slug": "readiness-missing"})
    run_id = response.json().get("run_id")
    assert run_id

    _lock_section(run_id, "project_summary", "Short summary.")

    readiness_resp = client.get(f"/v1/runs/{run_id}/srs/readiness")
    assert readiness_resp.status_code == 200
    data = readiness_resp.json()
    assert data["score"] == 15
    assert "Goals & Non-Goals is not locked yet." in data["missing"]
    assert data["counts"]["functional_requirements_bullets"] == 0
