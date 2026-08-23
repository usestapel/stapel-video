"""stapel-video answers the erasure protocol — probe, erase, receipt, once.

The finding this closes was found on a live stand: stapel-video was a
declared data owner with no ``gdpr.owner.probe`` subscriber. Its in-process
provider really did erase and its old ``user.deleted`` handler really did
run, and the fleet's erasure still waited on this owner forever, because
liveness is answered by the subscriber that erases and there was none to
answer.

The erasure discipline itself is unchanged and pinned here: rooms and
admissions go, and the meter is **pseudonymized** — ``ParticipantSpan`` rows
survive with an unattributable ``user_id``, so a closed reporting period
still counts the same seconds.

``VALIDATE_SCHEMAS`` is on (``_codegen_settings``), so every receipt these
tests capture was validated against ``schemas/emits/gdpr.section.erased.json``
on the way out.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stapel_core.comm import action_registry
from stapel_core.gdpr import register_gdpr_owner, registered_gdpr_owners

from stapel_video import services
from stapel_video.erasure import OWNER, SUBJECT_TYPES, erase_subject
from stapel_video.models import ParticipantSpan, ParticipantStatus, Room, RoomParticipant
from stapel_video.presence import open_span

#: The registration ``apps.ready()`` made. Same terms means the helper hands
#: back the existing registration rather than subscribing twice, so this is
#: both how the tests reach the handlers and an assertion that ready() ran.
VIDEO_OWNER = register_gdpr_owner(OWNER, SUBJECT_TYPES, erase_subject)

T0 = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)


def _event(**payload):
    return SimpleNamespace(payload=payload, event_id="evt-1", service="gdpr")


def _trail(user, other_user):
    """One row in every table video touches, so a count can be wrong out loud.

    A room the subject hosts, an admission to somebody else's room, and two
    metered spans (the ledger half).
    """
    mine = services.create_room(user, access_level="public")
    theirs = services.create_room(other_user, access_level="public")
    RoomParticipant.objects.create(
        room=theirs, user=user, status=ParticipantStatus.ADMITTED
    )
    for n in (0, 1):
        open_span(
            room_key=f"room-{n}",
            user_id=str(user.id),
            connection_id=f"conn-{n}",
            joined_at=T0 + timedelta(minutes=n),
            closed_at=T0 + timedelta(minutes=n + 30),
            close_reason="explicit",
        )
    # Somebody else's stay in the same period — untouched, and the proof the
    # pseudonymization is keyed to one subject.
    open_span(
        room_key="room-0",
        user_id=str(other_user.id),
        connection_id="conn-other",
        joined_at=T0,
        closed_at=T0 + timedelta(minutes=30),
        close_reason="explicit",
    )
    return mine, theirs


@pytest.mark.django_db
class TestRegistration:
    """What ``apps.ready()`` put on the bus."""

    def test_the_erasure_and_probe_handlers_are_subscribed(self):
        assert action_registry.handlers("gdpr.erasure.requested")
        assert action_registry.handlers("gdpr.owner.probe")
        # The deprecated account signal, until stapel-gdpr 0.6.0 drops it.
        assert action_registry.handlers("user.deleted")

    def test_the_owner_claims_the_account_and_nothing_else(self):
        from stapel_video.gdpr import VideoGDPRProvider

        assert registered_gdpr_owners()["video"] == ("account",)
        # One name, or the receipts land on nobody's part.
        assert OWNER == "video" == VideoGDPRProvider.section


@pytest.mark.django_db
class TestProbe:
    """`video: alive=false` — the symptom this release exists to end."""

    def test_the_probe_is_answered_with_what_this_module_erases(self):
        correlation_id = str(uuid.uuid4())
        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_owner_probe(_event(correlation_id=correlation_id))

        name, payload = m_emit.call_args.args
        assert name == "gdpr.owner.alive"
        assert payload == {
            "owner": "video",
            "subject_types": ["account"],
            "correlation_id": correlation_id,
        }

    def test_the_probe_is_answered_even_without_a_correlation_id(self):
        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_owner_probe(_event())

        name, payload = m_emit.call_args.args
        assert name == "gdpr.owner.alive"
        assert payload == {"owner": "video", "subject_types": ["account"]}


@pytest.mark.django_db
class TestErasure:
    """The receipt says what was erased, and only what was erased."""

    def test_the_trail_goes_the_meter_stays_and_the_receipt_counts_both(
        self, user, other_user
    ):
        mine, theirs = _trail(user, other_user)
        correlation_id = str(uuid.uuid4())

        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_erasure_requested(_event(
                request_id=1,
                correlation_id=correlation_id,
                subject_type="account",
                subject_key=str(user.id),
            ))

        receipts = [
            call.args[1] for call in m_emit.call_args_list
            if call.args[0] == "gdpr.section.erased"
        ]
        assert len(receipts) == 1
        payload = receipts[0]
        assert payload["owner"] == "video"
        assert payload["subject_type"] == "account"
        assert payload["subject_key"] == str(user.id)
        assert payload["correlation_id"] == correlation_id
        assert payload["receipt_id"] == f"video:account:{user.id}:{correlation_id}"
        # presence_spans counts rows PSEUDONYMIZED, not removed.
        assert payload["counts"] == {
            "rooms": 1,
            "participations": 1,
            "presence_spans": 2,
        }

        assert not Room.objects.filter(id=mine.id).exists()
        assert not RoomParticipant.objects.filter(user_id=user.id).exists()
        # The other host's room is their record and survives.
        assert Room.objects.filter(id=theirs.id).exists()

    def test_the_meter_keeps_its_arithmetic_and_loses_the_person(
        self, user, other_user
    ):
        """Content is never deleted, counters never move — the ledger rule.

        Three spans before, three spans after; only the ids that NAME the
        erased subject changed, and they changed to ONE pseudonym, so a
        distinct-participant count over the period is still 2.
        """
        _trail(user, other_user)
        before = ParticipantSpan.objects.count()
        durations_before = sorted(
            (s.left_at - s.joined_at).total_seconds()
            for s in ParticipantSpan.objects.all()
        )

        with patch("stapel_core.comm.emit"):
            VIDEO_OWNER.handle_erasure_requested(_event(
                request_id=2,
                correlation_id="corr-meter",
                subject_type="account",
                subject_key=str(user.id),
            ))

        assert ParticipantSpan.objects.count() == before
        assert sorted(
            (s.left_at - s.joined_at).total_seconds()
            for s in ParticipantSpan.objects.all()
        ) == durations_before
        assert not ParticipantSpan.objects.filter(user_id=str(user.id)).exists()
        erased = set(
            ParticipantSpan.objects.filter(user_id__startswith="erased:")
            .values_list("user_id", flat=True)
        )
        assert len(erased) == 1  # one subject stays one subject
        # The other person's stay is untouched — the digest is keyed to an id.
        assert ParticipantSpan.objects.filter(user_id=str(other_user.id)).count() == 1
        assert len(
            set(ParticipantSpan.objects.values_list("user_id", flat=True))
        ) == 2

    def test_redelivery_erases_nothing_twice_and_mints_the_same_receipt(
        self, user, other_user
    ):
        _trail(user, other_user)
        event = _event(
            request_id=3,
            correlation_id="corr-redelivered",
            subject_type="account",
            subject_key=str(user.id),
        )

        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_erasure_requested(event)
            VIDEO_OWNER.handle_erasure_requested(event)

        receipts = [
            call.args[1] for call in m_emit.call_args_list
            if call.args[0] == "gdpr.section.erased"
        ]
        first, second = receipts
        assert first["counts"] == {"rooms": 1, "participations": 1, "presence_spans": 2}
        # A span already pseudonymized is not pseudonymized again — a second
        # digest for one subject would split their history in two.
        assert set(second["counts"].values()) == {0}
        assert first["receipt_id"] == second["receipt_id"]

    def test_an_unclaimed_subject_is_ignored_without_a_receipt(self, user, other_user):
        """gdpr creates a part only for owners that claim the type, so a
        receipt here would be answering for somebody else."""
        mine, _ = _trail(user, other_user)

        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_erasure_requested(_event(
                request_id=4,
                correlation_id="corr-workspace",
                subject_type="workspace",
                subject_key="ws-1",
            ))

        m_emit.assert_not_called()
        assert Room.objects.filter(id=mine.id).exists()
        assert erase_subject("workspace", "ws-1") is None

    def test_a_malformed_request_is_dropped_without_a_receipt(self, user, other_user):
        """A payload this shape will never parse; raising would redeliver it."""
        mine, _ = _trail(user, other_user)

        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_erasure_requested(_event(
                correlation_id="corr-broken", subject_type="account",
            ))

        m_emit.assert_not_called()
        assert Room.objects.filter(id=mine.id).exists()

    def test_the_deprecated_signal_runs_the_same_erasure(self, user, other_user):
        """user.deleted and gdpr.erasure.requested reach one implementation,
        so deleting the legacy handler deletes no erasure logic."""
        mine, _ = _trail(user, other_user)

        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_user_deleted(_event(
                user_id=str(user.id), correlation_id="corr-legacy",
            ))

        receipts = [
            call.args[1] for call in m_emit.call_args_list
            if call.args[0] == "gdpr.section.erased"
        ]
        assert len(receipts) == 1
        assert receipts[0]["counts"]["rooms"] == 1
        assert receipts[0]["counts"]["presence_spans"] == 2
        assert receipts[0]["user_id"] == str(user.id)
        assert not Room.objects.filter(id=mine.id).exists()


@pytest.mark.django_db
class TestBothPathsInOneProcess:
    """A monolith runs the in-process provider AND this subscriber.

    stapel-video is not the module that hosts stapel-gdpr, so the two paths
    cannot be driven end to end from this repo's test instance. What can be
    pinned here is the property the host depends on: one erasure, one receipt,
    and it is the subscriber's — the provider is a silent caller of the same
    function, so whichever path runs second finds nothing left to match and
    cannot overwrite the honest counts with its zeroes.
    """

    def test_the_in_process_provider_erases_and_receipts_nothing(
        self, user, other_user
    ):
        from stapel_video.gdpr import VideoGDPRProvider

        mine, _ = _trail(user, other_user)

        with patch("stapel_core.comm.emit") as m_emit:
            VideoGDPRProvider().delete(user.id)

        assert not any(
            call.args[0] == "gdpr.section.erased" for call in m_emit.call_args_list
        )
        assert not Room.objects.filter(id=mine.id).exists()
        assert not ParticipantSpan.objects.filter(user_id=str(user.id)).exists()

    def test_subscriber_then_provider_leaves_exactly_one_receipt(
        self, user, other_user
    ):
        from stapel_video.gdpr import VideoGDPRProvider

        _trail(user, other_user)
        correlation_id = str(uuid.uuid4())

        with patch("stapel_core.comm.emit") as m_emit:
            VIDEO_OWNER.handle_erasure_requested(_event(
                request_id=5,
                correlation_id=correlation_id,
                subject_type="account",
                subject_key=str(user.id),
            ))
            VideoGDPRProvider().delete(user.id)
            VIDEO_OWNER.handle_user_deleted(_event(user_id=str(user.id)))

        receipts = [
            call.args[1] for call in m_emit.call_args_list
            if call.args[0] == "gdpr.section.erased"
        ]
        assert len(receipts) == 1
        assert receipts[0]["counts"]["presence_spans"] == 2
        assert receipts[0]["correlation_id"] == correlation_id
