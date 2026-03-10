from django.test import TestCase

from runs.services.event_builders import (
    build_assistant_message_payload,
    build_chat_message_payload,
)


class EventBuilderTests(TestCase):
    def test_chat_payload_carries_role_and_text(self):
        payload = build_chat_message_payload("user", "hello")
        self.assertEqual("user", payload["role"])
        self.assertEqual("hello", payload["text"])

    def test_assistant_payload_includes_optional_metadata(self):
        payload = build_assistant_message_payload(
            "reply",
            model="gpt-5",
            provider_response_id="resp123",
            step_index=5,
        )
        self.assertEqual("assistant", payload["role"])
        self.assertEqual("reply", payload["content"])
        self.assertEqual("gpt-5", payload["model"])
        self.assertEqual("resp123", payload["provider_response_id"])
        self.assertEqual(5, payload["step_index"])

    def test_assistant_payload_is_minimal_when_metadata_missing(self):
        payload = build_assistant_message_payload("hi")
        self.assertEqual({"role": "assistant", "content": "hi"}, payload)
