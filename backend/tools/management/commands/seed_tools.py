from django.core.management.base import BaseCommand

from tools.models import Tool, ToolGroup
from tools.registry import TOOL_REGISTRY


class Command(BaseCommand):
    help = "Seed the global tool catalog."

    def handle(self, *args, **options):
        for group_data in TOOL_REGISTRY:
            group, created = ToolGroup.objects.get_or_create(
                name=group_data["name"],
                defaults={"description": group_data.get("description", "")},
            )
            if not created and group.description != group_data.get("description", ""):
                group.description = group_data.get("description", "")
                group.save(update_fields=["description"])

            for tool_data in group_data.get("tools", []):
                defaults = {
                    "description": tool_data.get("description", ""),
                    "args_schema": tool_data.get("args_schema", {}),
                    "risk": tool_data.get("risk"),
                    "requires_approval": tool_data.get("requires_approval", False),
                    "released": tool_data.get("released", True),
                    "tool_group": group,
                }
                tool, tool_created = Tool.objects.get_or_create(
                    name=tool_data["name"],
                    defaults=defaults,
                )
                tool.tool_group = group
                updated = False
                for field, value in defaults.items():
                    if getattr(tool, field) != value:
                        setattr(tool, field, value)
                        updated = True
                if updated and not tool_created:
                    tool.save()
                if tool_created:
                    tool.save()
