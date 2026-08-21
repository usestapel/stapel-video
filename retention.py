"""Presence-span retention — delete the detail, keep nothing else that needs to go.

A :class:`~stapel_video.models.ParticipantSpan` is not a stapel-agent
PromptLog: there is no text column to scrub, so retention here deletes rows
rather than emptying them. What survives a purge is whatever a consumer
already exported — this module keeps no rollup table of its own, on purpose.
The aggregate Function computes from the spans every time it is asked, which
is the only way an answer can stay correct while the sweeper is still closing
yesterday's zombies; a host that wants numbers older than the window keeps its
own period rollup, fed from ``video.presence.spans_export``.

``PRESENCE_SPAN_RETENTION_DAYS`` defaults to 400 — a full year of reporting
plus a quarter of slack for a late reconciliation or an audit. ``None``
disables the cut-off, which is a decision a host states rather than drifts
into.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def purge_participant_spans(
    *, older_than_days: int | None = None, dry_run: bool = False
) -> int:
    """Delete spans that JOINED before the retention horizon. Returns the count.

    Keyed on ``joined_at``, not ``left_at``: a span still open after 400 days
    is not a person on a very long call, it is a row the reconciler never got
    to, and leaving it out of the purge would keep exactly the garbage the
    purge exists for.

    *older_than_days* overrides the configured window for one run. A ``None``
    window on both sides is a no-op returning 0 — the caller asked for no
    retention limit and gets none.
    """
    from datetime import timedelta

    from django.utils import timezone

    from .conf import video_settings
    from .models import ParticipantSpan

    days = (
        older_than_days
        if older_than_days is not None
        else video_settings.PRESENCE_SPAN_RETENTION_DAYS
    )
    if days is None:
        return 0

    cutoff = timezone.now() - timedelta(days=int(days))
    qs = ParticipantSpan.objects.filter(joined_at__lt=cutoff)
    if dry_run:
        return qs.count()
    deleted, _ = qs.delete()
    return deleted


__all__ = ["purge_participant_spans"]
