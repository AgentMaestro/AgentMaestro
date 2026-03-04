from pathlib import Path

from fastapi.testclient import TestClient

from toolrunner.app.main import app
from toolrunner.app.schemas import validate_plan

client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_trivial_trial_seeds_srs_and_plan_without_start():
    response = client.post(
        "/v1/trials/trivial",
        json={"slug": "trivial-sample", "start": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["plan_generated"] is True
    assert payload["run_started"] is False
    run_id = payload["run_id"]

    run_root = REPO_ROOT / ".agentmaestro" / "runs" / run_id
    assert (run_root / "srs" / "SRS.md").exists()
    assert (run_root / "srs" / "SRS.lock.json").exists()
    assert (run_root / "srs" / "readiness.json").exists()
    latest_plan = run_root / "plans" / "latest.json"
    assert latest_plan.exists()

    plan_resp = client.get(f"/v1/runs/{run_id}/plan")
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    validate_plan(plan)

    seeded_sections = payload["seeded_sections"]
    assert {"project_summary", "goals_non_goals", "functional_requirements", "interfaces", "acceptance_criteria", "risks_assumptions"}.issubset(
        seeded_sections
    )

    srs_body = (run_root / "srs" / "SRS.md").read_text(encoding="utf-8")
    assert "todo" in srs_body.lower()

    events = client.get(f"/v1/runs/{run_id}/events?since=0").json()["events"]
    event_types = {event["type"] for event in events}
    assert "TRIAL_STARTED" in event_types
    assert "TRIAL_SRS_SEEDED" in event_types
    assert "PLAN_GENERATED" in event_types
    assert "TRIAL_PLAN_GENERATED" in event_types
    assert "TRIAL_RUN_STARTED" not in event_types
