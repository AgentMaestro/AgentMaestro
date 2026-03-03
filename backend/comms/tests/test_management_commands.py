from io import StringIO

import httpx
import pytest
from django.core.management import call_command


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.django_db
def test_test_telegram_token_command(monkeypatch):
    def fake_get(url, timeout):
        return _FakeResponse(
            {
                "ok": True,
                "result": {
                    "id": 8722176023,
                    "first_name": "Agent Maestro",
                    "username": "agent_maestro_bot",
                    "is_bot": True,
                },
            }
        )

    monkeypatch.setattr("httpx.get", fake_get)
    output = StringIO()
    call_command("test_telegram_token", "--token", "fake-token", stdout=output)
    value = output.getvalue()
    assert "Telegram token validated." in value
    assert "bot_id=8722176023" in value
    assert "username=agent_maestro_bot" in value


@pytest.mark.django_db
def test_send_telegram_hi_command(monkeypatch):
    def fake_post(url, json, timeout):
        return _FakeResponse({"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr("httpx.post", fake_post)
    output = StringIO()
    call_command("send_telegram_hi", "--token", "fake-token", "--chat-id", "123", stdout=output)
    assert "Sent 'hi' to chat 123" in output.getvalue()
