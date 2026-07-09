"""i18n error keys of stapel-video.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_400_INVALID_ACCESS_LEVEL = "error.400.video_invalid_access_level"
ERR_400_INVALID_WEBHOOK = "error.400.video_invalid_webhook"
ERR_403_NOT_ROOM_HOST = "error.403.video_not_room_host"
ERR_403_JOIN_DENIED = "error.403.video_join_denied"
ERR_404_ROOM_NOT_FOUND = "error.404.video_room_not_found"
ERR_404_PARTICIPANT_NOT_FOUND = "error.404.video_participant_not_found"

STAPEL_VIDEO_ERRORS = {
    ERR_400_INVALID_ACCESS_LEVEL: "access_level must be one of: public, scope_trusted, restricted",
    ERR_400_INVALID_WEBHOOK: "Invalid or unverifiable provider webhook",
    ERR_403_NOT_ROOM_HOST: "Only the room host may perform this action",
    ERR_403_JOIN_DENIED: "You were denied entry to this room",
    ERR_404_ROOM_NOT_FOUND: "Room not found",
    ERR_404_PARTICIPANT_NOT_FOUND: "Waiting participant not found",
}

register_service_errors(STAPEL_VIDEO_ERRORS)

__all__ = [
    "STAPEL_VIDEO_ERRORS",
    "ERR_400_INVALID_ACCESS_LEVEL",
    "ERR_400_INVALID_WEBHOOK",
    "ERR_403_NOT_ROOM_HOST",
    "ERR_403_JOIN_DENIED",
    "ERR_404_ROOM_NOT_FOUND",
    "ERR_404_PARTICIPANT_NOT_FOUND",
]
