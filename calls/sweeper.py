"""The reconciler — what happens to a call nobody reports on.

Three failures, one loop, and each of them is silent without it:

* **A ring nobody answers.** Somebody has to decide the 45 seconds are up.
  Reading a ringing call already transitions it (``services._expire_if_overdue``),
  so the ANSWER is right without this — but nothing rings the caller's «missed»
  push or writes the thread line if neither party ever asks again, which is
  precisely the case where they both walked away.
* **A call whose end was never reported.** Webhook delivery is at-least-once,
  which is a promise about duplicates and not about losses. A dropped
  ``participant_left`` with no ``room_finished`` behind it leaves a call
  ``accepted`` forever: metered, blocking both parties from calling anybody
  else, and showing an in-call panel that will not close.
* **A call nobody hangs up.** Two phones face down on two tables. The media
  server's own empty timeout never fires, because the room is not empty.

The reconcile arm reads the provider's live roster — the same
``list_participants`` the presence sweeper uses, and on the same seam, so a
provider that cannot be polled says so once rather than having every repair
loop discover it separately.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from ..providers import VideoProviderError, get_video_provider
from .models import Call, CallEndReason, CallState
from .services import close_from_room, expire_call

logger = logging.getLogger(__name__)


def sweep_calls(*, now=None) -> dict:
    """One pass. Returns ``{"expired", "reconciled", "capped", "checked"}``.

    Counters, not a boolean, because this is the loop an operator reads to
    tell "nothing needed doing" from "nothing is running": a sweeper that has
    reconciled nothing in a week is either a healthy webhook path or a dead
    beat entry, and only the ``checked`` count distinguishes them.
    """
    now = now or timezone.now()
    from ..conf import video_settings

    ring_timeout = int(video_settings.CALL_RING_TIMEOUT_SECONDS or 45)
    max_duration = int(video_settings.CALL_MAX_DURATION_SECONDS or 7200)

    expired = 0
    for call in Call.objects.filter(
        state=CallState.RINGING, started_at__lte=now - timedelta(seconds=ring_timeout)
    ).iterator():
        if expire_call(call):
            expired += 1

    capped = 0
    accepted = list(
        Call.objects.filter(state=CallState.ACCEPTED).order_by("started_at")
    )
    for call in accepted:
        anchor = call.answered_at or call.started_at
        if anchor and anchor <= now - timedelta(seconds=max_duration):
            if close_from_room(
                call.room_name, at=now, reason=CallEndReason.MAX_DURATION
            ):
                capped += 1

    # Only calls that have had TIME to connect are reconcilable. An accepted
    # call is a promise about two browsers that have not dialled yet: the
    # token was handed over milliseconds ago, the media room is still empty,
    # and a roster read at that moment says "fewer than two" about a call that
    # is starting perfectly normally. Without this window the sweeper would
    # kill every call within one interval of it being answered — a defect that
    # would present as "calls hang up by themselves", with a healthy webhook
    # path and a green everything.
    grace = timedelta(seconds=int(video_settings.CALL_CONNECT_GRACE_SECONDS or 30))
    reconciled, checked = _reconcile(
        [
            call
            for call in accepted
            if call.state == CallState.ACCEPTED
            and call.answered_at
            and call.answered_at <= now - grace
        ],
        now,
    )

    result = {
        "expired": expired,
        "capped": capped,
        "reconciled": reconciled,
        "checked": checked,
    }
    if expired or capped or reconciled:
        logger.info("video call sweep: %s", result)
    return result


def _reconcile(calls: list, now) -> tuple[int, int]:
    """Close accepted calls the media server says are not happening.

    "Not happening" is the room being gone (``None`` — LiveKit's lazy rooms
    make "no such room" and "nobody in there" one fact) or holding fewer than
    two live connections. Fewer than two, not zero: a 1:1 call with one person
    left in it is over, and waiting for the last one to disconnect would meter
    somebody sitting alone in a room.

    A provider that cannot be polled is reported ONCE per pass, not per call:
    the sweeper's job is repair, and a log line per open call per interval is
    how an operator learns to filter out the message that mattered.
    """
    if not calls:
        return 0, 0
    provider = get_video_provider()
    reconciled = 0
    checked = 0
    for call in calls:
        try:
            live = provider.list_participants(call.room_name)
        except NotImplementedError:
            logger.warning(
                "%s cannot list participants, so accepted calls can only be "
                "closed by a webhook or a hangup on this deployment",
                type(provider).__name__,
            )
            return reconciled, checked
        except VideoProviderError:
            # One unreachable room must not strand the rest of the pass: the
            # next interval asks again, and the cost of waiting is one more
            # sweep of over-count rather than a dropped repair loop.
            logger.warning(
                "video call sweep: could not read the roster of %s", call.room_name,
                exc_info=True,
            )
            continue
        checked += 1
        if live is None or len(live) < 2:
            if close_from_room(
                call.room_name, at=now, reason=CallEndReason.RECONCILED
            ):
                reconciled += 1
    return reconciled, checked


__all__ = ["sweep_calls"]
