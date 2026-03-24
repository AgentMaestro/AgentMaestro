import asyncio
import json
from typing import Any

from django.core.management.base import BaseCommand
from logging_utils import scrub_sensitive_text, scrub_sensitive_value

from llm.services.toolrunner_bridge import run_tool

class Command(BaseCommand):
    help = "Smoke test ToolRunner integration by calling repo_tree."

    def add_arguments(self, parser):
        parser.add_argument("--root", default=".", help="Root path relative to workspace")
        parser.add_argument("--max-depth", type=int, default=2)
        parser.add_argument(
            "--absolute-root",
            action="store_true",
            help="Treat the provided root as an absolute path outside the workspace",
        )

    def handle(self, *args: Any, **options: Any):
        root = options["root"]
        max_depth = options["max_depth"]
        payload_args = {
            "root": "." if options["absolute_root"] else root,
            "max_depth": max_depth,
            "include_files": True,
            "include_dirs": True,
        }

        if options["absolute_root"]:
            payload_args["absolute_root"] = root

        payload = payload_args

        async def _run():
            result = await run_tool("repo_tree", payload)
            self.stdout.write(scrub_sensitive_text("ok=%s" % result.get("ok")))
            self.stdout.write(scrub_sensitive_text("error=%s" % result.get("error")))
            pretty = json.dumps(scrub_sensitive_value(result.get("result")), indent=2, ensure_ascii=False)
            self.stdout.write(scrub_sensitive_text(pretty))
            meta = result.get("meta", {})
            self.stdout.write(scrub_sensitive_text("meta=%s" % json.dumps(scrub_sensitive_value(meta), indent=2, ensure_ascii=False)))

        asyncio.run(_run())
