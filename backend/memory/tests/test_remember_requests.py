import pytest
from django.contrib.auth import get_user_model

from memory.remember_requests import (
    EXPLICIT_USER_REMEMBER_SOURCE_KIND,
    LOCAL_TIME_PREFERENCE_DEDUPE_KEY,
    capture_explicit_user_memory_request,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_capture_explicit_user_memory_request_stores_local_time_preference():
    user = get_user_model().objects.create_user(username="remember-intent", password="x")

    record = capture_explicit_user_memory_request(
        user=user,
        text="Remember that I'm in Ocala Florida, so please report time as of local time Ocala, FL",
        source_ref="chat:test-run",
    )

    assert record is not None
    assert record.scope_type == "user"
    assert record.scope_id == str(user.id)
    assert record.source_kind == EXPLICIT_USER_REMEMBER_SOURCE_KIND
    assert record.source_ref == "chat:test-run"
    assert record.dedupe_key == LOCAL_TIME_PREFERENCE_DEDUPE_KEY
    assert record.pinned is True
    assert "Ocala" in record.content
    assert "local time preference" in record.summary.lower()


def test_capture_explicit_user_memory_request_ignores_plain_chat():
    user = get_user_model().objects.create_user(username="remember-intent-ignore", password="x")

    record = capture_explicit_user_memory_request(
        user=user,
        text="What time is it in Ocala right now?",
        source_ref="chat:test-run",
    )

    assert record is None
