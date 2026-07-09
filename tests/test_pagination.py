"""Anchor-paginated participants listing (limit/offset is forbidden shelf-wide)."""
import pytest

from stapel_video import services
from stapel_video.models import ParticipantStatus, RoomParticipant

pytestmark = pytest.mark.django_db


def _room_with_participants(api_client, host, joiners):
    api_client.force_authenticate(user=host)
    code = api_client.post(
        "/video/api/rooms", {"access_level": "public"}, format="json"
    ).data["room"]["join_code"]
    for u in joiners:
        api_client.force_authenticate(user=u)
        api_client.post(f"/video/api/rooms/{code}/join", {}, format="json")
    return code


def test_participants_listing_is_anchor_paginated(api_client, django_user_model):
    host = django_user_model.objects.create_user(username="host", email="h@x.io", password="x")
    joiners = [
        django_user_model.objects.create_user(username=f"u{i}", email=f"u{i}@x.io", password="x")
        for i in range(4)
    ]
    code = _room_with_participants(api_client, host, joiners)
    # host + 4 joiners = 5 participants.

    api_client.force_authenticate(user=host)
    resp = api_client.get(f"/video/api/rooms/{code}/participants", {"limit": 2})
    assert resp.status_code == 200
    body = resp.data
    # Anchor-pagination envelope shape (mirrors core AnchorPagination).
    assert set(body) >= {"items", "next_anchor", "prev_anchor", "has_next", "has_prev", "count"}
    assert body["count"] == 2
    assert body["has_next"] is True
    assert body["next_anchor"]

    # Walk to the next page via the returned cursor (params dict URL-encodes it).
    resp2 = api_client.get(
        f"/video/api/rooms/{code}/participants",
        {"limit": 2, "anchor": body["next_anchor"]},
    )
    page1_ids = {p["id"] for p in body["items"]}
    page2_ids = {p["id"] for p in resp2.data["items"]}
    assert not (page1_ids & page2_ids), "pages must not overlap"

    # FIFO order: host (joined first) leads page one.
    assert body["items"][0]["role"] == "host"


def test_participants_listing_rejects_no_limit_offset_semantics(api_client, user):
    # There is simply no `offset` param — the whole set comes back anchored.
    api_client.force_authenticate(user=user)
    code = api_client.post(
        "/video/api/rooms", {"access_level": "public"}, format="json"
    ).data["room"]["join_code"]
    resp = api_client.get(f"/video/api/rooms/{code}/participants")
    assert resp.status_code == 200
    assert resp.data["count"] == 1
    assert resp.data["has_next"] is False


def test_participants_queryset_fifo(api_client, user, other_user):
    api_client.force_authenticate(user=user)
    code = api_client.post(
        "/video/api/rooms", {"access_level": "public"}, format="json"
    ).data["room"]["join_code"]
    api_client.force_authenticate(user=other_user)
    api_client.post(f"/video/api/rooms/{code}/join", {}, format="json")

    room = services.get_room(code)
    ordered = list(services.participants_queryset(room))
    assert [p.user_id for p in ordered] == [user.id, other_user.id]
    assert ordered[0].status == ParticipantStatus.ADMITTED
    assert RoomParticipant.objects.filter(room=room).count() == 2
