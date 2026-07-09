"""Room create + join lifecycle across the three access levels."""
import pytest
from django.test import override_settings

from stapel_video.models import ParticipantRole, ParticipantStatus, Room, RoomParticipant

pytestmark = pytest.mark.django_db

FAKE = "stapel_video.tests.fakeprovider.FakeProvider"


def _create(client, **body):
    return client.post("/video/api/rooms", body, format="json")


def test_create_room_auto_admits_creator_as_host(auth_client, user):
    resp = _create(auth_client, access_level="public")
    assert resp.status_code == 201, resp.data
    data = resp.data
    assert data["status"] == "admitted"
    assert data["token"], "creator should receive a join token"
    assert data["room"]["access_level"] == "public"
    assert data["participant"]["role"] == "host"

    room = Room.objects.get(id=data["room"]["id"])
    assert room.created_by_id == user.id
    assert room.provider_room_ref == f"fake-room::{room.join_code}"
    host = RoomParticipant.objects.get(room=room, user=user)
    assert host.status == ParticipantStatus.ADMITTED
    assert host.role == ParticipantRole.HOST


def test_create_room_rejects_bad_access_level(auth_client):
    resp = _create(auth_client, access_level="nonsense")
    assert resp.status_code == 400
    assert resp.data["localizable_error"] == "error.400.video_invalid_access_level"


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE, "DEFAULT_ACCESS_LEVEL": "public"})
def test_create_room_uses_default_access_level_axis(auth_client):
    resp = _create(auth_client)
    assert resp.status_code == 201
    assert resp.data["room"]["access_level"] == "public"


def test_public_room_join_auto_admits_stranger(api_client, user, other_user):
    api_client.force_authenticate(user=user)
    code = _create(api_client, access_level="public").data["room"]["join_code"]

    api_client.force_authenticate(user=other_user)
    resp = api_client.post(f"/video/api/rooms/{code}/join", {}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["status"] == "admitted"
    assert resp.data["token"]


def test_restricted_room_join_waits_in_lobby(api_client, user, other_user):
    api_client.force_authenticate(user=user)
    code = _create(api_client, access_level="restricted").data["room"]["join_code"]

    api_client.force_authenticate(user=other_user)
    resp = api_client.post(f"/video/api/rooms/{code}/join", {}, format="json")
    assert resp.status_code == 200
    assert resp.data["status"] == "waiting"
    assert resp.data["token"] is None
    p = RoomParticipant.objects.get(room__join_code=code, user=other_user)
    assert p.status == ParticipantStatus.WAITING
    assert p.role == ParticipantRole.GUEST


def test_restricted_room_host_rejoin_is_admitted(auth_client, user):
    code = _create(auth_client, access_level="restricted").data["room"]["join_code"]
    resp = auth_client.post(f"/video/api/rooms/{code}/join", {}, format="json")
    assert resp.data["status"] == "admitted"
    assert resp.data["token"]


@override_settings(STAPEL_VIDEO={
    "VIDEO_PROVIDER": FAKE,
    "SCOPE_PROVIDER": "stapel_video.tests.fakescope.UsernameScopeProvider",
})
def test_scope_trusted_admits_members_lobbies_outsiders(api_client, django_user_model):
    alice = django_user_model.objects.create_user(username="alice", email="a@x.io", password="x")
    carol = django_user_model.objects.create_user(username="carol", email="c@x.io", password="x")
    bob = django_user_model.objects.create_user(username="bob", email="b@x.io", password="x")

    api_client.force_authenticate(user=alice)
    code = _create(api_client, access_level="scope_trusted").data["room"]["join_code"]

    # carol is a scope member -> auto-admitted.
    api_client.force_authenticate(user=carol)
    resp = api_client.post(f"/video/api/rooms/{code}/join", {}, format="json")
    assert resp.data["status"] == "admitted"
    assert resp.data["token"]

    # bob is not a member -> lobby.
    api_client.force_authenticate(user=bob)
    resp = api_client.post(f"/video/api/rooms/{code}/join", {}, format="json")
    assert resp.data["status"] == "waiting"


def test_join_unknown_room_404(auth_client):
    resp = auth_client.post("/video/api/rooms/zzz-zzzz-zzz/join", {}, format="json")
    assert resp.status_code == 404
    assert resp.data["localizable_error"] == "error.404.video_room_not_found"


def test_room_info(auth_client):
    code = _create(auth_client, access_level="public").data["room"]["join_code"]
    resp = auth_client.get(f"/video/api/rooms/{code}")
    assert resp.status_code == 200
    assert resp.data["join_code"] == code
    assert resp.data["access_level"] == "public"


def test_join_code_shape(auth_client):
    code = _create(auth_client).data["room"]["join_code"]
    parts = code.split("-")
    assert [len(p) for p in parts] == [3, 4, 3]
    assert all(p.isalpha() and p.islower() for p in parts)


def test_endpoints_require_auth(api_client):
    assert api_client.post("/video/api/rooms", {}, format="json").status_code in (401, 403)
