from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord
from memory.services import get_recent_memory, remember, search_memory


pytestmark = pytest.mark.django_db


@pytest.fixture
def memory_scope_entities():
    User = get_user_model()
    user = User.objects.create_user(username="memorysvc", password="x")
    workspace = Workspace.objects.create(name="Memory Service Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Memory Service Agent",
        soul="Handle memory service tests",
    )
    return user, workspace, agent


def test_remember_truncates_with_ellipsis():
    record = remember(
        scope_type="sandbox",
        scope_id="ws-trim",
        memory_kind="semantic",
        content="x" * 5000,
        summary="y" * 1000,
    )

    assert record.content.endswith("...")
    assert len(record.content) == 4000
    assert record.summary.endswith("...")
    assert len(record.summary) == 600


def test_remember_creates_record():
    record = remember(
        scope_type="sandbox",
        scope_id="ws-1",
        memory_kind="semantic",
        content="The backend lives in backend/.",
        tags=["backend", "layout"],
        importance=0.7,
        summary="Repo layout note",
    )
    assert record.scope_type == "sandbox"
    assert record.scope_id == "ws-1"
    assert record.memory_kind == "semantic"
    assert record.tags == ["backend", "layout"]
    assert record.importance == Decimal("0.70")
    assert record.access_count == 1
    assert record.last_accessed_at is not None
    assert record.pinned is False
    assert record.expires_at is None
    assert record.dedupe_key == ""
    assert record.source_kind == ""
    assert record.source_ref == ""


def test_remember_dedupes_exact_content_and_increments_access_count():
    first = remember("sandbox", "ws-1", "semantic", "same", tags=["one"], summary="a")
    second = remember("sandbox", "ws-1", "semantic", "same", tags=["one"], summary="b")
    assert first.id == second.id
    assert MemoryRecord.objects.count() == 1
    assert second.summary == "b"
    assert second.access_count == 2
    assert second.last_accessed_at is not None


def test_remember_dedupe_key_merges_and_keeps_existing_content_by_default():
    first = remember(
        "sandbox",
        "ws-dedupe",
        "semantic",
        "Use backend/. for Django state.",
        tags=["backend"],
        importance=0.40,
        summary="backend note",
        dedupe_key=" fact:backend-location ",
        source_kind="manual_remember",
        source_ref="operator:one",
    )
    second = remember(
        "sandbox",
        "ws-dedupe",
        "semantic",
        "The backend app lives under backend/ and includes more detail.",
        tags=["architecture", "backend"],
        importance=0.80,
        summary="backend note updated",
        dedupe_key="FACT:BACKEND-LOCATION",
        source_kind="manual_remember",
        source_ref="operator:two",
    )

    assert first.id == second.id
    assert second.dedupe_key == "fact:backend-location"
    assert second.content == "Use backend/. for Django state."
    assert second.summary == "backend note updated"
    assert second.tags == ["backend", "architecture"]
    assert second.importance == Decimal("0.80")
    assert second.source_kind == "manual_remember"
    assert second.source_ref == "operator:one"
    assert second.access_count == 2


def test_remember_dedupe_key_replaces_placeholder_or_truncated_content():
    placeholder = remember(
        "sandbox",
        "ws-replace",
        "semantic",
        "todo",
        dedupe_key="fact:replaceable",
    )
    replaced = remember(
        "sandbox",
        "ws-replace",
        "semantic",
        "The backend app holds the Django state and services.",
        dedupe_key="fact:replaceable",
    )
    assert replaced.id == placeholder.id
    assert replaced.content == "The backend app holds the Django state and services."

    truncated = remember(
        "sandbox",
        "ws-replace",
        "semantic",
        ("x" * 5000),
        dedupe_key="fact:truncated",
    )
    better = remember(
        "sandbox",
        "ws-replace",
        "semantic",
        ("y" * 3990) + " fuller explanation that is better",
        dedupe_key="fact:truncated",
    )
    assert better.id == truncated.id
    assert better.content.startswith("y")


def test_remember_with_different_dedupe_keys_creates_distinct_records():
    remember("sandbox", "ws-1", "semantic", "same", dedupe_key="key:a")
    remember("sandbox", "ws-1", "semantic", "same", dedupe_key="key:b")
    assert MemoryRecord.objects.count() == 2


def test_same_dedupe_key_in_different_scopes_does_not_collide(memory_scope_entities):
    user, workspace, agent = memory_scope_entities
    sandbox_record = remember(
        scope_type="sandbox",
        scope_id=str(workspace.id),
        memory_kind="semantic",
        content="workspace memory",
        dedupe_key="shared-key",
    )
    agent_record = remember(
        agent=agent,
        memory_kind="semantic",
        content="agent memory",
        dedupe_key="shared-key",
    )
    user_record = remember(
        user=user,
        memory_kind="semantic",
        content="user memory",
        dedupe_key="shared-key",
    )

    assert len({sandbox_record.id, agent_record.id, user_record.id}) == 3


def test_remember_revives_expired_exact_match():
    expired_at = timezone.now() - timezone.timedelta(hours=1)
    record = remember(
        "sandbox",
        "ws-expired",
        "semantic",
        "same content",
        summary="stale",
        expires_at=expired_at,
    )

    revived = remember("sandbox", "ws-expired", "semantic", "same content", summary="fresh")

    assert revived.id == record.id
    assert revived.summary == "fresh"
    assert revived.expires_at is None
    assert revived.access_count == 2


def test_search_memory_filters_by_scope_and_kind():
    remember("sandbox", "ws-1", "semantic", "The api app handles endpoints.")
    remember("sandbox", "ws-2", "semantic", "The control app handles operator pages.")
    remember("sandbox", "ws-1", "procedural", "Always seed tools after registry changes.")

    results = search_memory("app", scope_type="sandbox", scope_id="ws-1", memory_kind="semantic", limit=5)

    assert len(results) == 1
    assert results[0].scope_id == "ws-1"
    assert results[0].memory_kind == "semantic"
    assert "api app" in results[0].content


def test_search_memory_matches_tags():
    tagged = remember(
        "sandbox",
        "ws-tags",
        "semantic",
        "The scheduler sends recurring weather reports.",
        tags=["weather", "forecast"],
    )

    results = search_memory("weather", scope_type="sandbox", scope_id="ws-tags", limit=5)

    assert [record.id for record in results] == [tagged.id]


def test_search_memory_increments_access_count_and_last_accessed_at():
    record = remember(
        "sandbox",
        "ws-access",
        "semantic",
        "The scheduler sends recurring weather reports.",
        summary="weather reports",
    )
    first_accessed_at = record.last_accessed_at

    results = search_memory("weather reports", scope_type="sandbox", scope_id="ws-access", limit=5)

    assert [found.id for found in results] == [record.id]
    refreshed = MemoryRecord.objects.get(id=record.id)
    assert refreshed.access_count == 2
    assert refreshed.last_accessed_at is not None
    assert refreshed.last_accessed_at >= first_accessed_at


def test_search_memory_excludes_expired_records():
    active = remember("sandbox", "ws-expiry", "semantic", "Keep active memory visible.")
    expired = remember(
        "sandbox",
        "ws-expiry",
        "semantic",
        "Hide expired memory from results.",
    )
    MemoryRecord.objects.filter(id=expired.id).update(expires_at=timezone.now() - timezone.timedelta(minutes=5))

    results = search_memory("memory", scope_type="sandbox", scope_id="ws-expiry", limit=5)

    assert [record.id for record in results] == [active.id]


def test_search_memory_orders_episodic_by_recency_before_importance():
    older = remember(
        "agent",
        "agent-1",
        "episodic",
        "Scott validated the Telegram bridge.",
        summary="Telegram bridge validation",
        importance=0.95,
    )
    newer = remember(
        "agent",
        "agent-1",
        "episodic",
        "Scott validated the Telegram bridge again after reconnect.",
        summary="Telegram bridge validation",
        importance=0.10,
    )
    MemoryRecord.objects.filter(id=older.id).update(updated_at=older.updated_at - timezone.timedelta(days=1))
    older.refresh_from_db()
    newer.refresh_from_db()

    results = search_memory(
        "Telegram bridge validation",
        scope_type="agent",
        scope_id="agent-1",
        memory_kind="episodic",
        limit=5,
    )

    assert [record.id for record in results[:2]] == [newer.id, older.id]


def test_get_recent_memory_prefers_pinned_then_last_accessed_at():
    pinned = remember(
        "agent",
        "agent-recent",
        "semantic",
        "Pinned memory should stay near the top.",
        pinned=True,
    )
    older = remember(
        "agent",
        "agent-recent",
        "semantic",
        "Older accessed memory.",
    )
    newer = remember(
        "agent",
        "agent-recent",
        "semantic",
        "Newer accessed memory.",
    )
    old_touch = timezone.now() - timezone.timedelta(days=2)
    new_touch = timezone.now() - timezone.timedelta(minutes=5)
    MemoryRecord.objects.filter(id=older.id).update(last_accessed_at=old_touch)
    MemoryRecord.objects.filter(id=newer.id).update(last_accessed_at=new_touch)

    results = get_recent_memory(scope_type="agent", scope_id="agent-recent", memory_kind="semantic", limit=5)

    assert [record.id for record in results[:3]] == [pinned.id, newer.id, older.id]
