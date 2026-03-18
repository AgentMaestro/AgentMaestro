import pytest
from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from memory.models import MemoryRecord
from memory.scope import ResolvedMemoryScope, resolve_memory_scope
from memory.services import remember, search_memory


pytestmark = pytest.mark.django_db


@pytest.fixture
def scope_entities():
    User = get_user_model()
    user = User.objects.create_user(username="memoryscope", password="x")
    workspace = Workspace.objects.create(name="Memory Scope Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Memory Scope Agent",
        soul="Handle memory scope tests",
    )
    return user, workspace, agent


def test_resolve_agent_scope(scope_entities):
    _user, _workspace, agent = scope_entities

    resolved = resolve_memory_scope(agent=agent)

    assert resolved == ResolvedMemoryScope(
        scope_type=MemoryRecord.ScopeType.AGENT,
        scope_id=str(agent.id),
        label=agent.name,
    )


def test_resolve_workspace_scope_alias(scope_entities):
    _user, workspace, _agent = scope_entities

    resolved = resolve_memory_scope(scope_type="workspace", scope_id=workspace.id)

    assert resolved.scope_type == MemoryRecord.ScopeType.SANDBOX
    assert resolved.scope_id == str(workspace.id)
    assert resolved.label == workspace.name


def test_resolve_sandbox_scope_keeps_raw_string_when_no_workspace_matches(scope_entities):
    resolved = resolve_memory_scope(scope_type="sandbox", scope_id="C:/Dev/AgentMaestro")

    assert resolved.scope_type == MemoryRecord.ScopeType.SANDBOX
    assert resolved.scope_id == "C:/Dev/AgentMaestro"
    assert resolved.label == "C:/Dev/AgentMaestro"


def test_resolve_user_scope(scope_entities):
    user, _workspace, _agent = scope_entities

    resolved = resolve_memory_scope(user=user)

    assert resolved.scope_type == MemoryRecord.ScopeType.USER
    assert resolved.scope_id == str(user.pk)
    assert resolved.label == user.username


def test_resolve_user_scope_accepts_case_insensitive_username(scope_entities):
    user, _workspace, _agent = scope_entities

    resolved = resolve_memory_scope(scope_type="user", scope_id=user.username.upper())

    assert resolved.scope_type == MemoryRecord.ScopeType.USER
    assert resolved.scope_id == str(user.pk)
    assert resolved.label == user.username


def test_invalid_scope_rejection(scope_entities):
    with pytest.raises(ValueError):
        resolve_memory_scope(scope_type="bad-scope", scope_id="123")
    with pytest.raises(ValueError):
        resolve_memory_scope(scope_type="agent", scope_id="")
    with pytest.raises(ValueError):
        resolve_memory_scope(scope_type="agent", scope_id="missing-agent")


def test_scope_resolver_is_used_by_memory_services(scope_entities):
    _user, workspace, agent = scope_entities
    remember(agent=agent, memory_kind="semantic", content="Agent-specific memory", summary="agent memory")
    remember(workspace=workspace, memory_kind="semantic", content="Workspace-specific memory", summary="workspace memory")

    agent_results = search_memory("memory", agent=agent, limit=5)
    workspace_results = search_memory("memory", workspace=workspace, limit=5)

    assert [record.scope_type for record in agent_results] == [MemoryRecord.ScopeType.AGENT]
    assert [record.scope_id for record in agent_results] == [str(agent.id)]
    assert [record.scope_type for record in workspace_results] == [MemoryRecord.ScopeType.SANDBOX]
    assert [record.scope_id for record in workspace_results] == [str(workspace.id)]
