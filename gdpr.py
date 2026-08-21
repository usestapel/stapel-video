"""GDPR data handler for stapel-video.

This module holds user PII: ``Room.created_by`` and ``RoomParticipant.user``.
Per the Stapel standard, a data-holding module subscribes to ``user.deleted``
and erases that data.

- Rooms the user created are hard-deleted (cascading to their participant
  rows). A room carries no third-party PII worth retaining once its host is
  gone, so deletion — not anonymization — is correct.
- The user's participations in *other* people's rooms are removed (their
  attendance is their PII), leaving those rooms intact for their hosts.
- Presence spans are **pseudonymized, not deleted**. A span holds no text to
  scrub, so what is personal about it is the ``user_id`` column itself, and
  rewriting it to a keyed digest removes the person while leaving the meter
  arithmetic exactly as it was: the same pseudonym for the same subject, so
  distinct counts and pair overlaps do not move. Deleting the rows instead
  would silently restate closed reporting periods — and the question the
  export answers is explicitly "who was in a call during the period", counted
  from spans and not from whether the account still exists. Same rule as the
  stapel-agent ledger (scrub the content, keep the counters), in the shape a
  table with no content needs it.
"""
from stapel_core.gdpr import GDPRProvider


class VideoGDPRProvider(GDPRProvider):
    section = "video"

    def export(self, user_id) -> dict:
        from .models import ParticipantSpan, Room, RoomParticipant

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
        # The meter is part of the subject's record: somebody asking what we
        # hold on them is owed the intervals a licence is counted from, not
        # only the admission rows.
        spans = list(
            ParticipantSpan.objects.filter(user_id=str(user_id)).values(
                "id",
                "room_key",
                "connection_id",
                "joined_at",
                "left_at",
                "close_reason",
            )
        )
        return {
            "created_rooms": _serialize(created),
            "participations": _serialize(participations),
            "presence_spans": _serialize(spans),
        }

    def delete(self, user_id) -> None:
        from .models import Room, RoomParticipant
        from .presence import pseudonymize_user

        # Rooms the user created cascade to their participant rows.
        Room.objects.filter(created_by_id=user_id).delete()
        # Attendance in other users' rooms is this user's PII — remove it.
        RoomParticipant.objects.filter(user_id=user_id).delete()
        # The meter keeps its arithmetic and loses the person (see the module
        # docstring). ParticipantSpan.user_id is a CharField precisely so this
        # is a decision here and not a cascade nobody chose.
        pseudonymize_user(user_id)

    def anonymize(self, user_id) -> None:
        from .presence import pseudonymize_user

        # Rooms and participations carry no content to redact; the meter's
        # one identifying column is the one thing there is to anonymize.
        pseudonymize_user(user_id)


def _serialize(rows: list[dict]) -> list[dict]:
    return [
        {k: v.isoformat() if hasattr(v, "isoformat") else str(v) for k, v in row.items()}
        for row in rows
    ]
