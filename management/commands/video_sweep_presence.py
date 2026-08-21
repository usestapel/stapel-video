"""Reconcile open presence spans against the media server — see
stapel_video.presence.sweep_open_spans.

The cron form of the sweeper: a lost `participant_left` webhook leaves a span
open forever, and only a second, independent reading of the room closes it.

    python manage.py video_sweep_presence
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Close presence spans whose connection the video provider no longer "
        "reports, at their last confirmed moment, and open spans for live "
        "connections whose join webhook never arrived."
    )

    def handle(self, *args, **options):
        from ...tasks import sweep_presence

        result = sweep_presence()
        self.stdout.write(
            "video_sweep_presence: {rooms} room(s), {confirmed} confirmed, "
            "{closed} zombie(s) closed, {opened} repaired, "
            "{unreachable} unreachable".format(**result)
        )
