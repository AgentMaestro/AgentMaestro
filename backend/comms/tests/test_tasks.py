import pytest

from comms.models import CommsConversation, CommsMessage, Transport, TransportEndpoint
from comms.tasks import _acquire_redis_lock, telegram_poll_once
from comms.transports.base import NormalizedEvent
from control.models import ControlMessage
from django.conf import settings


class _FakeLock:
    def __init__(self, acquire_result: bool = True) -> None:
        self.acquire_result = acquire_result
        self.released = False

    def acquire(self, blocking: bool = True) -> bool:
        return self.acquire_result

    def release(self) -> None:
        self.released = True


class _FakeClient:
    def __init__(self, lock: _FakeLock) -> None:
        self._lock = lock
        self.closed = False
        self.lock_name = ""
        self.timeout = None

    def lock(self, name: str, timeout: int):
        self.lock_name = name
        self.timeout = timeout
        return self._lock

    def close(self) -> None:
        self.closed = True


def test_acquire_redis_lock_success(monkeypatch):
    fake_lock = _FakeLock(acquire_result=True)
    fake_client = _FakeClient(fake_lock)
    monkeypatch.setattr(settings, "TELEGRAM_POLL_LOCK_REDIS_URL", "redis://example", raising=False)
    monkeypatch.setattr(
        "comms.tasks.redis.from_url", lambda url, decode_responses=True: fake_client
    )
    handle = _acquire_redis_lock(42)
    assert handle is not None
    handle.release()
    assert fake_lock.released
    assert fake_client.closed


def test_acquire_redis_lock_busy(monkeypatch):
    fake_lock = _FakeLock(acquire_result=False)
    fake_client = _FakeClient(fake_lock)
    monkeypatch.setattr(settings, "TELEGRAM_POLL_LOCK_REDIS_URL", "redis://example", raising=False)
    monkeypatch.setattr(
        "comms.tasks.redis.from_url", lambda url, decode_responses=True: fake_client
    )
    handle = _acquire_redis_lock(99)
    assert handle is None
    assert fake_client.closed


@pytest.mark.django_db
def test_telegram_poll_once_ingests_updates(monkeypatch):
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"], "last_update_id": 10},
    )

    raw_updates = [
        {"update_id": 11},
        {"update_id": 12},
    ]

    normalized = [
        NormalizedEvent(
            kind="message",
            update_id=11,
            chat_id="chat-1",
            from_user_id="42",
            from_username="alice",
            text="hello",
            message_id="100",
            callback_data=None,
            callback_query_id=None,
            ts=1,
        ),
        NormalizedEvent(
            kind="message",
            update_id=12,
            chat_id="chat-1",
            from_user_id="42",
            from_username="alice",
            text="world",
            message_id="101",
            callback_data=None,
            callback_query_id=None,
            ts=1,
        ),
    ]

    monkeypatch.setattr(
        "comms.tasks._fetch_updates", lambda endpoint, offset, timeout: (raw_updates, normalized)
    )
    monkeypatch.setattr("comms.tasks._acquire_redis_lock", lambda endpoint_id: None)

    telegram_poll_once(endpoint.id)

    endpoint.refresh_from_db()
    assert endpoint.config.get("last_update_id") == 12
    assert ControlMessage.objects.count() == 2
    assert CommsConversation.objects.filter(external_conversation_id="chat-1").exists()
    assert CommsMessage.objects.filter(conversation__external_conversation_id="chat-1").count() == 2
