import json

from django.core.management.base import BaseCommand

from llm.services.tool_schemas import get_tool_arg_templates, get_tool_schemas


class Command(BaseCommand):
    help = "Prints the canonically defined tool schemas and argument templates."

    def handle(self, *args, **options):
        templates = get_tool_arg_templates()
        for tool in get_tool_schemas():
            name = tool.get("name")
            self.stdout.write(f"\nTool: {name}")
            self.stdout.write("Schema:")
            schema = tool.get("parameters") or {}
            self.stdout.write(json.dumps(schema, indent=2))
            example = templates.get(name)
            if example:
                self.stdout.write("Example args:")
                self.stdout.write(json.dumps(example, indent=2))
        self.stdout.write("\nRun llm_toolloop_real_smoke once you have templates in hand.")
