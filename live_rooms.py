"""live-rooms provider — "which calls is this person sitting in right now?"

The one question a subscriber has to answer before it can push anything into a
running call, and the one question this library cannot answer for a host that
runs its own rooms. ``handle_profile_changed`` needs a list of provider room
refs for a user id; reading ``stapel_video``'s own tables answers it for a host
that uses this module's room lifecycle, and answers it *silently wrong* — an
empty list, forever — for a host that adopted the provider seam and kept its
own Room model. That failure has no symptom: nobody is renamed, and nothing
says so.

So the answer is configuration, like ``VIDEO_PROVIDER`` and ``SCOPE_PROVIDER``:
a dotted path in ``STAPEL_VIDEO["LIVE_ROOMS_PROVIDER"]``, defaulting to this
module's own tables. ``checks.py`` then makes the trap non-silent — a
deployment where the default cannot possibly hold refuses to boot.

Exactly ONE method, deliberately. A second one ("who is in this room", "is this
user live") turns the seam into a shadow repository over Room/Participant — a
fourth abstraction next to the model, the DTO and the provider, that every host
then has to implement in full to get one subscriber working. A second
subscriber earns a second method when that subscriber exists, not before.
"""
from __future__ import annotations


class LiveRoomsProvider:
    """Contract for "which live rooms is this user in". Subclass and point
    ``STAPEL_VIDEO["LIVE_ROOMS_PROVIDER"]`` at it when the host — not this
    library — owns the room lifecycle."""

    def live_rooms_for_user(self, user_id) -> list:
        """Provider room refs of every call ``user_id`` is currently in.

        A ``provider_room_ref`` is what the host handed (or would hand) to
        ``VideoProvider.create_room`` / ``mint_join_token`` — for a LiveKit
        host that is the LiveKit room name, which is usually just the room's
        own join code. Return an empty list for someone who is not on a call:
        that is the ordinary answer, and the reason the default implementation
        being wrong is so quiet.

        Implementations must be cheap enough to run on every ``profile.changed``
        event and must not raise for an unknown user.
        """
        raise NotImplementedError


class DefaultLiveRoomsProvider(LiveRoomsProvider):
    """Reads this library's own tables: rooms where the user is an ADMITTED
    participant who has not left. Correct for a host that mounts this module's
    URL surface (its join endpoint is what writes those rows), and correct for
    nobody else — see ``checks.check_live_rooms_source_is_writable``."""

    def live_rooms_for_user(self, user_id) -> list:
        from .models import ParticipantStatus, Room

        return list(
            Room.objects.filter(
                participants__user_id=user_id,
                participants__status=ParticipantStatus.ADMITTED,
                participants__left_at__isnull=True,
            )
            .exclude(provider_room_ref="")
            .values_list("provider_room_ref", flat=True)
            .distinct()
        )


def get_live_rooms_provider() -> LiveRoomsProvider:
    """Resolve the configured provider (already import_string'd by conf)."""
    from .conf import video_settings

    provider = video_settings.LIVE_ROOMS_PROVIDER
    return provider() if isinstance(provider, type) else provider


__all__ = [
    "LiveRoomsProvider",
    "DefaultLiveRoomsProvider",
    "get_live_rooms_provider",
]
