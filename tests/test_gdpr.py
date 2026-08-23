"""GDPR: the Art. 15 export and the provider seam onto the erasure.

The erasure protocol itself — probe, receipt, idempotence, the meter that is
pseudonymized rather than deleted — lives in ``test_gdpr_owner.py``, beside
the one function every caller reaches.
"""
import pytest

from stapel_video import services
from stapel_video.gdpr import VideoGDPRProvider
from stapel_video.models import Room, RoomParticipant, ParticipantStatus

pytestmark = pytest.mark.django_db


def test_delete_removes_created_rooms_and_participations(user, other_user):
    # user hosts a room; other_user joins another user's room.
    mine = services.create_room(user, access_level="public")
    theirs = services.create_room(other_user, access_level="public")
    RoomParticipant.objects.create(
        room=theirs, user=user, status=ParticipantStatus.ADMITTED
    )

    VideoGDPRProvider().delete(user.id)

    # user's own room is gone (cascades to its participants).
    assert not Room.objects.filter(id=mine.id).exists()
    # user's participation in the other room is gone; the other room survives.
    assert Room.objects.filter(id=theirs.id).exists()
    assert not RoomParticipant.objects.filter(room=theirs, user=user).exists()


def test_export_shape(user):
    services.create_room(user, access_level="restricted")
    data = VideoGDPRProvider().export(user.id)
    assert "created_rooms" in data
    assert "participations" in data
    assert len(data["created_rooms"]) == 1
