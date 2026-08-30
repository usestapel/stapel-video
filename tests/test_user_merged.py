"""A merge is not a delete — the other half of an account's life cycle.

stapel-auth folds an anonymous guest into an existing account on sign-in and
then deletes the guest row. ``Room.created_by`` and ``RoomParticipant.user``
are ``CASCADE``, so before ``user.merged`` was consumed a visitor who opened
a call before signing in lost the room the instant they signed in — silently,
with no erasure ever requested for it. ``ParticipantSpan`` survived the
deletion and was worse off: it kept metering one human as two billable
people.

These tests pin the three models the handler re-points, the meter rule (every
second survives, only the per-person figures move), the duplicate rule that
keeps ``video_participant_uniq`` satisfiable, and the ways a payload can be
wrong without becoming a poison pill.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stapel_core.comm import action_registry

from stapel_video import services
from stapel_video.actions import MergeTargetNotReady, handle_user_merged
from stapel_video.models import ParticipantSpan, ParticipantStatus, Room, RoomParticipant
from stapel_video.presence import open_span

T0 = datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc)


def _event(**payload):
    return SimpleNamespace(payload=payload, event_id="evt-merge-1", service="auth")


def _guest_trail(guest, host):
    """One row in every table the merge carries over.

    A room the guest hosted, an admission to somebody else's room, and two
    metered spans — one closed, one still open, because the person is
    typically mid-call at the instant they sign in.
    """
    mine = services.create_room(guest, access_level="public")
    theirs = services.create_room(host, access_level="public")
    RoomParticipant.objects.create(
        room=theirs, user=guest, status=ParticipantStatus.ADMITTED
    )
    open_span(
        room_key="room-guest",
        user_id=str(guest.id),
        connection_id="conn-closed",
        joined_at=T0,
        closed_at=T0 + timedelta(minutes=30),
        close_reason="explicit",
    )
    open_span(
        room_key="room-guest",
        user_id=str(guest.id),
        connection_id="conn-open",
        joined_at=T0 + timedelta(minutes=40),
    )
    return mine, theirs


@pytest.fixture
def survivor(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="survivor", email="survivor@example.com", password="x"
    )


@pytest.mark.django_db
class TestSubscription:
    def test_user_merged_is_subscribed(self):
        """The pair the lifecycle check reads: this module answers both."""
        assert action_registry.handlers("user.deleted")
        assert handle_user_merged in action_registry.handlers("user.merged")

    def test_the_lifecycle_pair_check_is_green(self):
        """``stapel_core.lifecycle.E001`` with this app loaded and ready().

        The ``user.deleted`` half is a closure stapel-core subscribes on this
        library's behalf from ``register_gdpr_owner``; core stamps it with
        this module's name, so the pair is charged here and not to core. If
        that stamp ever stops working this assertion turns red in THIS repo,
        which is where somebody can act on it.
        """
        from stapel_core.comm.lifecycle_checks import check_lifecycle_pairs

        assert check_lifecycle_pairs() == []


@pytest.mark.django_db
class TestReparenting:
    def test_rooms_admissions_and_the_meter_land_on_the_survivor(
        self, user, other_user, survivor
    ):
        mine, theirs = _guest_trail(user, other_user)

        handle_user_merged(
            _event(from_user_id=str(user.id), into_user_id=str(survivor.id))
        )

        assert Room.objects.get(id=mine.id).created_by_id == survivor.id
        assert not Room.objects.filter(created_by_id=user.id).exists()
        # The admission moves; the host's room stays the host's.
        assert RoomParticipant.objects.filter(room=theirs, user=survivor).exists()
        assert not RoomParticipant.objects.filter(user_id=user.id).exists()
        assert Room.objects.get(id=theirs.id).created_by_id == other_user.id
        # Both spans — the closed one and the one still open.
        assert ParticipantSpan.objects.filter(user_id=str(survivor.id)).count() == 2
        assert not ParticipantSpan.objects.filter(user_id=str(user.id)).exists()

    def test_the_meter_keeps_every_second_it_had(self, user, other_user, survivor):
        """The ledger rule, in the shape a merge needs it.

        Re-keying is not a restatement of time: the same spans, the same
        rooms, the same instants. What collapses is the count of *people*,
        and it collapses onto the truth — the guest and the survivor were
        never two people.
        """
        _guest_trail(user, other_user)
        before = sorted(
            ParticipantSpan.objects.values_list(
                "room_key", "connection_id", "joined_at", "left_at"
            )
        )

        handle_user_merged(
            _event(from_user_id=str(user.id), into_user_id=str(survivor.id))
        )

        after = sorted(
            ParticipantSpan.objects.values_list(
                "room_key", "connection_id", "joined_at", "left_at"
            )
        )
        assert before == after
        assert ParticipantSpan.objects.values("user_id").distinct().count() == 1

    def test_an_erased_span_stays_erased(self, user, other_user, survivor):
        """Pseudonymized rows carry no original id, so no merge can un-erase one."""
        from stapel_video.presence import pseudonymize_user

        _guest_trail(user, other_user)
        pseudonymize_user(str(user.id))

        handle_user_merged(
            _event(from_user_id=str(user.id), into_user_id=str(survivor.id))
        )

        assert not ParticipantSpan.objects.filter(user_id=str(survivor.id)).exists()
        assert ParticipantSpan.objects.filter(user_id__startswith="erased:").count() == 2

    def test_a_duplicate_admission_keeps_the_survivors_own_row(
        self, user, other_user, survivor
    ):
        """One room, both accounts: ``video_participant_uniq`` allows one row."""
        room = services.create_room(other_user, access_level="public")
        RoomParticipant.objects.create(
            room=room, user=survivor, status=ParticipantStatus.ADMITTED
        )
        RoomParticipant.objects.create(
            room=room, user=user, status=ParticipantStatus.WAITING
        )

        handle_user_merged(
            _event(from_user_id=str(user.id), into_user_id=str(survivor.id))
        )

        rows = RoomParticipant.objects.filter(room=room, user=survivor)
        assert rows.count() == 1
        assert rows.get().status == ParticipantStatus.ADMITTED
        assert not RoomParticipant.objects.filter(room=room, user_id=user.id).exists()

    def test_redelivery_changes_nothing_further(self, user, other_user, survivor):
        """Delivery is at-least-once; the second run is the idempotency path."""
        _guest_trail(user, other_user)
        payload = _event(from_user_id=str(user.id), into_user_id=str(survivor.id))

        handle_user_merged(payload)
        snapshot = (
            sorted(Room.objects.values_list("id", "created_by_id")),
            sorted(RoomParticipant.objects.values_list("id", "room_id", "user_id")),
            sorted(ParticipantSpan.objects.values_list("id", "user_id")),
        )

        handle_user_merged(payload)

        assert snapshot == (
            sorted(Room.objects.values_list("id", "created_by_id")),
            sorted(RoomParticipant.objects.values_list("id", "room_id", "user_id")),
            sorted(ParticipantSpan.objects.values_list("id", "user_id")),
        )

    def test_a_guest_this_module_never_saw_is_a_quiet_no_op(
        self, user, other_user, survivor
    ):
        _guest_trail(user, other_user)
        before = Room.objects.count()

        handle_user_merged(
            _event(from_user_id=str(uuid.uuid4()), into_user_id=str(survivor.id))
        )

        assert Room.objects.count() == before
        assert not Room.objects.filter(created_by_id=survivor.id).exists()
        assert not ParticipantSpan.objects.filter(user_id=str(survivor.id)).exists()

    def test_a_guest_with_only_metered_time_needs_no_survivor_row(
        self, user, other_user
    ):
        """The meter is a CharField, not an FK — it has nothing to point at.

        A guest whose only trace here is metered time must still be merged,
        and must not be held up waiting for a user projection the meter never
        needed.
        """
        ghost = str(uuid.uuid4())
        absent_survivor = str(uuid.uuid4())
        open_span(
            room_key="room-ghost",
            user_id=ghost,
            connection_id="conn-ghost",
            joined_at=T0,
            closed_at=T0 + timedelta(minutes=5),
            close_reason="explicit",
        )

        handle_user_merged(
            _event(from_user_id=ghost, into_user_id=absent_survivor)
        )

        assert ParticipantSpan.objects.filter(user_id=absent_survivor).count() == 1

    def test_merging_an_account_into_itself_does_nothing(self, user, other_user):
        mine, _ = _guest_trail(user, other_user)

        handle_user_merged(
            _event(from_user_id=str(user.id), into_user_id=str(user.id))
        )

        assert Room.objects.get(id=mine.id).created_by_id == user.id
        assert ParticipantSpan.objects.filter(user_id=str(user.id)).count() == 2


@pytest.mark.django_db
class TestOrderingAndPoison:
    def test_rooms_to_move_and_no_survivor_yet_asks_for_a_redelivery(
        self, user, other_user
    ):
        """Not a no-op: returning success would lose the rooms for good."""
        _guest_trail(user, other_user)
        absent = str(uuid.uuid4())

        with pytest.raises(MergeTargetNotReady):
            handle_user_merged(
                _event(from_user_id=str(user.id), into_user_id=absent)
            )

        # Nothing half-moved — the meter update is inside the same
        # transaction and rolls back with the rest.
        assert Room.objects.filter(created_by_id=user.id).count() == 1
        assert ParticipantSpan.objects.filter(user_id=str(user.id)).count() == 2

    def test_a_malformed_id_is_logged_and_dropped(self, user, other_user, survivor):
        """``UUIDField`` raises ``ValidationError``, which is not a ``ValueError``.

        An escaping exception is a poison pill: no redelivery can fix a typo,
        so the bus would replay it until it gives up.
        """
        _guest_trail(user, other_user)

        handle_user_merged(
            _event(from_user_id="not-a-uuid", into_user_id=str(survivor.id))
        )
        handle_user_merged(
            _event(from_user_id=str(user.id), into_user_id="not-a-uuid")
        )

        assert Room.objects.filter(created_by_id=user.id).count() == 1
        assert ParticipantSpan.objects.filter(user_id=str(user.id)).count() == 2

    def test_a_payload_missing_an_id_does_not_raise(self, user, other_user, survivor):
        _guest_trail(user, other_user)

        handle_user_merged(_event(into_user_id=str(survivor.id)))
        handle_user_merged(_event(from_user_id=str(user.id)))
        handle_user_merged(_event())

        assert Room.objects.filter(created_by_id=user.id).count() == 1
