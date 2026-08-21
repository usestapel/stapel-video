"""Scheduled work of stapel-video — the two jobs the meter cannot live without.

A presence meter is two mechanisms, and only one of them is event-driven. The
webhook stream records what the media server tells us; the **sweeper** records
what it failed to tell us, and without it a single dropped
``participant_left`` is a span that never ends and a number that grows
forever. The **purge** is the other end: a 400-day retention window that
nothing runs is a number in a settings file, not a policy (the DOCS-02
lesson, and stapel-agent's W014 verbatim).

Celery is OPTIONAL. Both entry points below are plain callables a cron, a
systemd timer or a k8s CronJob can invoke — and both have a management command
form (``video_sweep_presence`` / ``video_purge_spans``). When celery is
installed they are additionally registered as shared tasks under the stable
names below.

Wire them into a host's beat schedule:

    from stapel_video.tasks import get_video_beat_schedule

    CELERY_BEAT_SCHEDULE = {
        **get_video_beat_schedule(),
        ...
    }

``checks.py`` warns (W003 / W004) when a host drives a beat schedule that has
no entry for either.
"""
import logging

logger = logging.getLogger(__name__)

#: Names a beat schedule must reference (stable across refactors).
SWEEP_TASK_NAME = "stapel_video.tasks.sweep_presence"
PURGE_TASK_NAME = "stapel_video.tasks.purge_presence_spans"


def sweep_presence() -> dict:
    """Reconcile open presence spans against the provider's live roster."""
    from .presence import sweep_open_spans

    return sweep_open_spans()


def purge_presence_spans() -> int:
    """Delete presence spans past the retention horizon."""
    from .retention import purge_participant_spans

    deleted = purge_participant_spans()
    logger.info("video presence retention purge: %s span(s) deleted", deleted)
    return deleted


def get_video_beat_schedule() -> dict:
    """Beat entries for the sweeper and the retention purge.

    The sweep cadence is an interval, not a crontab: it is a freshness bound
    ("a lost departure costs at most this much"), and expressing it as a
    wall-clock time would make the guarantee depend on when the hour starts.
    The purge is a nightly crontab, like every other retention job on the
    shelf.
    """
    try:
        from celery.schedules import crontab
    except ImportError as exc:  # pragma: no cover - celery is present in most envs
        raise ImportError(
            "get_video_beat_schedule() builds celery entries and needs celery "
            "installed. Without celery, schedule the plain callables instead: "
            "stapel_video.tasks.sweep_presence and "
            "stapel_video.tasks.purge_presence_spans are both invocable from "
            "cron, as are the video_sweep_presence and video_purge_spans "
            "management commands."
        ) from exc

    from datetime import timedelta

    from .conf import video_settings

    interval = int(video_settings.PRESENCE_SWEEP_INTERVAL_SECONDS or 60)
    purge = dict(video_settings.PRESENCE_PURGE_SCHEDULE or {"hour": 4, "minute": 10})
    return {
        "video-presence-sweep": {
            "task": SWEEP_TASK_NAME,
            "schedule": timedelta(seconds=interval),
        },
        "video-presence-retention-purge": {
            "task": PURGE_TASK_NAME,
            "schedule": crontab(**purge),
        },
    }


try:  # pragma: no cover — exercised by whichever profile the host installs
    from celery import shared_task
except ImportError:
    pass
else:
    sweep_presence = shared_task(name=SWEEP_TASK_NAME)(sweep_presence)
    purge_presence_spans = shared_task(name=PURGE_TASK_NAME)(purge_presence_spans)


__all__ = [
    "PURGE_TASK_NAME",
    "SWEEP_TASK_NAME",
    "get_video_beat_schedule",
    "purge_presence_spans",
    "sweep_presence",
]
