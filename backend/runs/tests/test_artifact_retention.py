from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace
from runs.models import AgentRun, Artifact
from runs.services.artifacts import purge_consumed_artifacts, store_run_artifact


class ArtifactRetentionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user("artifact-retention", password="irrelevant")
        self.workspace = Workspace.objects.create(name="artifact-retention")
        self.agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="RetentionAgent",
            slug="retention-agent",
        )
        self.run = AgentRun.objects.create(
            workspace=self.workspace,
            agent=self.agent,
            started_by=self.user,
            status=AgentRun.Status.RUNNING,
            channel=AgentRun.Channel.DASHBOARD,
            started_at=timezone.now(),
        )

    def test_purge_consumed_artifact_removes_row_and_files(self):
        with tempfile.TemporaryDirectory() as temp_root:
            with override_settings(RUN_ARTIFACT_ROOT=Path(temp_root)):
                uploaded_file = SimpleUploadedFile("notes.txt", b"hello world", content_type="text/plain")
                artifact = store_run_artifact(self.run, uploaded_file)
                old_consumed_at = timezone.now() - timedelta(days=31)
                Artifact.objects.filter(id=artifact.id).update(
                    metadata={
                        **artifact.metadata,
                        "consumed_at": old_consumed_at.isoformat(),
                    },
                    updated_at=old_consumed_at,
                )

                result = purge_consumed_artifacts(older_than_days=30)

                self.assertEqual(1, result["inspected"])
                self.assertEqual(1, result["deleted"])
                self.assertEqual(0, result["skipped"])
                self.assertFalse(Artifact.objects.filter(id=artifact.id).exists())

                storage_path = Path(artifact.storage_path)
                self.assertFalse(storage_path.exists())
                self.assertFalse(storage_path.parent.exists())
                self.assertFalse(storage_path.parent.parent.exists())
                self.assertTrue(Path(temp_root).exists())
