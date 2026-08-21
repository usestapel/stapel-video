"""Retention job for the presence meter — see stapel_video.retention.

    python manage.py video_purge_spans [--days N] [--dry-run]
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Delete ParticipantSpan rows that joined before "
        "STAPEL_VIDEO['PRESENCE_SPAN_RETENTION_DAYS'] ago."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override the configured retention window for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would be deleted and change nothing.",
        )

    def handle(self, *args, **options):
        from ...retention import purge_participant_spans

        count = purge_participant_spans(
            older_than_days=options["days"], dry_run=options["dry_run"]
        )
        verb = "would delete" if options["dry_run"] else "deleted"
        self.stdout.write(f"video_purge_spans: {verb} {count} span(s)")
