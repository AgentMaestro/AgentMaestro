from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from core.models import Workspace

from agents.current import agent_creation_context
from agents.tooling import TOOL_REGISTRY
from tools.models import AgentToolGrant, Tool, ToolDefinition, ToolGroup, ToolRisk
from .models import Agent

from comms.models import Transport, TransportEndpoint


class AgentModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="agent-owner")
        self.workspace = Workspace.objects.create(name="agent-test-ws")

    def test_slug_generated_from_name(self):
        agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="Alpha Agent",
            soul="Core directives",
        )
        self.assertEqual(agent.slug, "alpha-agent")

    def test_slug_updates_when_name_changes(self):
        agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="Beta Agent",
            soul="Directive beta",
        )
        agent.name = "Beta Agent Updated"
        agent.save()
        self.assertEqual(agent.slug, "beta-agent-updated")

    def test_name_uniqueness_enforced(self):
        Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="Unique Agent",
            soul="Unique soul",
        )
        with self.assertRaises(IntegrityError):
            Agent.objects.create(
                workspace=self.workspace,
                owner=self.user,
                name="Unique Agent",
                soul="Duplicate soul",
            )

    def test_name_appends_suffix_on_duplicate(self):
        Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="Duplicate Agent",
            soul="First copy",
        )
        second = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="Duplicate Agent",
            soul="Second copy",
        )
        self.assertNotEqual(second.name, "Duplicate Agent")
        self.assertTrue(second.name.endswith("-2"))

    def test_owner_is_required(self):
        with agent_creation_context(self.user):
            agent = Agent.objects.create(
                workspace=self.workspace,
                name="Ownerless Agent",
                soul="No owner allowed",
            )
        self.assertEqual(agent.owner, self.user)

    def test_owner_assigned_from_context(self):
        other_user = get_user_model().objects.create_user(username="context-owner")
        with agent_creation_context(other_user):
            agent = Agent.objects.create(
                workspace=self.workspace,
                name="Context Agent",
                soul="Context soul",
            )
        self.assertEqual(agent.owner, other_user)


class AgentWizardViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="wizard-user")
        self.client.force_login(self.user)


    def _seed_workspace_with_tool(self):
        workspace = Workspace.objects.create(name="Wizard Workspace")
        group = ToolGroup.objects.create(name="wizard-group")
        tool = Tool.objects.create(
            name="wizard_tool",
            tool_group=group,
            risk=ToolRisk.SAFE,
            requires_approval=False,
            released=True,
        )
        ToolDefinition.objects.create(workspace=workspace, tool=tool, enabled=True)
        return workspace, tool

    def test_wizard_creates_agent(self):
        url = reverse("agents:agent_create")
        basics = {
            "name": "Wizard Agent",
            "description": "Guide the user.",
            "soul": "Always be helpful.",
        }
        self.client.post(f"{url}?step=1", data=basics)
        llm = {
            "default_model": "gpt-5.1",
            "temperature": "0.75",
            "policy_name": "react",
            "plan_enabled": "on",
        }
        self.client.post(f"{url}?step=2", data=llm)
        workspace, tool = self._seed_workspace_with_tool()
        self.client.post(f"{url}?step=3", data={"workspace": str(workspace.id)})
        self.client.post(f"{url}?step=4", data={f"tool_{tool.id}": "on"})
        response = self.client.post(f"{url}?step=5")
        self.assertEqual(response.status_code, 302)
        agent = Agent.objects.filter(name="Wizard Agent").first()
        self.assertIsNotNone(agent)
        self.assertEqual(agent.owner, self.user)
        self.assertEqual(agent.workspace.name, "Wizard Workspace")
        self.assertTrue(agent.plan_enabled)
        self.assertEqual(agent.tool_policy_json["selected_tools"], ["wizard_tool"])
        self.assertNotIn("agent_wizard", self.client.session)
        self.assertTrue(
            AgentToolGrant.objects.filter(agent=agent, tool=tool, enabled=True).exists()
        )

    def test_wizard_tool_selection_default_deny(self):
        url = reverse("agents:agent_create")
        self.client.post(f"{url}?step=1", data={"name": "No Tools Agent", "soul": "Defer"})
        self.client.post(
            f"{url}?step=2",
            data={"default_model": "gpt-5", "temperature": "0.5", "policy_name": "react"},
        )
        workspace, tool = self._seed_workspace_with_tool()
        self.client.post(f"{url}?step=3", data={"workspace": str(workspace.id)})
        self.client.post(f"{url}?step=4", data={})
        response = self.client.post(f"{url}?step=5")
        self.assertEqual(response.status_code, 302)
        agent = Agent.objects.filter(name="No Tools Agent").first()
        self.assertIsNotNone(agent)
        self.assertFalse(AgentToolGrant.objects.filter(agent=agent).exists())


class AgentDetailViewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="detail-owner")
        self.workspace = Workspace.objects.create(name="Detail Workspace")
        self.agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.owner,
            name="Detail Agent",
            soul="Just behave.",
        )
        group = ToolGroup.objects.create(name="detail-tools")
        tool = Tool.objects.create(
            name="detail_tool",
            tool_group=group,
            risk=ToolRisk.SAFE,
            requires_approval=False,
            released=True,
        )
        ToolDefinition.objects.create(workspace=self.workspace, tool=tool, enabled=True)
        AgentToolGrant.objects.create(agent=self.agent, tool=tool, enabled=True)

        transport = Transport.objects.create(key="telegram", display_name="Telegram Bot")
        TransportEndpoint.objects.create(
            transport=transport,
            kind="bot",
            config={"agent_id": str(self.agent.id), "bot_username": "detail_bot"},
        )

        self.client.force_login(self.owner)

    def test_owner_sees_agent_detail(self):
        response = self.client.get(reverse("agents:agent_detail", kwargs={"slug": self.agent.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Agent")
        self.assertContains(response, "detail_tool")
        self.assertContains(response, "Configure Telegram")
        self.assertTrue(response.context["tool_count"] >= 1)
        self.assertTrue(response.context["transport_status"]["connected"])

    def test_non_member_forbidden(self):
        other = get_user_model().objects.create_user(username="outsider")
        self.client.force_login(other)
        response = self.client.get(reverse("agents:agent_detail", kwargs={"slug": self.agent.slug}))
        self.assertEqual(response.status_code, 403)
