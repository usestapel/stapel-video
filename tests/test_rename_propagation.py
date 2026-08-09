"""``profile.changed`` reaches the calls a renamed person is already sitting in.

The regression these tests hold down: the display name is a claim inside the
join token, so it is frozen at the instant the connection was made. Everything
else in a deployment re-reads the name and is correct as soon as the write
commits; a video tile keeps rendering whatever it was handed at join. The
symptom is one person's tile showing an old name while everyone else looks
right — because everyone else happened to reconnect after the write.
"""
from types import SimpleNamespace

import pytest

from stapel_video import services
from stapel_video.actions import handle_profile_changed
from stapel_video.models import ParticipantStatus, RoomParticipant

from .fakeprovider import FakeProvider

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_provider(settings):
    settings.STAPEL_VIDEO = {
        "VIDEO_PROVIDER": "tests.fakeprovider.FakeProvider",
    }
    FakeProvider.renames = []
    yield
    FakeProvider.renames = []


def _event(user_id, display_name="New Name"):
    return SimpleNamespace(
        payload={"user_id": str(user_id), "display_name": display_name},
        event_id="e1",
    )


def _admit(room, user):
    return RoomParticipant.objects.create(
        room=room, user=user, status=ParticipantStatus.ADMITTED
    )


def test_rename_is_pushed_into_the_room_the_person_is_in(user, other_user):
    room = services.create_room(other_user, access_level="public")
    _admit(room, user)

    handle_profile_changed(_event(user.id))

    assert FakeProvider.renames == [
        (room.provider_room_ref, str(user.id), "New Name")
    ]


def test_rename_reaches_every_room_at_once(user, other_user):
    first = services.create_room(other_user, access_level="public")
    second = services.create_room(other_user, access_level="public")
    _admit(first, user)
    _admit(second, user)

    handle_profile_changed(_event(user.id))

    assert {ref for ref, _, _ in FakeProvider.renames} == {
        first.provider_room_ref,
        second.provider_room_ref,
    }


def test_nobody_is_renamed_for_a_call_already_left(user, other_user):
    from django.utils import timezone

    room = services.create_room(other_user, access_level="public")
    participant = _admit(room, user)
    participant.left_at = timezone.now()
    participant.save(update_fields=["left_at"])

    handle_profile_changed(_event(user.id))

    # Their next join mints a token with the new name; there is nothing live.
    assert FakeProvider.renames == []


def test_a_waiting_participant_is_not_pushed(user, other_user):
    # Still in the lobby: no provider connection exists to rename.
    room = services.create_room(other_user, access_level="public")
    RoomParticipant.objects.create(
        room=room, user=user, status=ParticipantStatus.WAITING
    )

    handle_profile_changed(_event(user.id))

    assert FakeProvider.renames == []


def test_a_person_in_no_room_costs_nothing(user):
    handle_profile_changed(_event(user.id))
    assert FakeProvider.renames == []


def test_a_cleared_name_propagates_as_the_empty_string(user, other_user):
    # Clearing IS a supported outcome upstream; it must not read as "no data,
    # skip" and leave the old name burned into the tile.
    room = services.create_room(other_user, access_level="public")
    _admit(room, user)

    handle_profile_changed(
        SimpleNamespace(payload={"user_id": str(user.id)}, event_id="e1")
    )

    assert FakeProvider.renames == [(room.provider_room_ref, str(user.id), "")]


def test_an_event_without_a_user_id_is_refused_not_guessed(user, other_user):
    room = services.create_room(other_user, access_level="public")
    _admit(room, user)

    handle_profile_changed(SimpleNamespace(payload={}, event_id="e1"))

    assert FakeProvider.renames == []


def test_one_unreachable_room_does_not_strand_the_others(user, other_user, monkeypatch):
    first = services.create_room(other_user, access_level="public")
    second = services.create_room(other_user, access_level="public")
    _admit(first, user)
    _admit(second, user)

    calls = []

    def _flaky(self, provider_room_ref, user_id, user_name):
        calls.append(provider_room_ref)
        if provider_room_ref == first.provider_room_ref:
            raise RuntimeError("livekit unreachable")
        return 1

    monkeypatch.setattr(FakeProvider, "rename_participant", _flaky)

    handle_profile_changed(_event(user.id))

    assert set(calls) == {first.provider_room_ref, second.provider_room_ref}


def test_a_token_only_provider_says_so_instead_of_pretending(
    user, other_user, monkeypatch, caplog
):
    room = services.create_room(other_user, access_level="public")
    _admit(room, user)

    def _unsupported(self, provider_room_ref, user_id, user_name):
        raise NotImplementedError

    monkeypatch.setattr(FakeProvider, "rename_participant", _unsupported)

    with caplog.at_level("WARNING"):
        handle_profile_changed(_event(user.id))

    assert "cannot push renames" in caplog.text


def test_the_consumer_is_registered_on_the_comm_plane():
    # The whole point is that installing the module is enough — a host that
    # never wires anything still gets the propagation.
    from stapel_core.comm import action_registry

    handlers = action_registry.handlers("profile.changed")
    assert any(h.__name__ == "handle_profile_changed" for h in handlers)
