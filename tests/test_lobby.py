"""Host-only lobby admit/deny + denied-sticky behavior."""
import pytest

from stapel_video.models import ParticipantStatus, RoomParticipant

pytestmark = pytest.mark.django_db


def _restricted_room_with_waiter(api_client, host, guest):
    api_client.force_authenticate(user=host)
    code = api_client.post(
        "/video/api/v1/rooms", {"access_level": "restricted"}, format="json"
    ).data["room"]["join_code"]
    api_client.force_authenticate(user=guest)
    api_client.post(f"/video/api/v1/rooms/{code}/join", {}, format="json")
    pid = str(RoomParticipant.objects.get(room__join_code=code, user=guest).id)
    return code, pid


def test_host_admits_waiting_participant(api_client, user, other_user):
    code, pid = _restricted_room_with_waiter(api_client, user, other_user)

    api_client.force_authenticate(user=user)
    resp = api_client.post(
        f"/video/api/v1/rooms/{code}/lobby/admit", {"participant_id": pid}, format="json"
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["participant"]["status"] == "admitted"
    assert resp.data["token"]
    assert RoomParticipant.objects.get(id=pid).status == ParticipantStatus.ADMITTED


def test_host_denies_waiting_participant_and_denial_is_sticky(api_client, user, other_user):
    code, pid = _restricted_room_with_waiter(api_client, user, other_user)

    api_client.force_authenticate(user=user)
    resp = api_client.post(
        f"/video/api/v1/rooms/{code}/lobby/deny", {"participant_id": pid}, format="json"
    )
    assert resp.status_code == 200
    assert RoomParticipant.objects.get(id=pid).status == ParticipantStatus.DENIED

    # A denied guest re-joining stays denied (403), not resurrected to waiting.
    api_client.force_authenticate(user=other_user)
    resp = api_client.post(f"/video/api/v1/rooms/{code}/join", {}, format="json")
    assert resp.status_code == 403
    assert resp.data["localizable_error"] == "error.403.video_join_denied"


def test_non_host_cannot_admit(api_client, user, other_user):
    code, pid = _restricted_room_with_waiter(api_client, user, other_user)

    # The waiting guest is not the host — must not be able to admit anyone.
    api_client.force_authenticate(user=other_user)
    resp = api_client.post(
        f"/video/api/v1/rooms/{code}/lobby/admit", {"participant_id": pid}, format="json"
    )
    assert resp.status_code == 403
    assert resp.data["localizable_error"] == "error.403.video_not_room_host"


def test_admit_unknown_participant_404(api_client, user, other_user):
    code, _ = _restricted_room_with_waiter(api_client, user, other_user)
    api_client.force_authenticate(user=user)
    resp = api_client.post(
        f"/video/api/v1/rooms/{code}/lobby/admit",
        {"participant_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
        format="json",
    )
    assert resp.status_code == 404
    assert resp.data["localizable_error"] == "error.404.video_participant_not_found"
