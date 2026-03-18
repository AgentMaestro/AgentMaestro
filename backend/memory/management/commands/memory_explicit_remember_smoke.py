from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord
from memory.remember_requests import (
    EXPLICIT_USER_REMEMBER_SOURCE_KIND,
    LOCAL_TIME_PREFERENCE_DEDUPE_KEY,
    capture_explicit_user_memory_request,
)
from runs.models import AgentRun
from runs.services.memory_bootstrap import bootstrap_memory_for_first_turn


class Command(BaseCommand):
    help = "Smoke test explicit remember capture and future-run bootstrap recall."

    def handle(self, *args, **options):
        User = get_user_model()
        user = User.objects.filter(username="memory_smoke_remember").first()
        if user is None:
            user = User.objects.create_user(username="memory_smoke_remember", password="x")

        workspace, _ = Workspace.objects.get_or_create(name="Memory Explicit Remember Smoke Workspace")
        WorkspaceMembership.objects.get_or_create(
            workspace=workspace,
            user=user,
            defaults={"role": WorkspaceMembership.Role.OWNER},
        )
        agent = Agent.objects.filter(name="Memory Explicit Remember Smoke Agent").first()
        if agent is None:
            agent = Agent.objects.create(
                workspace=workspace,
                owner=user,
                created_by=user,
                name="Memory Explicit Remember Smoke Agent",
                soul="Smoke-test durable remember capture.",
            )

        first_run = AgentRun.objects.create(
            workspace=workspace,
            agent=agent,
            started_by=user,
            status=AgentRun.Status.RUNNING,
            channel=AgentRun.Channel.DASHBOARD,
            input_text="",
        )
        second_run = AgentRun.objects.create(
            workspace=workspace,
            agent=agent,
            started_by=user,
            status=AgentRun.Status.RUNNING,
            channel=AgentRun.Channel.DASHBOARD,
            input_text="",
        )

        record = capture_explicit_user_memory_request(
            user=user,
            text="Remember that I'm in Ocala Florida, so please report time as of local time Ocala, FL",
            source_ref=f"chat:{first_run.id}",
        )
        if record is None:
            raise CommandError("Explicit remember smoke failed: no memory record was created.")
        if record.scope_type != MemoryRecord.ScopeType.USER or record.scope_id != str(user.id):
            raise CommandError("Explicit remember smoke failed: memory was not stored in user scope.")
        if record.source_kind != EXPLICIT_USER_REMEMBER_SOURCE_KIND:
            raise CommandError("Explicit remember smoke failed: unexpected source_kind.")
        if record.dedupe_key != LOCAL_TIME_PREFERENCE_DEDUPE_KEY:
            raise CommandError("Explicit remember smoke failed: local-time preference dedupe key was not applied.")

        bootstrap_result = bootstrap_memory_for_first_turn(
            second_run,
            agent,
            "What time is it in Ocala right now?",
        )
        if bootstrap_result is None or not bootstrap_result.applied:
            raise CommandError("Explicit remember smoke failed: later run did not bootstrap the stored preference.")

        self.stdout.write("Explicit remember smoke succeeded.")
        self.stdout.write(f"memory_id={record.id}")
        self.stdout.write(f"dedupe_key={record.dedupe_key}")
        self.stdout.write(f"bootstrap_result_count={bootstrap_result.result_count}")
