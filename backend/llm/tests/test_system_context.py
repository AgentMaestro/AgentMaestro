from django.contrib.auth import get_user_model
from django.test import TestCase

from agents.models import Agent
from core.models import Workspace
from llm.system_context import build_system_context


class BuildSystemContextTests(TestCase):
    def test_includes_canonical_authenticated_user_identity_for_memory_tools(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="system-context-scott",
            password="x",
            email="scott@example.com",
            first_name="Scott",
            last_name="Kissinger",
        )
        workspace = Workspace.objects.create(name="Context Workspace")
        agent = Agent.objects.create(
            workspace=workspace,
            owner=user,
            created_by=user,
            name="Context Agent",
            soul="Use memory carefully.",
        )

        context = build_system_context(
            agent,
            model_name="gpt-5-codex",
            transport="websocket",
            tool_names=["remember", "search_memory", "spawn_subrun", "schedule_task"],
            authenticated_user=user,
        )

        assert "Capability: Scheduling" in context
        assert "task_type=other_daily_task" in context
        assert "Capability: User Memory Scope" in context
        assert "Capability: Subruns" in context
        assert "Canonical user id:" in context
        assert "Canonical auth username: system-context-scott" in context
        assert "Display name: Scott Kissinger" in context
        assert "For `remember` or `search_memory` with `scope_type=user`" in context
