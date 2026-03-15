import json
from io import StringIO
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from memory.services import remember


pytestmark = pytest.mark.django_db


def test_run_memory_retention_command_supports_dry_run():
    record = remember(
        "agent",
        "agent-command",
        "episodic",
        "Execution 1 at 08:00 with status ok",
        summary="Executed daily weather report",
        importance=0.20,
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
    )
    record.updated_at = timezone.now() - timedelta(days=40)
    record.last_accessed_at = timezone.now() - timedelta(days=40)
    record.save(update_fields=["updated_at", "last_accessed_at"])

    stdout = StringIO()
    call_command("run_memory_retention", "--dry-run", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["dry_run"] is True
    assert payload["examined"] >= 1
