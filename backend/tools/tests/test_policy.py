from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from agents.models import Agent
from core.models import Workspace
from tools.models import (
    AgentToolGrant,
    Tool,
    ToolDefinition,
    ToolGroup,
    ToolRisk,
)
from tools.policy import assert_tool_allowed, get_effective_tools, ToolNotAllowedError


class ToolPolicyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="password", email="owner@example.com")
        self.superuser = User.objects.create_superuser(username="admin", password="password", email="admin@example.com")
        self.workspace = Workspace.objects.create(name="policy-workspace")
        self.agent = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="policy-agent",
            description="policy agent",
            default_model="gpt-5",
            temperature=Decimal("0.70"),
            soul="",
            policy_name="react",
        )
        self.group = ToolGroup.objects.create(name="policy-group")
        self.tool = Tool.objects.create(
            name="policy_tool",
            tool_group=self.group,
            risk=ToolRisk.SAFE,
            requires_approval=False,
            released=True,
        )
        self.definition = ToolDefinition.objects.create(
            workspace=self.workspace,
            tool=self.tool,
            name=self.tool.name,
            enabled=True,
        )

    def test_agent_grant_required(self):
        effective = get_effective_tools(self.agent, self.user)
        self.assertEqual([], effective)

    def test_workspace_disabled_blocks_access(self):
        self.definition.enabled = False
        self.definition.save()
        AgentToolGrant.objects.create(agent=self.agent, tool=self.tool, enabled=True)
        self.assertEqual([], get_effective_tools(self.agent, self.user))

    def test_unreleased_tool_requires_superuser(self):
        preview = Tool.objects.create(
            name="preview_tool",
            tool_group=self.group,
            risk=ToolRisk.SAFE,
            requires_approval=False,
            released=False,
        )
        ToolDefinition.objects.create(
            workspace=self.workspace,
            tool=preview,
            name=preview.name,
            enabled=True,
        )
        AgentToolGrant.objects.create(agent=self.agent, tool=preview, enabled=True)
        self.assertEqual([], get_effective_tools(self.agent, self.user))
        allowed = get_effective_tools(self.agent, self.superuser)
        self.assertEqual(1, len(allowed))
        self.assertEqual(preview, allowed[0].tool)

    def test_metric_overrides_and_assert(self):
        AgentToolGrant.objects.create(agent=self.agent, tool=self.tool, enabled=True)
        self.definition.default_risk_level = ToolRisk.DANGEROUS
        self.definition.default_requires_approval = True
        self.definition.save()
        effective = get_effective_tools(self.agent, self.user)
        self.assertEqual(1, len(effective))
        entry = effective[0]
        self.assertEqual(ToolRisk.DANGEROUS, entry.risk)
        self.assertTrue(entry.requires_approval)
        allowed = assert_tool_allowed(self.agent, self.user, self.tool.name)
        self.assertEqual(self.tool, allowed.tool)
        with self.assertRaises(ToolNotAllowedError):
            assert_tool_allowed(self.agent, self.user, "missing_tool")


class SeedWorkspaceToolsTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="seed-workspace")
        self.group = ToolGroup.objects.create(name="seed-group")
        self.tool = Tool.objects.create(
            name="seed_tool",
            tool_group=self.group,
            risk=ToolRisk.SAFE,
            requires_approval=False,
            released=True,
        )

    def test_seed_workspace_tools_creates_definition(self):
        call_command("seed_workspace_tools", "--workspace", self.workspace.name)
        definition = ToolDefinition.objects.get(workspace=self.workspace, tool=self.tool)
        self.assertFalse(definition.enabled)

    def test_seed_workspace_tools_enable_all(self):
        call_command("seed_workspace_tools", "--workspace", self.workspace.name, "--enable-all")
        definition = ToolDefinition.objects.get(workspace=self.workspace, tool=self.tool)
        self.assertTrue(definition.enabled)

    def test_seed_workspace_tools_preserves_existing_enabled_flag(self):
        definition = ToolDefinition.objects.create(
            workspace=self.workspace,
            tool=self.tool,
            name=self.tool.name,
            enabled=True,
        )
        call_command("seed_workspace_tools", "--workspace", self.workspace.name)
        definition.refresh_from_db()
        self.assertTrue(definition.enabled)


class SeedDevToolsCommandTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="devowner", password="password", email="devowner@example.com")
        self.workspace = Workspace.objects.create(name="Dev Workspace")
        self.agent_one = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="jeeves",
            description="dev agent one",
            default_model="gpt-5",
            temperature=Decimal("0.70"),
            soul="",
            policy_name="react",
        )
        self.agent_two = Agent.objects.create(
            workspace=self.workspace,
            owner=self.user,
            name="maestro",
            description="dev agent two",
            default_model="gpt-5",
            temperature=Decimal("0.70"),
            soul="",
            policy_name="react",
        )

    def test_seed_dev_tools_enables_workspace_tools_and_grants_agents(self):
        call_command("seed_dev_tools", "--workspace", self.workspace.name)

        self.assertTrue(ToolDefinition.objects.filter(workspace=self.workspace, enabled=True).exists())
        file_read = Tool.objects.get(name="file_read")
        self.assertTrue(AgentToolGrant.objects.filter(agent=self.agent_one, tool=file_read, enabled=True).exists())
        self.assertTrue(AgentToolGrant.objects.filter(agent=self.agent_two, tool=file_read, enabled=True).exists())

        self.agent_one.refresh_from_db()
        self.assertIn("file_read", self.agent_one.tool_policy_json.get("selected_tools", []))

    def test_seed_dev_tools_can_target_specific_agent(self):
        call_command("seed_dev_tools", "--workspace", self.workspace.name, "--agent", self.agent_one.name)

        file_read = Tool.objects.get(name="file_read")
        self.assertTrue(AgentToolGrant.objects.filter(agent=self.agent_one, tool=file_read, enabled=True).exists())
        self.assertFalse(AgentToolGrant.objects.filter(agent=self.agent_two, tool=file_read).exists())
