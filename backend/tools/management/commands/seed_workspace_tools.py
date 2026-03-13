from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from uuid import UUID

from core.models import Workspace
from tools.models import Tool, ToolDefinition


class Command(BaseCommand):
    help = "Seed workspace tool definitions from the global catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace",
            "-w",
            help="Workspace ID or name to seed (defaults to all).",
        )
        parser.add_argument(
            "--enable-all",
            action="store_true",
            help="Enable every definition created or updated during this command.",
        )
        parser.add_argument(
            "--include-unreleased",
            action="store_true",
            help="Include tools marked released=False when seeding definitions.",
        )

    def handle(self, *args, **options):
        workspace_key = options.get("workspace")
        enable_all = options.get("enable_all")
        workspaces = Workspace.objects.all()
        if workspace_key:
            filter_q = Q(name=workspace_key)
            try:
                workspace_uuid = UUID(workspace_key)
            except (ValueError, TypeError):
                workspace_uuid = None
            if workspace_uuid:
                filter_q |= Q(id=workspace_uuid)
            workspaces = workspaces.filter(filter_q)
        if not workspaces.exists():
            self.stdout.write(self.style.WARNING("No workspaces matched the provided identifier."))
            return
        include_unreleased = options.get("include_unreleased")
        tools = Tool.objects.all() if include_unreleased else Tool.objects.filter(released=True)
        total = 0
        for workspace in workspaces:
            with transaction.atomic():
                for tool in tools:
                    defaults = {
                        "name": tool.name,
                        "description": tool.description,
                        "args_schema": tool.args_schema,
                        "default_risk_level": tool.risk,
                        "default_requires_approval": tool.requires_approval,
                        "config": {},
                    }
                    create_defaults = {**defaults, "enabled": enable_all}
                    definition, created = ToolDefinition.objects.get_or_create(
                        workspace=workspace, tool=tool, defaults=create_defaults
                    )
                    if not created:
                        updated_fields = []
                        for field, value in defaults.items():
                            current = getattr(definition, field)
                            if current != value:
                                setattr(definition, field, value)
                                updated_fields.append(field)
                        if enable_all and not definition.enabled:
                            definition.enabled = True
                            updated_fields.append("enabled")
                        if updated_fields:
                            definition.save(update_fields=updated_fields)
                    total += 1
        action = "enabled" if enable_all else "seeded"
        self.stdout.write(self.style.SUCCESS(f"{action.capitalize()} {total} tool definitions."))
