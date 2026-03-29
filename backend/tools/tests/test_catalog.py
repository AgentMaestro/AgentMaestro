from django.core.management import call_command
from django.test import TestCase
from llm.services.tool_schemas import get_tool_schemas
from tools.models import Tool, ToolGroup, ToolRisk
from tools.policy import visible_tools_for_user
from tools.registry import TOOL_REGISTRY


class ToolCatalogTests(TestCase):
    def test_seed_tools_creates_catalog_entries(self):
        call_command("seed_tools")
        group_name = TOOL_REGISTRY[0]["name"]
        group = ToolGroup.objects.filter(name=group_name).first()
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


class ToolSchemaCoverageTests(TestCase):
    def test_safe_command_tools_are_registered(self):
        registry_tools = {tool["name"]: tool for group in TOOL_REGISTRY for tool in group["tools"]}
        self.assertIn("run_command_safe", registry_tools)
        self.assertIn("run_tests", registry_tools)
        self.assertFalse(registry_tools["run_command_safe"]["requires_approval"])
        self.assertFalse(registry_tools["run_tests"]["requires_approval"])

        fallback_names = {tool["name"] for tool in get_tool_schemas()}
        self.assertIn("run_command_safe", fallback_names)
        self.assertIn("run_tests", fallback_names)

    def test_google_bridge_tool_is_registered(self):
        registry_tools = {tool["name"]: tool for group in TOOL_REGISTRY for tool in group["tools"]}
        fallback_names = {tool["name"] for tool in get_tool_schemas()}

        self.assertIn("google_bridge", registry_tools)
        self.assertIn("google_bridge", fallback_names)
        self.assertFalse(registry_tools["google_bridge"]["requires_approval"])

    def test_send_telegram_tool_is_registered(self):
        registry_tools = {tool["name"]: tool for group in TOOL_REGISTRY for tool in group["tools"]}
        fallback_names = {tool["name"] for tool in get_tool_schemas()}

        self.assertIn("send_telegram", registry_tools)
        self.assertIn("send_telegram", fallback_names)
        self.assertEqual(registry_tools["send_telegram"]["risk"], ToolRisk.ELEVATED)
        self.assertIn("target", registry_tools["send_telegram"]["args_schema"]["properties"])
        self.assertIn("text", registry_tools["send_telegram"]["args_schema"]["properties"])
        self.assertIn("name", registry_tools["send_telegram"]["args_schema"]["properties"])

    def test_scheduled_task_management_tools_are_registered(self):
        registry_tools = {tool["name"]: tool for group in TOOL_REGISTRY for tool in group["tools"]}
        fallback_names = {tool["name"] for tool in get_tool_schemas()}

        for tool_name in {
            "schedule_task",
            "get_scheduled_task",
            "edit_scheduled_task",
            "disable_scheduled_task",
            "enable_scheduled_task",
            "list_scheduled_tasks",
        }:
            self.assertIn(tool_name, registry_tools)
            self.assertIn(tool_name, fallback_names)
            self.assertFalse(registry_tools[tool_name]["requires_approval"])

    def test_current_datetime_tool_is_registered(self):
        registry_tools = {tool["name"]: tool for group in TOOL_REGISTRY for tool in group["tools"]}
        fallback_names = {tool["name"] for tool in get_tool_schemas()}

        self.assertIn("get_current_datetime", registry_tools)
        self.assertIn("get_current_datetime", fallback_names)
        self.assertFalse(registry_tools["get_current_datetime"]["requires_approval"])

    def test_scheduled_task_detail_tool_is_registered(self):
        registry_tools = {tool["name"]: tool for group in TOOL_REGISTRY for tool in group["tools"]}
        fallback_names = {tool["name"] for tool in get_tool_schemas()}
        schema_tools = {tool["name"]: tool for tool in get_tool_schemas()}

        self.assertIn("get_scheduled_task", registry_tools)
        self.assertIn("get_scheduled_task", fallback_names)
        self.assertFalse(registry_tools["get_scheduled_task"]["requires_approval"])
        self.assertIn(
            "execution_payload",
            schema_tools["get_scheduled_task"]["response_fields"],
        )
        self.assertIn(
            "include_execution_payload",
            registry_tools["list_scheduled_tasks"]["args_schema"]["properties"],
        )

    def test_code_navigation_tools_are_registered(self):
        registry_tools = {tool["name"]: tool for group in TOOL_REGISTRY for tool in group["tools"]}
        fallback_names = {tool["name"] for tool in get_tool_schemas()}

        for tool_name in {
            "search_files",
            "list_symbols",
            "find_symbol",
            "find_references",
            "jump_to_symbol",
        }:
            self.assertIn(tool_name, registry_tools)
            self.assertIn(tool_name, fallback_names)
            self.assertFalse(registry_tools[tool_name]["requires_approval"])

        self.assertIn("symbol_name", registry_tools["find_symbol"]["args_schema"]["properties"])
        self.assertIn(
            "context_lines", registry_tools["find_references"]["args_schema"]["properties"]
        )
        self.assertIn("scope", registry_tools["search_files"]["args_schema"]["properties"])
        self.assertIn("scope", registry_tools["list_symbols"]["args_schema"]["properties"])
        self.assertIn("scope", registry_tools["find_symbol"]["args_schema"]["properties"])
        self.assertIn("scope", registry_tools["find_references"]["args_schema"]["properties"])
        self.assertIn("scope", registry_tools["jump_to_symbol"]["args_schema"]["properties"])
        self.assertIn("compact", registry_tools["search_files"]["args_schema"]["properties"])
        self.assertIn("compact", registry_tools["list_symbols"]["args_schema"]["properties"])
        self.assertIn("compact", registry_tools["find_symbol"]["args_schema"]["properties"])
        self.assertIn("compact", registry_tools["find_references"]["args_schema"]["properties"])
        self.assertIn("compact", registry_tools["jump_to_symbol"]["args_schema"]["properties"])
        self.assertIn(
            "does not search file contents", registry_tools["search_files"]["description"]
        )
        self.assertIn("Use this when", registry_tools["search_files"]["description"])
        self.assertIn(
            "Do not use it for content search", registry_tools["search_files"]["description"]
        )
        self.assertIn(
            "Search one path/name query at a time", registry_tools["search_files"]["description"]
        )
        self.assertIn("Hidden paths are included", registry_tools["search_files"]["description"])
        self.assertIn(
            "Test paths are included by default", registry_tools["search_files"]["description"]
        )
        self.assertIn("returned_count", registry_tools["search_files"]["description"])
        self.assertIn("max_results_used", registry_tools["search_files"]["description"])
        self.assertIn("omit `cmd` entirely", registry_tools["lint_runner"]["description"])
        self.assertIn("omit `cmd` entirely", registry_tools["format_runner"]["description"])
        self.assertIn(
            "Only valid when tool=command",
            registry_tools["lint_runner"]["args_schema"]["properties"]["cmd"]["description"],
        )
        self.assertIn(
            "Only valid when tool=command",
            registry_tools["format_runner"]["args_schema"]["properties"]["cmd"]["description"],
        )
        self.assertIn("resolved_cwd", registry_tools["lint_runner"]["response_fields"])
        self.assertIn("resolved_paths", registry_tools["lint_runner"]["response_fields"])
        self.assertIn("command", registry_tools["lint_runner"]["response_fields"])
        self.assertIn("resolved_cwd", registry_tools["format_runner"]["response_fields"])
        self.assertIn("resolved_paths", registry_tools["format_runner"]["response_fields"])
        self.assertIn("command", registry_tools["format_runner"]["response_fields"])
        self.assertIn("requested_cwd", registry_tools["run_command_safe"]["response_fields"])
        self.assertIn("resolved_cwd", registry_tools["run_command_safe"]["response_fields"])
        self.assertIn("requested_path", registry_tools["file_patch"]["response_fields"])
        self.assertIn("resolved_path", registry_tools["file_patch"]["response_fields"])
        self.assertIn("requested_repo_dir", registry_tools["file_patch"]["response_fields"])
        self.assertIn("resolved_repo_dir", registry_tools["file_patch"]["response_fields"])
        for tool_name in {
            "git_add",
            "git_apply",
            "git_branch_create",
            "git_checkout",
            "git_commit",
            "git_diff",
            "git_log",
            "git_push",
        }:
            self.assertIn("requested_repo_dir", registry_tools[tool_name]["response_fields"])
            self.assertIn("resolved_repo_dir", registry_tools[tool_name]["response_fields"])
        self.assertIn(
            "coverage summaries", registry_tools["coverage_runner"]["response_fields"]["files"]
        )
        self.assertIn("allowed executables", registry_tools["run_command_safe"]["description"])
        self.assertIn("Common rejections", registry_tools["run_command_safe"]["description"])
