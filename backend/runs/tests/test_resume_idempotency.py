from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace
from runs.models import AgentRun, RunEvent
from tools.models import ToolCall
from runs.services.resume import (
    handle_provider_tool_calls,
    persist_assistant_response,
)


class ResumeIdempotencyTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user("idemp-user", password="pass")
        self.workspace = Workspace.objects.create(name="idemp-workspace")
        self.agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="IdempAgent",
            slug="idemp-agent",
        )
        self.run = AgentRun.objects.create(
            workspace=self.workspace,
            agent=self.agent,
            started_by=self.user,
            status=AgentRun.Status.RUNNING,
            channel=AgentRun.Channel.DASHBOARD,
            started_at=timezone.now(),
        )

    def test_persist_assistant_response_skips_duplicate(self):
        persist_assistant_response(
            self.run,
            "dup",
            model="m",
            provider_response_id="resp-a",
        )
        persist_assistant_response(
            self.run,
            "dup",
            model="m",
            provider_response_id="resp-a",
        )
        count = RunEvent.objects.filter(run=self.run, event_type="assistant_message").count()
        self.assertEqual(1, count)

    @patch("tools.tasks.execute_tool_call_async.delay")
    def test_handle_provider_tool_calls_skips_duplicate(self, mock_delay):
        mock_delay.return_value = Mock(id="cta")
        tool_call_payload = {"name": "repo_tree", "arguments": {}, "call_id": "provider-123"}
        handle_provider_tool_calls(str(self.run.id), [tool_call_payload], run=self.run)
        handle_provider_tool_calls(str(self.run.id), [tool_call_payload], run=self.run)
        matches = ToolCall.objects.filter(provider_call_id="provider-123")
        self.assertEqual(1, matches.count())
