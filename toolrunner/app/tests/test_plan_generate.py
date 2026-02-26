from pathlib import Path

from fastapi.testclient import TestClient

from toolrunner.app.main import app
from toolrunner.app.schemas import validate_plan

client = TestClient(app)


def _lock_section(run_id: str, section_id: str, content: str) -> None:
    response = client.post(
        f"/v1/runs/{run_id}/srs/sections/{section_id}",
        json={"content": content, "action": "lock"},
    )
    assert response.status_code == 200


def test_plan_generate_requires_locked_sections():
    response = client.post("/v1/runs", json={"slug": "plan-test"})
    run_id = response.json()["run_id"]

    response = client.post(f"/v1/runs/{run_id}/plan/generate")
    assert response.status_code == 400

    sections = client.get(f"/v1/runs/{run_id}/srs/sections").json()
    assert not (Path(__file__).resolve().parents[2] / ".agentmaestro" / "runs" / run_id / "plans").exists()


def test_plan_generate_persists_schema_valid_plan():
    response = client.post("/v1/runs", json={"slug": "plan-test"})
    run_id = response.json()["run_id"]

    _lock_section(run_id, "project_summary", "Executive summary.\n- Value proposition\n")
    _lock_section(run_id, "goals_non_goals", "Goals and non-goals.\n- Goal 1\n- Non-goal A\n")
    _lock_section(run_id, "functional_requirements", "- FR1\n- FR2\n- FR3\n- FR4\n")
    _lock_section(run_id, "acceptance_criteria", "- AC1\n- AC2\n- AC3\n")

    gen_resp = client.post(f"/v1/runs/{run_id}/plan/generate")
    assert gen_resp.status_code == 200

    plan_resp = client.get(f"/v1/runs/{run_id}/plan")
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    validate_plan(plan)
    assert plan["run_id"] == run_id
    assert plan["milestones"]

    run_root = Path(__file__).resolve().parents[2] / ".agentmaestro" / "runs" / run_id
    plan_path = run_root / "plans" / f"{plan['plan_id']}.json"
    latest_path = run_root / "plans" / "latest.json"
    assert plan_path.exists()
    assert latest_path.exists()

    events = client.get(f"/v1/runs/{run_id}/events?since=0").json()
    assert any(evt["type"] == "PLAN_GENERATED" for evt in events["events"])
