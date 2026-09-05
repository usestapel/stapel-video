"""Presence ingest: webhook fidelity, dispatch registry, span append-only.

The defect this whole path closes is that ``parse_webhook`` used to collapse
every event into four egress keys, so a ``participant_left`` — the only
departure signal that survives a client crash — arrived, verified, and was
dropped. These tests assert the normalized event carries the rest, that the
dispatch is a registry rather than an ``if``, and that ingest survives
at-least-once, out-of-order delivery without double-counting anybody.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from stapel_video import presence
from stapel_video.models import ParticipantSpan, SpanCloseReason

pytestmark = pytest.mark.django_db

ROOM = "abc-defg-hij"
ALICE = "11111111-1111-1111-1111-111111111111"
SESSION = "sess-a"
T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _epoch(moment) -> int:
    return int(moment.timestamp())


def _event(event, *, at, identity=f"{ALICE}_{SESSION}", joined_at=T0, room=ROOM):
    body = {"event": event, "id": f"EV_{event}_{_epoch(at)}", "created_at": _epoch(at)}
    if room is not None:
        body["room"] = {"name": room, "sid": "RM_1"}
    if identity is not None:
        body["participant"] = {
            "identity": identity,
            "name": "Alice",
            "joined_at": _epoch(joined_at) if joined_at else 0,
        }
    return body


def _post(api_client, body):
    return api_client.post(
        "/video/api/v1/webhook",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer good-signature",
    )


# ── 1. Webhook fidelity ────────────────────────────────────────────────────


def test_parse_webhook_preserves_room_participant_and_timestamps():
    from stapel_video.tests.fakeprovider import FakeProvider

    body = json.dumps(_event("participant_joined", at=T0)).encode()
    parsed = FakeProvider().parse_webhook(body, "Bearer good")

    assert parsed["event"] == "participant_joined"
    assert parsed["event_ts"] == T0
    assert parsed["room"] == {"name": ROOM, "sid": "RM_1"}
    assert parsed["participant"]["identity"] == f"{ALICE}_{SESSION}"
    # The provider decomposes the identity it invented — a caller re-parsing
    # that string is a caller that has forked the provider.
    assert parsed["participant"]["user_id"] == ALICE
    assert parsed["participant"]["connection_id"] == SESSION
    assert parsed["participant"]["joined_at"] == T0


def test_parse_webhook_keeps_the_egress_keys_byte_identical():
    """The recording path predates all of this and must not have moved."""
    from stapel_video.tests.fakeprovider import FakeProvider

    body = json.dumps(
        {
            "event": "egress_ended",
            "egress_id": "eg_42",
            "status": "EGRESS_COMPLETE",
            "storage_key": "recordings/room-x.mp4",
        }
    ).encode()
    parsed = FakeProvider().parse_webhook(body, "Bearer good")
    assert parsed["egress_id"] == "eg_42"
    assert parsed["status"] == "EGRESS_COMPLETE"
    assert parsed["storage_key"] == "recordings/room-x.mp4"
    # …and the additive keys read "this event carried none of that".
    assert parsed["room"] is None
    assert parsed["participant"] is None


def test_split_identity_handles_both_minted_forms_and_neither():
    from stapel_video.providers import split_identity

    assert split_identity(f"{ALICE}_{SESSION}") == (ALICE, SESSION)
    # A client_session_id may contain the separator; a UUID user id may not.
    assert split_identity(f"{ALICE}_a_b") == (ALICE, "a_b")
    # Pre-convention identity: it is its own connection.
    assert split_identity(ALICE) == (ALICE, ALICE)


# ── 2. Dispatch merge-registry ─────────────────────────────────────────────


def test_registry_carries_the_builtin_events():
    from stapel_video.webhooks import get_webhook_handlers

    handlers = get_webhook_handlers()
    assert set(handlers) == {
        "egress_ended",
        "egress_updated",
        "participant_joined",
        # 0.11.0: the builtin is now the composite in webhooks.py — a
        # departure closes the presence span AND ends a 1:1 call — and
        # room_finished, dropped on the floor for five releases, is the
        # independent witness a call needs when a participant_left is lost.
        "participant_left",
        "room_finished",
    }


def test_host_overlay_adds_an_event_without_restating_the_builtins(settings):
    from stapel_video.webhooks import get_webhook_handler, get_webhook_handlers

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "WEBHOOK_HANDLERS": {"room_finished": "stapel_video.presence._now"},
    }
    handlers = get_webhook_handlers()
    assert "room_finished" in handlers
    assert get_webhook_handler("participant_left") is not None


def test_host_overlay_can_tombstone_a_builtin(settings):
    from stapel_video.webhooks import get_webhook_handler

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "WEBHOOK_HANDLERS": {"egress_ended": None},
    }
    assert get_webhook_handler("egress_ended") is None
    assert get_webhook_handler("participant_joined") is not None


def test_runtime_registration_wins_and_is_reachable_through_the_ingress(api_client):
    from stapel_video.webhooks import (
        register_webhook_handler,
        unregister_webhook_handler,
    )

    seen = []
    register_webhook_handler("room_finished", seen.append)
    try:
        resp = _post(api_client, _event("room_finished", at=T0, identity=None))
        assert resp.status_code == 200
        assert len(seen) == 1
        assert seen[0]["room"]["name"] == ROOM
    finally:
        unregister_webhook_handler("room_finished")


def test_unhandled_event_is_a_quiet_200(api_client):
    # A 4xx would make the provider retry a delivery that was perfectly
    # correct; most of what a media server sends is not addressed to us.
    resp = _post(api_client, _event("track_published", at=T0))
    assert resp.status_code == 200
    assert ParticipantSpan.objects.count() == 0


def test_broken_overlay_entry_is_a_boot_error_not_a_500(api_client, settings):
    from django.core.checks import Error, run_checks

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "WEBHOOK_HANDLERS": {"room_finished": "stapel_video.nope.missing"},
    }
    ids = [m.id for m in run_checks() if isinstance(m, Error)]
    assert "stapel_video.E010" in ids
    # …and a live webhook still answers, rather than 500-ing over a typo.
    assert _post(api_client, _event("room_finished", at=T0)).status_code == 200


# ── 3. Ingest: idempotency, ordering, append-only ──────────────────────────


def test_joined_then_left_records_one_closed_span(api_client, presence_events):
    _post(api_client, _event("participant_joined", at=T0))
    _post(api_client, _event("participant_left", at=T0 + timedelta(minutes=30)))

    span = ParticipantSpan.objects.get()
    assert span.room_key == ROOM
    assert span.user_id == ALICE
    assert span.connection_id == SESSION
    assert span.joined_at == T0
    assert span.left_at == T0 + timedelta(minutes=30)
    assert span.close_reason == SpanCloseReason.WEBHOOK

    topics = [e.event_type for e in presence_events]
    assert topics == ["video.participant.joined", "video.participant.left"]
    left = presence_events[1].payload
    assert left["duration_seconds"] == 1800
    assert left["close_reason"] == "webhook"
    # Ids only: no display name reaches the bus even though the webhook had one.
    assert "Alice" not in json.dumps(left)


def test_redelivered_joined_does_not_open_a_second_span(api_client, presence_events):
    """At-least-once delivery: the same arrival can arrive twice."""
    for _ in range(3):
        _post(api_client, _event("participant_joined", at=T0))

    assert ParticipantSpan.objects.count() == 1
    assert len(presence_events) == 1


def test_redelivered_left_does_not_restate_the_duration(api_client):
    _post(api_client, _event("participant_joined", at=T0))
    _post(api_client, _event("participant_left", at=T0 + timedelta(minutes=10)))
    _post(api_client, _event("participant_left", at=T0 + timedelta(minutes=99)))

    span = ParticipantSpan.objects.get()
    assert span.left_at == T0 + timedelta(minutes=10)


def test_left_before_joined_materializes_the_whole_stay(api_client, presence_events):
    """Out of order, and the ordinary case when a join webhook is lost."""
    _post(api_client, _event("participant_left", at=T0 + timedelta(minutes=5)))

    span = ParticipantSpan.objects.get()
    assert span.joined_at == T0
    assert span.left_at == T0 + timedelta(minutes=5)
    assert span.close_reason == SpanCloseReason.WEBHOOK
    assert [e.event_type for e in presence_events] == [
        "video.participant.joined",
        "video.participant.left",
    ]

    # The late twin collides on (connection_id, joined_at) and changes nothing.
    _post(api_client, _event("participant_joined", at=T0))
    assert ParticipantSpan.objects.count() == 1
    assert ParticipantSpan.objects.get().left_at == T0 + timedelta(minutes=5)


def test_a_closed_span_is_never_reopened_by_a_later_close():
    span, _ = presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id=SESSION, joined_at=T0
    )
    assert presence.close_span(span, at=T0 + timedelta(minutes=1), reason="webhook")
    assert not presence.close_span(span, at=T0 + timedelta(hours=9), reason="sweeper")

    span.refresh_from_db()
    assert span.left_at == T0 + timedelta(minutes=1)
    assert span.close_reason == SpanCloseReason.WEBHOOK


def test_a_return_opens_a_new_span_instead_of_reopening_the_old_one(api_client):
    """The grace-rejoin defect, structurally impossible here: the previous
    interval is a row, not a nullable column somebody resets."""
    _post(api_client, _event("participant_joined", at=T0))
    _post(api_client, _event("participant_left", at=T0 + timedelta(minutes=5)))
    back = T0 + timedelta(minutes=6)
    _post(api_client, _event("participant_joined", at=back, joined_at=back))
    _post(
        api_client,
        _event(
            "participant_left", at=back + timedelta(minutes=4), joined_at=back
        ),
    )

    spans = list(ParticipantSpan.objects.order_by("joined_at"))
    assert len(spans) == 2
    assert [int((s.left_at - s.joined_at).total_seconds()) for s in spans] == [300, 240]


def test_departure_before_arrival_is_clamped_not_negative():
    span, _ = presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id=SESSION, joined_at=T0
    )
    presence.close_span(span, at=T0 - timedelta(minutes=5), reason="webhook")
    span.refresh_from_db()
    assert span.left_at == span.joined_at


def test_a_webhook_with_no_room_records_nothing(api_client):
    _post(api_client, _event("participant_joined", at=T0, room=None))
    assert ParticipantSpan.objects.count() == 0


def test_explicit_close_only_touches_open_spans():
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id=SESSION, joined_at=T0
    )
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="sess-b", joined_at=T0
    )
    closed = presence.close_spans_explicitly(
        room_key=ROOM, user_id=ALICE, at=T0 + timedelta(minutes=3)
    )
    assert closed == 2
    assert presence.close_spans_explicitly(room_key=ROOM, user_id=ALICE) == 0
    assert set(
        ParticipantSpan.objects.values_list("close_reason", flat=True)
    ) == {SpanCloseReason.EXPLICIT}


def test_erasure_pseudonymizes_the_meter_and_keeps_the_arithmetic():
    from stapel_video.gdpr import VideoGDPRProvider

    presence.open_span(
        room_key=ROOM,
        user_id=ALICE,
        connection_id=SESSION,
        joined_at=T0,
        closed_at=T0 + timedelta(minutes=10),
        close_reason="webhook",
    )
    exported = VideoGDPRProvider().export(ALICE)
    assert len(exported["presence_spans"]) == 1

    VideoGDPRProvider().delete(ALICE)
    span = ParticipantSpan.objects.get()
    assert span.user_id.startswith("erased:")
    assert span.user_id != ALICE
    # The interval — the thing a licence is counted from — is untouched.
    assert int((span.left_at - span.joined_at).total_seconds()) == 600
