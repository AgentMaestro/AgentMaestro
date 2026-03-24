from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from logging_utils import scrub_sensitive_text, scrub_sensitive_value

from memory.health import build_memory_health_report


class Command(BaseCommand):
    help = "Generate a memory health report and persist a snapshot for trend comparison."

    def add_arguments(self, parser):
        parser.add_argument(
            "--compare-days",
            type=int,
            default=30,
            help="Compare current memory counts to the newest saved snapshot at or before this many days ago.",
        )
        parser.add_argument("--no-save", action="store_true", help="Do not persist a MemoryHealthSnapshot row for this report.")

    def handle(self, *args, **options):
        report = build_memory_health_report(
            compare_days=options.get("compare_days") or 30,
            save_snapshot=not bool(options.get("no_save")),
        )
        self.stdout.write(scrub_sensitive_text(json.dumps(scrub_sensitive_value(report), indent=2, sort_keys=True, default=str)))
