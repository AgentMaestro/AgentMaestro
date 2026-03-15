import pytest
from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import RunMemory
from runs.services.memory import (
    append_tool_result_summary,
    get_or_create_run_memory,
    merge_key_facts,
    merge_open_questions,
    update_run_memory,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def run_factory():
    def _build():
        suffix = RunMemory.objects.count()
        user = get_user_model().objects.create_user(username=f"memory-{suffix}", password="x")
        workspace = Workspace.objects.create(name=f"Memory Workspace {suffix}")
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=user,
            role=WorkspaceMembership.Role.OWNER,
        )
        agent = Agent.objects.create(
            workspace=workspace,
            name=f"Memory Agent {suffix}",
            soul="Memory tests",
            created_by=user,
        )
        return agent.runs.create(
            workspace=workspace,
            started_by=user,
            status="RUNNING",
            input_text="test",
        )

    return _build


def test_get_or_create_run_memory(run_factory):
    run = run_factory()
    memory = get_or_create_run_memory(run)
    assert memory.run == run
    assert RunMemory.objects.count() == 1


def test_update_run_memory_normalizes_fields(run_factory):
    run = run_factory()
    memory = update_run_memory(
        run,
        objective="Ship research foundation",
        key_facts=["one", "one", "two"],
        open_questions=["what next?"],
        notes="n" * 5000,
    )
    assert memory.objective == "Ship research foundation"
    assert memory.key_facts == ["one", "two"]
    assert memory.open_questions == ["what next?"]
    assert len(memory.notes) <= 4000


def test_append_tool_result_summary_caps_entries(run_factory):
    run = run_factory()
    for index in range(10):
        append_tool_result_summary(run, "file_read", f"summary {index}")
    memory = get_or_create_run_memory(run)
    assert len(memory.recent_tool_results) == 8
    assert memory.recent_tool_results[0]["summary"] == "summary 2"
    assert memory.recent_tool_results[-1]["summary"] == "summary 9"


def test_merge_helpers_dedupe(run_factory):
    run = run_factory()
    merge_key_facts(run, ["alpha", "beta"])
    merge_key_facts(run, ["beta", "gamma"])
    merge_open_questions(run, ["one?", "two?"])
    merge_open_questions(run, ["two?", "three?"])
    memory = get_or_create_run_memory(run)
    assert memory.key_facts == ["alpha", "beta", "gamma"]
    assert memory.open_questions == ["one?", "two?", "three?"]
