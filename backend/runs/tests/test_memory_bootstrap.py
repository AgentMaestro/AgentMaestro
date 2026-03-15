from django.contrib.auth import get_user_model
from django.test import TestCase

from agents.models import Agent
from core.models import Workspace
from memory.services import remember
from runs.models import AgentRun, RunEvent
from runs.services.memory_bootstrap import MEMORY_BOOTSTRAP_EVENT, bootstrap_memory_for_first_turn


class MemoryBootstrapTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="memory-bootstrap", password="x")
        self.workspace = Workspace.objects.create(name="Memory Bootstrap Workspace")
        self.agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="Memory Bootstrap Agent",
            soul="Use memory carefully.",
        )
        self.run = AgentRun.objects.create(
            workspace=self.workspace,
            agent=self.agent,
            started_by=self.user,
            status=AgentRun.Status.RUNNING,
            channel=AgentRun.Channel.DASHBOARD,
            input_text="",
        )

    def test_bootstrap_applies_once_and_updates_run_memory(self):
        remember(
            scope_type="sandbox",
            scope_id=str(self.workspace.id),
            memory_kind="semantic",
            content="The preferred restart order is backend, worker, then beat.",
            summary="Preferred restart order",
            tags=["ops"],
        )
        remember(
            scope_type="agent",
            scope_id=str(self.agent.id),
            memory_kind="procedural",
            content="For local Telegram testing, prefer polling over webhook.",
            summary="Telegram local testing guidance",
            tags=["telegram"],
        )

        result = bootstrap_memory_for_first_turn(
            self.run,
            self.agent,
            "Please help me validate the restart order and local Telegram setup.",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.applied)
        self.assertGreaterEqual(result.result_count, 1)
        self.assertIn("Relevant prior memory for this run:", result.summary_text)

        self.run.refresh_from_db()
        self.assertIn("memory bootstrap applied for query", self.run.memory.notes)
        self.assertEqual(
            1,
            RunEvent.objects.filter(run=self.run, event_type=MEMORY_BOOTSTRAP_EVENT).count(),
        )

        second = bootstrap_memory_for_first_turn(
            self.run,
            self.agent,
            "Please help me validate the restart order and local Telegram setup.",
        )
        self.assertIsNone(second)
        self.assertEqual(
            1,
            RunEvent.objects.filter(run=self.run, event_type=MEMORY_BOOTSTRAP_EVENT).count(),
        )

    def test_bootstrap_skips_non_substantive_turns(self):
        result = bootstrap_memory_for_first_turn(self.run, self.agent, "hi")
        self.assertIsNone(result)
        self.assertFalse(
            RunEvent.objects.filter(run=self.run, event_type=MEMORY_BOOTSTRAP_EVENT).exists()
        )
