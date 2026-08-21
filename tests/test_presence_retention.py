"""Retention and its scheduling gates.

A retention window nothing runs is a number in a settings file, not a policy —
so the window is tested, and so is the check that notices nobody scheduled it.
"""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from stapel_video import presence
from stapel_video.models import ParticipantSpan
from stapel_video.retention import purge_participant_spans

pytestmark = pytest.mark.django_db

ROOM = "abc-defg-hij"
A = "user-a"


def _span(days_ago, *, connection, closed=True):
    joined = timezone.now() - timedelta(days=days_ago)
    presence.open_span(
        room_key=ROOM,
        user_id=A,
        connection_id=connection,
        joined_at=joined,
        closed_at=joined + timedelta(minutes=10) if closed else None,
        close_reason="webhook" if closed else "",
    )


def test_the_default_window_is_400_days():
    from stapel_video.conf import video_settings

    assert video_settings.PRESENCE_SPAN_RETENTION_DAYS == 400


def test_spans_older_than_the_window_are_deleted_and_the_rest_kept():
    _span(500, connection="ancient")
    _span(399, connection="recent")

    assert purge_participant_spans() == 1
    assert [s.connection_id for s in ParticipantSpan.objects.all()] == ["recent"]


def test_an_open_span_older_than_the_window_goes_too():
    """Not a very long call — a row the reconciler never reached. Leaving it
    would keep exactly the garbage the purge exists for."""
    _span(500, connection="stuck", closed=False)
    assert purge_participant_spans() == 1
    assert ParticipantSpan.objects.count() == 0


def test_a_none_window_keeps_everything(settings):
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "PRESENCE_SPAN_RETENTION_DAYS": None,
    }
    _span(5000, connection="forever")
    assert purge_participant_spans() == 0
    assert ParticipantSpan.objects.count() == 1


def test_dry_run_counts_and_changes_nothing():
    _span(500, connection="ancient")
    assert purge_participant_spans(dry_run=True) == 1
    assert ParticipantSpan.objects.count() == 1


def test_the_management_command_takes_a_window_override():
    _span(10, connection="ten-days-old")
    out = StringIO()
    call_command("video_purge_spans", "--days", "5", stdout=out)
    assert "deleted 1 span(s)" in out.getvalue()
    assert ParticipantSpan.objects.count() == 0


# ── The scheduling gates ───────────────────────────────────────────────────


def _check_ids(settings):
    from django.core.checks import run_checks

    return [m.id for m in run_checks()]


def test_no_beat_schedule_is_not_second_guessed(settings):
    """A host with no CELERY_BEAT_SCHEDULE runs cron, which this process
    cannot see."""
    settings.CELERY_BEAT_SCHEDULE = {}
    ids = _check_ids(settings)
    assert "stapel_video.W003" not in ids
    assert "stapel_video.W004" not in ids


def test_a_beat_schedule_without_the_jobs_is_warned_about(settings):
    settings.CELERY_BEAT_SCHEDULE = {
        "something-else": {"task": "myapp.tasks.nightly", "schedule": 60}
    }
    ids = _check_ids(settings)
    assert "stapel_video.W003" in ids
    assert "stapel_video.W004" in ids


def test_wiring_the_shipped_schedule_silences_both(settings, monkeypatch):
    settings.CELERY_BEAT_SCHEDULE = dict(_beat_schedule(monkeypatch))
    ids = _check_ids(settings)
    assert "stapel_video.W003" not in ids
    assert "stapel_video.W004" not in ids


def test_stating_that_spans_are_kept_forever_silences_the_retention_warning(settings):
    settings.CELERY_BEAT_SCHEDULE = {
        "something-else": {"task": "myapp.tasks.nightly", "schedule": 60}
    }
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "PRESENCE_SPAN_RETENTION_DAYS": None,
    }
    ids = _check_ids(settings)
    # The sweeper is still required — it is not a retention question.
    assert "stapel_video.W003" in ids
    assert "stapel_video.W004" not in ids


def test_the_beat_schedule_carries_the_configured_cadences(settings, monkeypatch):
    from stapel_video.tasks import PURGE_TASK_NAME, SWEEP_TASK_NAME

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "PRESENCE_SWEEP_INTERVAL_SECONDS": 15,
        "PRESENCE_PURGE_SCHEDULE": {"hour": 2, "minute": 5},
    }
    schedule = _beat_schedule(monkeypatch)

    sweep = schedule["video-presence-sweep"]
    assert sweep["task"] == SWEEP_TASK_NAME
    # An interval, not a crontab: the sweep cadence is a freshness bound
    # ("a lost departure costs at most this much"), and a wall-clock time
    # would make that guarantee depend on when the hour starts.
    assert sweep["schedule"] == timedelta(seconds=15)

    purge = schedule["video-presence-retention-purge"]
    assert purge["task"] == PURGE_TASK_NAME
    assert purge["schedule"] == ("crontab", {"hour": 2, "minute": 5})


def test_asking_for_the_schedule_without_celery_names_the_alternatives(monkeypatch):
    """Celery is optional; a bare ImportError from three frames down is not
    an answer to "how do I schedule this then"."""
    import sys

    from stapel_video.tasks import get_video_beat_schedule

    monkeypatch.setitem(sys.modules, "celery.schedules", None)
    with pytest.raises(ImportError) as excinfo:
        get_video_beat_schedule()
    assert "video_sweep_presence" in str(excinfo.value)
    assert "video_purge_spans" in str(excinfo.value)


def _beat_schedule(monkeypatch):
    """``get_video_beat_schedule()`` with celery stubbed.

    Celery is an optional extra nothing here depends on, so CI does not
    install it — and a schedule builder that only runs where celery happens
    to be present is a schedule builder nobody tests. The stub is the
    stapel-docs precedent: `crontab` becomes an identifiable tuple, and the
    interval half needs no stub at all.
    """
    import sys
    import types

    def crontab(**kwargs):
        return ("crontab", kwargs)

    stub = types.ModuleType("celery.schedules")
    stub.crontab = crontab
    monkeypatch.setitem(sys.modules, "celery", types.ModuleType("celery"))
    monkeypatch.setitem(sys.modules, "celery.schedules", stub)

    from stapel_video.tasks import get_video_beat_schedule

    return get_video_beat_schedule()
