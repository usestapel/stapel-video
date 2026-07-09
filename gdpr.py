"""GDPR data handler for stapel-video.

This module holds user PII: ``Room.created_by`` and ``RoomParticipant.user``.
Per the Stapel standard, a data-holding module subscribes to ``user.deleted``
and erases that data.

- Rooms the user created are hard-deleted (cascading to their participant
  rows). A room carries no third-party PII worth retaining once its host is
  gone, so deletion — not anonymization — is correct.
- The user's participations in *other* people's rooms are removed (their
  attendance is their PII), leaving those rooms intact for their hosts.
"""
from stapel_core.gdpr import GDPRProvider


class VideoGDPRProvider(GDPRProvider):
    section = "video"

    def export(self, user_id) -> dict:
        from .models import Room, RoomParticipant

        created = list(
            Room.objects.filter(created_by_id=user_id).values(
                "id", "join_code", "scope_key", "access_level", "created_at"
            )
        )
        participations = list(
            RoomParticipant.objects.filter(user_id=user_id).values(
                "room_id", "status", "role", "joined_at"
            )
        )
        return {
            "created_rooms": _serialize(created),
            "participations": _serialize(participations),
        }

    def delete(self, user_id) -> None:
        from .models import Room, RoomParticipant

        # Rooms the user created cascade to their participant rows.
        Room.objects.filter(created_by_id=user_id).delete()
        # Attendance in other users' rooms is this user's PII — remove it.
        RoomParticipant.objects.filter(user_id=user_id).delete()

    def anonymize(self, user_id) -> None:
        # Video rows carry no content that must be retained after deletion.
        pass


def _serialize(rows: list[dict]) -> list[dict]:
    return [
        {k: v.isoformat() if hasattr(v, "isoformat") else str(v) for k, v in row.items()}
        for row in rows
    ]
