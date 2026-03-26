from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace
from runs.models import AgentRun, RunEvent
from runs.services.reconstruction import (
    build_provider_input_from_events,
    filter_resumable_events,
    load_resumable_run_events,
    summarize_reconstruction,
)


class ReconstructionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user("reconstruction-user", password="irrelevant")
        self.workspace = Workspace.objects.create(name="run-reconstruction")
        self.agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="ReconAgent",
            slug="recon-agent",
        )
        self.run = AgentRun.objects.create(
            workspace=self.workspace,
            agent=self.agent,
            started_by=self.user,
            status=AgentRun.Status.RUNNING,
            channel=AgentRun.Channel.DASHBOARD,
            started_at=timezone.now(),
        )

    def _create_event(self, event_type: str, payload: dict | None) -> RunEvent:
        seq = RunEvent.objects.filter(run=self.run).count() + 1
        return RunEvent.objects.create(
            run=self.run,
            seq=seq,
            event_type=event_type,
            payload=payload or {},
            correlation_id=uuid.uuid4(),
        )

    def test_user_messages_become_provider_items(self):
        self._create_event("chat_message", {"role": "user", "text": "hello"})
        events = load_resumable_run_events(str(self.run.id))
        filtered = filter_resumable_events(events)
        items = build_provider_input_from_events(filtered)
        self.assertEqual([{"role": "user", "content": "hello"}], items)

    def test_assistant_message_persists(self):
        self._create_event("assistant_message", {"content": "I am thinking", "model": "gpt-5"})
        items = build_provider_input_from_events(filter_resumable_events(load_resumable_run_events(str(self.run.id))))
        self.assertEqual(1, len(items))
        self.assertEqual("assistant", items[0]["role"])
        self.assertEqual("I am thinking", items[0]["content"])
        self.assertEqual("gpt-5", items[0]["model"])

    def test_tool_call_completed_emits_tool_item(self):
        payload = {
            "tool_call_id": str(uuid.uuid4()),
            "tool_name": "repo_tree",
            "result": {"root": "C:\\test"},
            "provider_call_id": "call123",
        }
        self._create_event("tool_call_completed", payload)
        items = build_provider_input_from_events(
            filter_resumable_events(load_resumable_run_events(str(self.run.id)))
        )
        self.assertEqual(1, len(items))
        tool_item = items[0]
        self.assertEqual("tool", tool_item["role"])
        decoded = json.loads(tool_item["content"])
        self.assertEqual({"root": "C:\\test"}, decoded)
        self.assertEqual(payload["tool_call_id"], tool_item["tool_call_id"])

    def test_remote_ops_message_becomes_system_item(self):
        self._create_event(
            "remote_ops_message",
            {"text": "A helper note for replay", "kind": "remote_ops"},
        )
        items = build_provider_input_from_events(
            filter_resumable_events(load_resumable_run_events(str(self.run.id)))
        )
        self.assertEqual(1, len(items))
        self.assertEqual("system", items[0]["role"])
        self.assertEqual("A helper note for replay", items[0]["content"])

    def test_artifact_context_becomes_system_item(self):
        self._create_event(
            "artifact_context",
            {
                "artifact_id": str(uuid.uuid4()),
                "artifact_path": r"C:\Dev\AgentMaestro\backend\run_artifacts\run-1\artifact-1\notes.txt",
                "text": "ATTACHED FILE CONTEXT\nFilename: notes.txt\nFull path: C:\\Dev\\AgentMaestro\\backend\\run_artifacts\\run-1\\artifact-1\\notes.txt\nType: FILE\nExtracted content:\nhello world\nInstruction: Use this content directly. Do not infer that only the filename was provided.",
            },
        )
        items = build_provider_input_from_events(
            filter_resumable_events(load_resumable_run_events(str(self.run.id)))
        )
        self.assertEqual(1, len(items))
        self.assertEqual("system", items[0]["role"])
        self.assertIn("hello world", items[0]["content"])
        self.assertIn("Instruction: Use this content directly.", items[0]["content"])

    def test_removed_artifact_context_is_not_replayed(self):
        artifact_id = str(uuid.uuid4())
        self._create_event(
            "artifact_context",
            {
                "artifact_id": artifact_id,
                "artifact_path": r"C:\Dev\AgentMaestro\backend\run_artifacts\run-1\artifact-1\notes.txt",
                "text": "ATTACHED FILE CONTEXT\nFilename: notes.txt\nFull path: C:\\Dev\\AgentMaestro\\backend\\run_artifacts\\run-1\\artifact-1\\notes.txt\nType: FILE\nExtracted content:\nhello world\nInstruction: Use this content directly. Do not infer that only the filename was provided.",
            },
        )
        self._create_event(
            "artifact_removed",
            {"artifact_id": artifact_id, "artifact_name": "notes.txt"},
        )
        items = build_provider_input_from_events(
            filter_resumable_events(load_resumable_run_events(str(self.run.id)))
        )
        self.assertEqual([], items)

    def test_consumed_artifact_context_is_not_replayed(self):
        artifact_id = str(uuid.uuid4())
        self._create_event(
            "artifact_context",
            {
                "artifact_id": artifact_id,
                "artifact_path": r"C:\Dev\AgentMaestro\backend\run_artifacts\run-1\artifact-1\notes.txt",
                "text": "ATTACHED FILE CONTEXT\nFilename: notes.txt\nFull path: C:\\Dev\\AgentMaestro\\backend\\run_artifacts\\run-1\\artifact-1\\notes.txt\nType: FILE\nExtracted content:\nhello world\nInstruction: Use this content directly. Do not infer that only the filename was provided.",
            },
        )
        self._create_event(
            "artifact_consumed",
            {"artifact_ids": [artifact_id], "artifact_count": 1},
        )
        items = build_provider_input_from_events(
            filter_resumable_events(load_resumable_run_events(str(self.run.id)))
        )
        self.assertEqual([], items)

    def test_irrelevant_events_are_filtered(self):
        self._create_event("debug_log", {"foo": "bar"})
        self._create_event("chat_message", {"role": "user", "text": "hi"})
        events = filter_resumable_events(load_resumable_run_events(str(self.run.id)))
        self.assertEqual(1, len(events))

    def test_summarize_reconstruction_includes_counts(self):
        self._create_event("chat_message", {"role": "user", "text": "a"})
        self._create_event("assistant_message", {"role": "assistant", "content": "b"})
        events = filter_resumable_events(load_resumable_run_events(str(self.run.id)))
        items = build_provider_input_from_events(events)
        summary = summarize_reconstruction(events, items)
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["item_count"], 2)
        self.assertIn("chat_message", summary["event_types"])
