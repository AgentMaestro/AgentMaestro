from django.test import SimpleTestCase
from logging_utils import scrub_sensitive_text, scrub_sensitive_value


class ScrubbingTests(SimpleTestCase):
    def test_scrub_sensitive_text_preserves_emails_and_redacts_tokens(self):
        text = (
            "Contact user@example.com with Bearer sk-proj-test1234567890abcdef and token=abc123xyz."
        )

        sanitized = scrub_sensitive_text(text)

        self.assertIn("user@example.com", sanitized)
        self.assertNotIn("sk-proj-test1234567890abcdef", sanitized)
        self.assertNotIn("abc123xyz", sanitized)
        self.assertIn("Bearer [REDACTED]", sanitized)

    def test_scrub_sensitive_value_recurses_through_structures(self):
        payload = {
            "text": "xoxb-123456789012-abcdefghijklmnopqrstuv",
            "nested": {
                "email": "person@example.com",
                "secret": "ghp_123456789012345678901234567890123456",
            },
        }

        sanitized = scrub_sensitive_value(payload)

        self.assertEqual(sanitized["nested"]["email"], "person@example.com")
        self.assertNotIn("xoxb-123456789012-abcdefghijklmnopqrstuv", str(sanitized))
        self.assertNotIn("ghp_123456789012345678901234567890123456", str(sanitized))
