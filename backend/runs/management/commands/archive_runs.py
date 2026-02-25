from __future__ import annotations

from django.core.management.base import BaseCommand

from runs.services.checkpoints import archive_completed_runs


class Command(BaseCommand):
    help = "Archive completed runs older than a cutoff and optionally compact their events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than",
            type=int,
            default=30,
            help="Archive runs that ended more than this many days ago.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of runs to archive in one invocation.",
        )
        parser.add_argument(
            "--compact",
            action="store_true",
            help="Compact verbose events when archiving.",
        )
        parser.add_argument(
            "--verbose-events",
            nargs="*",
            type=str,
            help="Event types to compact (defaults to AGENTMAESTRO_VERBOSE_EVENT_TYPES).",
        )

    def handle(self, *args, **options):
        results = archive_completed_runs(
            older_than_days=options["older_than"],
            limit=options.get("limit"),
            compact=options["compact"],
            event_types=options.get("verbose_events"),
        )
        for row in results:
            self.stdout.write(
                f"Archived run {row['run_id']} -> {row['archive_path']} (compacted {row['compacted']} events)"
            )
        self.stdout.write(self.style.SUCCESS(f"Archived {len(results)} run(s)."))
