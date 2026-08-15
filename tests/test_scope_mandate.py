"""F4 — "trusted member of the scope" meant "has an account".

``DefaultScopeProvider.is_member`` returned ``bool(user.is_authenticated)``,
and ``_should_auto_admit`` consumes exactly that for ``SCOPE_TRUSTED``. So a
registered account holding no mandate anywhere, in possession of a join code,
skipped the lobby entirely and was minted a live media token. "Trusted member
of a scope" and "has an account" are not the same sentence, and the shipped
provider said they were.

Two things ride along on the same flow, closed here because they are the same
hole seen from another side:

* ``join_room`` created the ``RoomParticipant`` row with ``get_or_create``
  BEFORE deciding admission, and ``consumers.py`` admits anyone holding such
  a row. A denied joiner therefore still got the lobby socket.
* ``RoomParticipantsView`` returned every participant's identity to any
  authenticated caller with a join code, and ``RoomDetailView`` returned
  ``scope_key`` — which ``dto.py`` documents as the workspace/org id.

Mutation-wise: put ``is_authenticated`` back in ``is_member`` and the first
test fails; move the ``get_or_create`` back above the decision and the second
group fails.
"""
import pytest

from stapel_core.comm import function_registry
from stapel_core.django.mandate import MANDATE_FUNCTION, MANDATE_RESULT_KEY
from stapel_video.models import ParticipantStatus, Room, RoomParticipant

pytestmark = pytest.mark.django_db


@pytest.fixture
def mandate_seam():
    state = {"has_mandate": False, "raises": None}

    def handler(payload):
        if state["raises"]:
            raise state["raises"]
        return {MANDATE_RESULT_KEY: state["has_mandate"]}

    function_registry.register(MANDATE_FUNCTION, handler)
    yield state
    function_registry._providers.pop(MANDATE_FUNCTION, None)


@pytest.fixture(autouse=True)
def _clear_mandate_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def trusted_room(auth_client):
    """A scope_trusted room with the lobby on, created by someone else."""
    resp = auth_client.post(
        "/video/api/v1/rooms",
        {"access_level": "scope_trusted", "admit_required": True},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    room = Room.objects.get(id=resp.data["room"]["id"])
    # A real scope key, so the leak tests below are about something.
    room.scope_key = "workspace-42"
    room.save(update_fields=["scope_key"])
    return room


@pytest.fixture
def outsider_client(other_user):
    """Its OWN client: ``auth_client`` re-authenticates the shared one, and a
    test whose "outsider" is really the room's creator proves nothing."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


def _join(client, room):
    return client.post(f"/video/api/v1/rooms/{room.join_code}/join", {}, format="json")


# ---------------------------------------------------------------------------
# The auto-admit
# ---------------------------------------------------------------------------


def test_a_mandate_less_account_is_not_a_trusted_scope_member(
    mandate_seam, outsider_client, trusted_room
):
    resp = _join(outsider_client, trusted_room)
    assert resp.status_code == 200, resp.data
    assert resp.data["status"] == "waiting"
    assert not resp.data.get("token"), "a lobby wait must mint no media token"


def test_a_mandated_account_still_auto_admits(
    mandate_seam, outsider_client, trusted_room
):
    mandate_seam["has_mandate"] = True
    resp = _join(outsider_client, trusted_room)
    assert resp.data["status"] == "admitted"
    assert resp.data["token"]


def test_could_not_ask_refuses_with_503_never_a_token(
    mandate_seam, outsider_client, trusted_room
):
    mandate_seam["raises"] = RuntimeError("workspaces is down")
    resp = _join(outsider_client, trusted_room)
    assert resp.status_code == 503, resp.data


def test_a_standalone_deployment_keeps_its_trusted_members(
    outsider_client, trusted_room
):
    """Nothing wired: nobody holds a mandate, so refusing every joiner would
    be a different bug. The system check warns instead."""
    resp = _join(outsider_client, trusted_room)
    assert resp.data["status"] == "admitted"


# ---------------------------------------------------------------------------
# The row that outran the decision
# ---------------------------------------------------------------------------


def test_a_waiting_joiner_is_not_yet_a_lobby_member(
    mandate_seam, outsider_client, trusted_room, other_user
):
    """``consumers.py`` admits anyone holding a RoomParticipant row. The row
    must therefore never say more than the admission decision did."""
    _join(outsider_client, trusted_room)
    row = RoomParticipant.objects.get(room=trusted_room, user=other_user)
    assert row.status == ParticipantStatus.WAITING


def test_a_denied_joiner_gets_no_row_from_the_attempt(
    mandate_seam, outsider_client, trusted_room, other_user
):
    """A denial that still writes the row hands out the lobby socket it just
    refused."""
    trusted_room.admit_required = True
    trusted_room.access_level = "restricted"
    trusted_room.save(update_fields=["admit_required", "access_level"])
    RoomParticipant.objects.filter(room=trusted_room, user=other_user).delete()
    RoomParticipant.objects.create(
        room=trusted_room, user=other_user, status=ParticipantStatus.DENIED
    )
    resp = _join(outsider_client, trusted_room)
    assert resp.status_code == 403, resp.data


def test_a_failed_admission_lookup_writes_nothing(
    mandate_seam, outsider_client, trusted_room, other_user
):
    """503 is a refusal. A refusal that left a participant row behind would
    have handed out the socket anyway."""
    mandate_seam["raises"] = RuntimeError("workspaces is down")
    _join(outsider_client, trusted_room)
    assert not RoomParticipant.objects.filter(
        room=trusted_room, user=other_user
    ).exists()


# ---------------------------------------------------------------------------
# The identities and the scope key on the same views
# ---------------------------------------------------------------------------


def test_a_non_participant_cannot_list_the_room_s_participants(
    mandate_seam, outsider_client, trusted_room
):
    resp = outsider_client.get(f"/video/api/v1/rooms/{trusted_room.join_code}/participants")
    assert resp.status_code == 403, resp.data


def test_a_participant_still_lists_them(mandate_seam, auth_client, trusted_room):
    resp = auth_client.get(f"/video/api/v1/rooms/{trusted_room.join_code}/participants")
    assert resp.status_code == 200, resp.data


def test_room_detail_does_not_hand_the_scope_key_to_a_non_participant(
    mandate_seam, outsider_client, trusted_room
):
    """``dto.py`` documents scope_key as the workspace/org id. A join code is
    not authority to learn which organization is meeting."""
    resp = outsider_client.get(f"/video/api/v1/rooms/{trusted_room.join_code}")
    assert resp.status_code == 200, resp.data
    assert resp.data["scope_key"] == ""


def test_room_detail_still_shows_the_scope_key_to_a_participant(
    mandate_seam, auth_client, trusted_room
):
    resp = auth_client.get(f"/video/api/v1/rooms/{trusted_room.join_code}")
    assert resp.data["scope_key"] == trusted_room.scope_key
