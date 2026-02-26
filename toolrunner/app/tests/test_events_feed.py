from fastapi.testclient import TestClient

from toolrunner.app.main import app

client = TestClient(app)


def test_events_feed_returns_prompt_event():
    create = client.post("/v1/runs", json={"slug": "events-test"})
    run_id = create.json()["run_id"]
    sections = client.get(f"/v1/runs/{run_id}/srs/sections").json()
    section_id = sections[0]["section_id"]
    client.get(f"/v1/runs/{run_id}/srs/sections/{section_id}/prompt")

    events_resp = client.get(f"/v1/runs/{run_id}/events?since=0")
    assert events_resp.status_code == 200
    payload = events_resp.json()
    assert "events" in payload
    assert payload["events"], "expected at least one event"
    assert any(evt["type"] == "SRS_SECTION_PROMPTED" for evt in payload["events"])
    next_since = payload["next_since"]
    assert next_since >= len(payload["events"])

    second_resp = client.get(f"/v1/runs/{run_id}/events?since={next_since}")
    assert second_resp.status_code == 200
    second_payload = second_resp.json()
    assert "events" in second_payload
    assert second_payload["events"] == []
    assert second_payload["next_since"] == next_since
