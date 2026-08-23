"""The Art. 15 export, and the registry seam onto the Art. 17 erasure.

The erasure itself is not here: it lives in :mod:`stapel_video.erasure`, as
one function the in-process registry (below) and the comm subscribers
registered in ``apps.ready()`` both reach. Two callers, one implementation —
a monolith and a fleet erase the same rows the same way, and there is no
second erasure to drift.

What it does, unchanged by the move:

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

from .erasure import erase_subject


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
        """Erase the subject — the same operation the comm path runs.

        The registry reaches the erasure here; the
        ``gdpr.erasure.requested`` subscriber registered in ``apps.ready()``
        reaches it there. A host that runs both in one process erases once:
        whichever path arrives second finds nothing left to match and
        receipts its zeroes, and this one receipts nothing at all — the
        orchestrator records the local pass itself.
        """
        erase_subject("account", user_id)

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
