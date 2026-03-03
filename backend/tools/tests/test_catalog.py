from django.core.management import call_command
from django.test import TestCase

from tools.models import Tool, ToolGroup, ToolRisk
from tools.policy import visible_tools_for_user


class ToolCatalogTests(TestCase):
    def test_seed_tools_creates_catalog_entries(self):
        call_command("seed_tools")
        group = ToolGroup.objects.filter(name="File Operations").first()
        self.assertIsNotNone(group)
        tool = Tool.objects.filter(name="file_read").first()
        self.assertIsNotNone(tool)
        self.assertEqual(tool.tool_group, group)

    def test_seed_tools_is_idempotent(self):
        call_command("seed_tools")
        call_command("seed_tools")
        self.assertTrue(ToolGroup.objects.filter(name="Execution").exists())

    def test_tool_slug_unique_generation(self):
        group = ToolGroup.objects.create(name="Slug Group")
        tool1 = Tool.objects.create(name="Slug Tool", tool_group=group)
        tool2 = Tool.objects.create(name="Slug Tool!", tool_group=group)
        self.assertTrue(tool1.slug.startswith("slug-tool"))
        self.assertTrue(tool2.slug.startswith("slug-tool"))
        self.assertNotEqual(tool1.slug, tool2.slug)


class ToolVisibilityTests(TestCase):
    def setUp(self):
        self.group = ToolGroup.objects.create(name="Visibility", description="Test")
        self.released_tool = Tool.objects.create(
            name="released_tool",
            tool_group=self.group,
            risk=ToolRisk.SAFE,
            released=True,
        )
        self.unreleased_tool = Tool.objects.create(
            name="preview_tool",
            tool_group=self.group,
            risk=ToolRisk.SAFE,
            released=False,
        )

    def test_visible_tools_filters_unreleased(self):
        user = None
        visible = visible_tools_for_user(user)
        self.assertIn(self.released_tool, visible)
        self.assertNotIn(self.unreleased_tool, visible)

    def test_superuser_sees_unreleased_tools(self):
        superuser = self._create_superuser()
        visible = visible_tools_for_user(superuser)
        self.assertIn(self.unreleased_tool, visible)

    def _create_superuser(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        return User.objects.create_superuser(username="supers", password="pass")
