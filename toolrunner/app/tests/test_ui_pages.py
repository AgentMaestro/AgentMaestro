from fastapi.testclient import TestClient

from toolrunner.app.main import app

client = TestClient(app)


def test_ui_page_contains_tabs():
    response = client.get("/ui")
    assert response.status_code == 200
    body = response.text
    assert "User" in body
    assert "Maestro" in body
    assert "Apprentice" in body


def test_ui_tabs_markup():
    response = client.get("/ui")
    assert response.status_code == 200
    body = response.text
    assert '<div class="tabs-card"' in body
    assert 'role="tablist"' in body
    assert body.count('role="tab"') >= 3
    assert 'class="glider"' in body
    assert "--glider-left" in body
    assert "--glider-width" in body
    assert 'type="radio"' not in body


def test_ui_js_updates_glider():
    response = client.get("/ui")
    assert response.status_code == 200
    body = response.text
    assert "function updateGlider" in body
    assert "updateGlider();" in body
    assert 'setProperty("--glider-left"' in body
def test_ui_contains_chat_elements():
    response = client.get("/ui/partials/user")
    assert response.status_code == 200
    body = response.text
    assert 'id="chat-messages"' in body
    assert 'id="chat-input"' in body
    assert "SRS Live Preview" in body
