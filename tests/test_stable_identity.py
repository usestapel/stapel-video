"""``client_session_id`` survives the whole trip: HTTP body -> provider.

The defect this holds down is not cosmetic drift. A random identity per
connection means every page reload arrives at the vendor as a stranger, so the
pre-reload connection stays seated as a ghost tile until the vendor's own
disconnect timeout. It happens for every adopter on every reload, and it heals
itself a minute later — the same signature as the rename bug, which is exactly
why it survives being looked for.

A provider parameter no view forwards is that story in miniature, so these
tests go through the real endpoints rather than calling the provider.
"""
import pytest
from django.urls import reverse

from stapel_video import services

from .fakeprovider import FakeProvider

pytestmark = pytest.mark.django_db

SESSION = "a1b2c3d4e5f6"


@pytest.fixture(autouse=True)
def reset_mints(settings):
    # The dotted path must name the SAME module object this file imported, or
    # the class recording the mints is not the class asserted on.
    settings.STAPEL_VIDEO = {"VIDEO_PROVIDER": "tests.fakeprovider.FakeProvider"}
    FakeProvider.mints = []
    yield
    FakeProvider.mints = []


def _session_ids():
    return [mint[4] for mint in FakeProvider.mints]


def test_the_join_endpoint_forwards_the_session_id_to_the_provider(
    auth_client, user, other_user
):
    room = services.create_room(other_user, access_level="public")
    FakeProvider.mints = []

    resp = auth_client.post(
        reverse("video-room-join", args=[room.join_code]),
        {"client_session_id": SESSION},
        format="json",
    )

    assert resp.status_code == 200
    assert _session_ids() == [SESSION]


def test_two_joins_from_one_browser_mint_one_identity(auth_client, user, other_user):
    room = services.create_room(other_user, access_level="public")
    url = reverse("video-room-join", args=[room.join_code])
    FakeProvider.mints = []

    first = auth_client.post(url, {"client_session_id": SESSION}, format="json")
    second = auth_client.post(url, {"client_session_id": SESSION}, format="json")

    # Same identity on the reconnect is what lets the vendor evict the ghost.
    assert first.json()["token"] == second.json()["token"]


def test_a_join_without_a_session_id_still_works(auth_client, user, other_user):
    # Older clients keep the random-suffix behavior rather than breaking.
    room = services.create_room(other_user, access_level="public")
    FakeProvider.mints = []

    resp = auth_client.post(
        reverse("video-room-join", args=[room.join_code]), {}, format="json"
    )

    assert resp.status_code == 200
    assert _session_ids() == [None]


def test_the_create_endpoint_forwards_it_too(auth_client, user):
    # The creator's first token comes from create, not join: without this the
    # creator ghosts itself on its very first reload.
    resp = auth_client.post(
        reverse("video-rooms"), {"client_session_id": SESSION}, format="json"
    )

    assert resp.status_code == 201
    assert _session_ids() == [SESSION]


def test_the_avatar_travels_with_the_name(auth_client, user, other_user):
    # rename_participant echoes participant metadata back so a rename cannot
    # erase an avatar. That guard guarded nothing while no code path could set
    # one — the provider must at least be OFFERED the field.
    room = services.create_room(other_user, access_level="public")
    FakeProvider.mints = []

    auth_client.post(
        reverse("video-room-join", args=[room.join_code]), {}, format="json"
    )

    assert len(FakeProvider.mints[0]) == 5  # (ref, user_id, name, avatar, session)
    assert FakeProvider.mints[0][3] == ""  # no avatar on this user model
