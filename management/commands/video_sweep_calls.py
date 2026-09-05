"""``manage.py video_sweep_calls`` — one pass of the call reconciler.

The cron/systemd form of ``stapel_video.tasks.sweep_calls``, for a deployment
with no celery. Same rule as the presence sweeper: a repair loop that only
exists as a celery task is a repair loop a host without celery silently does
not have.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Expire overdue rings, cap runaway calls, and close accepted calls "
        "the media server says are not happening."
    )

    def handle(self, *args, **options):
        from stapel_video.calls.sweeper import sweep_calls

        result = sweep_calls()
        self.stdout.write(
            "calls: {expired} expired, {capped} capped, {reconciled} "
            "reconciled ({checked} rosters read)".format(**result)
        )
