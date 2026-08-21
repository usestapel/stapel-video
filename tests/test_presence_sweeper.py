"""The sweeper: the second contour, and the only one that closes a zombie.

A webhook stream is at-least-once, which also means at-most-eventually. One
dropped ``participant_left`` is a span with no end and a number that grows
forever, and no amount of care in the ingest path can see that — the missing
event is missing. So the meter has a second, independent reading of the room,
and these tests hold it to the two properties that make it worth having: a
zombie closes at its LAST CONFIRMED moment (not at "now", which would hand the
customer the whole gap), and a connection the media server reports gets a span
even if its join never arrived.
"""
from datetime import datetime, timedelta, timezone

import pytest

from stapel_video import presence
from stapel_video.models import ParticipantSpan, SpanCloseReason
from stapel_video.tests.fakeprovider import FakeProvider

pytestmark = pytest.mark.django_db

ROOM = "abc-defg-hij"
A = "user-a"
B = "user-b"
T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _live(*participants):
    FakeProvider.live[ROOM] = [
        {
            "identity": f"{user}_{connection}",
            "name": "",
            "joined_at": int(joined.timestamp()),
        }
        for user, connection, joined in participants
    ]


def test_a_confirmed_connection_moves_its_watermark_and_stays_open():
    presence.open_span(
        room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0
    )
    _live((A, "c1", T0))

    now = T0 + timedelta(minutes=5)
    result = presence.sweep_open_spans(now=now)

    span = ParticipantSpan.objects.get()
    assert span.left_at is None
    assert span.last_seen_at == now
    assert result["confirmed"] == 1
    assert result["closed"] == 0


def test_a_zombie_closes_at_its_last_confirmed_moment_not_at_now():
    presence.open_span(
        room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0
    )
    _live((A, "c1", T0))
    confirmed = T0 + timedelta(minutes=5)
    presence.sweep_open_spans(now=confirmed)

    # The provider stops reporting them; their `participant_left` never came.
    FakeProvider.live[ROOM] = []
    presence.sweep_open_spans(now=T0 + timedelta(hours=6))

    span = ParticipantSpan.objects.get()
    assert span.close_reason == SpanCloseReason.SWEEPER
    # 5 minutes, not 6 hours: the error is bounded by the sweep interval.
    assert span.left_at == confirmed


def test_the_zombie_close_emits_the_departure_fact(presence_events):
    presence.open_span(
        room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0
    )
    FakeProvider.live[ROOM] = []
    presence.sweep_open_spans(now=T0 + timedelta(minutes=1))

    left = [e for e in presence_events if e.event_type == "video.participant.left"]
    assert len(left) == 1
    assert left[0].payload["close_reason"] == "sweeper"


def test_a_room_the_provider_no_longer_knows_closes_all_its_spans():
    """A lazily-created media room that nobody is in does not exist, and the
    provider answers None. "Nobody in there" is the honest reading."""
    presence.open_span(room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0)
    presence.open_span(room_key=ROOM, user_id=B, connection_id="c2", joined_at=T0)
    FakeProvider.live.pop(ROOM, None)

    result = presence.sweep_open_spans(now=T0 + timedelta(minutes=2))
    assert result["closed"] == 2
    assert ParticipantSpan.objects.filter(left_at__isnull=True).count() == 0


def test_a_live_connection_with_no_span_is_repaired():
    """The lost `participant_joined`, whose failure mode is otherwise silent:
    no row, no zombie, nothing to notice."""
    presence.open_span(room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0)
    joined_b = T0 + timedelta(minutes=2)
    _live((A, "c1", T0), (B, "c2", joined_b))

    result = presence.sweep_open_spans(now=T0 + timedelta(minutes=5))
    assert result["opened"] == 1

    repaired = ParticipantSpan.objects.get(user_id=B)
    # From the PROVIDER's joined_at, not from when the sweeper happened to run.
    assert repaired.joined_at == joined_b
    assert repaired.left_at is None


def test_the_sweeper_is_idempotent_across_runs():
    presence.open_span(room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0)
    _live((A, "c1", T0))
    for minute in range(1, 4):
        presence.sweep_open_spans(now=T0 + timedelta(minutes=minute))
    assert ParticipantSpan.objects.count() == 1


def test_a_closed_span_is_not_reopened_by_a_provider_still_reporting_it():
    """LiveKit reaps a dropped peer on its own timeout, so the roster can lag
    an explicit leave. Append-only means the closed span stands and the still-
    reported connection does not resurrect it."""
    span, _ = presence.open_span(
        room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0
    )
    presence.close_span(span, at=T0 + timedelta(minutes=1), reason="explicit")
    _live((A, "c1", T0))

    presence.sweep_open_spans(now=T0 + timedelta(minutes=5))
    span.refresh_from_db()
    assert span.left_at == T0 + timedelta(minutes=1)
    assert span.close_reason == SpanCloseReason.EXPLICIT
    assert ParticipantSpan.objects.count() == 1


def test_a_token_only_provider_reports_that_it_cannot_reconcile(settings, caplog):
    """Silence would leave every span open with nothing saying why."""
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "VIDEO_PROVIDER": "stapel_video.tests.test_presence_sweeper.TokenOnlyProvider",
    }
    presence.open_span(room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0)

    result = presence.sweep_open_spans(now=T0 + timedelta(minutes=1))
    assert result["unreachable"] == 1
    assert result["closed"] == 0
    assert ParticipantSpan.objects.filter(left_at__isnull=True).count() == 1
    assert "cannot be reconciled" in caplog.text


def test_one_unreachable_room_does_not_strand_the_others(settings):
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "VIDEO_PROVIDER": "stapel_video.tests.test_presence_sweeper.FlakyProvider",
    }
    presence.open_span(room_key="bad", user_id=A, connection_id="c1", joined_at=T0)
    presence.open_span(room_key="good", user_id=B, connection_id="c2", joined_at=T0)

    result = presence.sweep_open_spans(now=T0 + timedelta(minutes=1))
    assert result["unreachable"] == 1
    assert result["closed"] == 1
    assert ParticipantSpan.objects.get(room_key="bad").left_at is None
    assert ParticipantSpan.objects.get(room_key="good").left_at is not None


def test_the_management_command_runs_the_sweep():
    from io import StringIO

    from django.core.management import call_command

    presence.open_span(room_key=ROOM, user_id=A, connection_id="c1", joined_at=T0)
    FakeProvider.live[ROOM] = []
    out = StringIO()
    call_command("video_sweep_presence", stdout=out)
    assert "1 zombie(s) closed" in out.getvalue()


class TokenOnlyProvider(FakeProvider):
    """A backend that mints tokens and nothing else — the contract's default."""

    def list_participants(self, provider_room_ref):
        raise NotImplementedError


class FlakyProvider(FakeProvider):
    def list_participants(self, provider_room_ref):
        from stapel_video.providers import VideoProviderError

        if provider_room_ref == "bad":
            raise VideoProviderError("transport error")
        return []
