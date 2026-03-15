from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from memory.retention import run_memory_retention


class Command(BaseCommand):
    help = "Run the memory retention distill-and-purge service."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be distilled and purged without mutating data.")
        parser.add_argument("--days", type=int, help="Override the retention cutoff in days.")
        parser.add_argument("--batch-size", type=int, help="Override the maximum number of aged records examined in one run.")
        parser.add_argument("--group-limit", type=int, help="Override the maximum number of episodic groups distilled in one run.")

    def handle(self, *args, **options):
        report = run_memory_retention(
            dry_run=bool(options.get("dry_run")),
            retention_days=options.get("days"),
            batch_size=options.get("batch_size"),
            group_limit=options.get("group_limit"),
        )
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
