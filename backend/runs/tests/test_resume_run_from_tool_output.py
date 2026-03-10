from __future__ import annotations

from django.test import SimpleTestCase

from runs.tasks import resume_run_from_tool_output


class ResumeRunTests(SimpleTestCase):
    def test_resume_run_from_tool_output_is_noop(self):
        result = resume_run_from_tool_output.run("run-id", "tool-call-id")
        self.assertIsNone(result)
