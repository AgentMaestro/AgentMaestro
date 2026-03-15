import pytest

from django.conf import settings

from comms.transports.telegram import TelegramAdapter


chat_id_env = settings.TELEGRAM_CHAT_ID
try:
    chat_id_value = int(chat_id_env) if chat_id_env else 999
except ValueError:
    chat_id_value = 999

CHAT_JSON = {
    "id": chat_id_value,
    "first_name": "Test_First_Name",
    "last_name": "Test_Last_Name",
    "type": "private",
}
CHAT_ID_STR = str(chat_id_value)


@pytest.mark.parametrize(
    "update,expected_kind,expected_text,expected_callback",
    [
        (
            {
                "update_id": 101,
                "message": {
                    "message_id": 7,
                    "date": 1680000000,
                    "text": "hello",
                    "chat": CHAT_JSON,
                    "from": {"id": 42, "username": "alice"},
                },
            },
            "message",
            "hello",
            None,
        ),
        (
            {
                "update_id": 102,
                "callback_query": {
                    "id": "cb-1",
                    "data": "approve",
                    "message": {
                        "message_id": 8,
                        "date": 1680000030,
                        "chat": CHAT_JSON,
                        "from": {"id": 9999, "username": "bot_account"},
                    },
                    "from": {"id": 43, "username": "bob"},
                },
            },
            "callback",
            "approve",
            "approve",
        ),
    ],
)
def test_telegram_normalize_update(update, expected_kind, expected_text, expected_callback):
    adapter = TelegramAdapter()
    events = list(adapter.normalize_update(update))
    assert len(events) == 1
    event = events[0]
    assert event.kind == expected_kind
    assert event.update_id == update["update_id"]
    assert event.chat_id == CHAT_ID_STR
    assert event.from_user_id in {"42", "43"}
    assert event.text == expected_text
    assert event.callback_data == expected_callback
