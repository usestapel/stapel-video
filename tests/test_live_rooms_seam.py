"""The LIVE_ROOMS_PROVIDER seam, and the check that keeps it honest.

Two things are under test and they are the same thing twice. First: a host
that runs its OWN rooms over the VideoProvider seam can still get the
``profile.changed`` rename, because "which calls is this person in" is
configuration rather than a hardcoded read of this module's tables. Second:
a host that installs the module, leaves the seam at its default and never
writes those tables is stopped at boot — because otherwise the subscriber
answers "no live rooms" for everybody, forever, and empty-because-
unconfigured looks exactly like empty-because-nobody-is-on-a-call.
"""
from types import SimpleNamespace

import pytest
from django.test import override_settings

from stapel_video import checks, services
from stapel_video.actions import handle_profile_changed
from stapel_video.live_rooms import (
    DefaultLiveRoomsProvider,
    get_live_rooms_provider,
)
from stapel_video.models import ParticipantStatus, RoomParticipant

from .fakeliverooms import HostRoomsProvider
from .fakeprovider import FakeProvider

FAKE = "tests.fakeprovider.FakeProvider"
HOST_ROOMS = "tests.fakeliverooms.HostRoomsProvider"
UNMOUNTED = "tests.urls_unmounted"


@pytest.fixture(autouse=True)
def reset_fake():
    FakeProvider.renames = []
    HostRoomsProvider.refs = []
    yield
    FakeProvider.renames = []
    HostRoomsProvider.refs = []


def _event(user_id, display_name="New Name"):
    return SimpleNamespace(
        payload={"user_id": str(user_id), "display_name": display_name},
        event_id="e1",
    )


# ── the default: this module's own tables ───────────────────────────────────


@pytest.mark.django_db
def test_the_default_reads_this_modules_own_admitted_participants(user, other_user):
    room = services.create_room(other_user, access_level="public")
    RoomParticipant.objects.create(
        room=room, user=user, status=ParticipantStatus.ADMITTED
    )
    assert DefaultLiveRoomsProvider().live_rooms_for_user(user.id) == [
        room.provider_room_ref
    ]


@pytest.mark.django_db
def test_the_default_skips_the_lobby_and_the_departed(user, other_user):
    from django.utils import timezone

    waiting = services.create_room(other_user, access_level="public")
    RoomParticipant.objects.create(
        room=waiting, user=user, status=ParticipantStatus.WAITING
    )
    left = services.create_room(other_user, access_level="public")
    RoomParticipant.objects.create(
        room=left,
        user=user,
        status=ParticipantStatus.ADMITTED,
        left_at=timezone.now(),
    )
    assert DefaultLiveRoomsProvider().live_rooms_for_user(user.id) == []


# ── the seam: a host's own rooms reach the subscriber ───────────────────────


@pytest.mark.django_db
@override_settings(
    STAPEL_VIDEO={
        "VIDEO_PROVIDER": FAKE,
        "LIVE_ROOMS_PROVIDER": HOST_ROOMS,
    }
)
def test_a_rename_reaches_rooms_this_module_has_never_heard_of(user):
    # The host's own Room table. No stapel_video row exists for any of this,
    # which is the entire point: provider-only adoption bought the capability
    # and left the CALLING of it as a prose obligation.
    HostRoomsProvider.refs = ["host-room-1", "host-room-2"]

    handle_profile_changed(_event(user.id))

    assert {ref for ref, _, _ in FakeProvider.renames} == {
        "host-room-1",
        "host-room-2",
    }


@override_settings(
    STAPEL_VIDEO={
        "VIDEO_PROVIDER": FAKE,
        "LIVE_ROOMS_PROVIDER": HOST_ROOMS,
    }
)
def test_the_seam_resolves_through_conf_not_an_import():
    assert isinstance(get_live_rooms_provider(), HostRoomsProvider)


@pytest.mark.django_db
@override_settings(
    STAPEL_VIDEO={
        "VIDEO_PROVIDER": FAKE,
        "LIVE_ROOMS_PROVIDER": HOST_ROOMS,
    }
)
def test_an_empty_ref_is_never_pushed(user):
    # A host row with no provider ref was never connected to anything.
    HostRoomsProvider.refs = ["", None, "host-room-1"]
    handle_profile_changed(_event(user.id))
    assert [ref for ref, _, _ in FakeProvider.renames] == ["host-room-1"]


# ── the guard ───────────────────────────────────────────────────────────────


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE})
def test_the_default_seam_passes_when_this_modules_urls_are_mounted():
    # The test urlconf mounts them — which is exactly the deployment shape the
    # default is correct for.
    assert checks.check_live_rooms_provider(None) == []
    assert checks.check_live_rooms_source_is_writable(None) == []


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE}, ROOT_URLCONF=UNMOUNTED)
def test_the_default_seam_is_an_error_when_nothing_can_write_its_tables():
    # A provider-only freeloader: module installed, seam untouched, this
    # module's join endpoint nowhere in the URLconf. Nothing in this process
    # can ever populate what the default reads.
    errors = checks.check_live_rooms_source_is_writable(None)
    assert errors and errors[0].id == "stapel_video.E008"
    message = errors[0].msg
    # Both remedies must be named — an error that only says "wrong" is a
    # riddle.
    assert "LIVE_ROOMS_PROVIDER" in message
    assert "stapel_video.urls" in message


@override_settings(
    STAPEL_VIDEO={
        "VIDEO_PROVIDER": FAKE,
        "LIVE_ROOMS_PROVIDER": HOST_ROOMS,
    },
    ROOT_URLCONF=UNMOUNTED,
)
def test_a_correctly_adapted_host_passes_without_mounting_anything():
    assert checks.check_live_rooms_source_is_writable(None) == []


@override_settings(
    STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE, "LIVE_ROOMS_PROVIDER": "stapel_video.nope.Missing"}
)
def test_an_unimportable_seam_is_an_error():
    errors = checks.check_live_rooms_provider(None)
    assert errors and errors[0].id == "stapel_video.E006"


@override_settings(
    STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE, "LIVE_ROOMS_PROVIDER": "stapel_video.models.Room"}
)
def test_a_seam_that_is_not_a_live_rooms_provider_is_an_error():
    errors = checks.check_live_rooms_provider(None)
    assert errors and errors[0].id == "stapel_video.E007"
