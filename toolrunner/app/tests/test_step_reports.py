from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from toolrunner.app.main import app

client = TestClient(app)


def test_step_report_endpoints_populated():
    response = client.post("/v1/runs", json={"slug": "reports-test"})
    run_id = response.json()["run_id"]

    run_root = Path(__file__).resolve().parents[2] / ".agentmaestro" / "runs" / run_id
    report_path = run_root / "step_reports" / "milestone-1"
    report_path.mkdir(parents=True, exist_ok=True)
    step_file = report_path / "S001.json"
    sample_report = {"step_id": "S001", "milestone_id": "milestone-1", "status": "ok"}
    step_file.write_text(json.dumps(sample_report), encoding="utf-8")

    list_resp = client.get(f"/v1/runs/{run_id}/step_reports")
    assert list_resp.status_code == 200
    entries = list_resp.json()
    assert entries
    assert entries[0]["step_id"] == "S001"
    assert entries[0]["milestone_id"] == "milestone-1"

    fetch_resp = client.get(f"/v1/runs/{run_id}/step_reports/milestone-1/S001")
    assert fetch_resp.status_code == 200
    payload = fetch_resp.json()
    assert payload["step_id"] == "S001"
    assert payload["status"] == "ok"
