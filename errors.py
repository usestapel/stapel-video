"""i18n error keys of stapel-video.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_400_INVALID_ACCESS_LEVEL = "error.400.video_invalid_access_level"
ERR_400_INVALID_WEBHOOK = "error.400.video_invalid_webhook"
ERR_403_NOT_ROOM_HOST = "error.403.video_not_room_host"
ERR_403_JOIN_DENIED = "error.403.video_join_denied"
ERR_403_NOT_ROOM_PARTICIPANT = "error.403.video_not_room_participant"
ERR_400_INVALID_USAGE_PERIOD = "error.400.video_invalid_usage_period"
ERR_404_ROOM_NOT_FOUND = "error.404.video_room_not_found"
ERR_404_PARTICIPANT_NOT_FOUND = "error.404.video_participant_not_found"
#: The uniform answer of the usage read: no such scope, no access to it, and
#: no calls in it are ONE response. A distinct 403 would confirm that a
#: guessed workspace id exists, which is the fact the key is protecting.
ERR_404_SCOPE_NOT_FOUND = "error.404.video_scope_not_found"

# ── 1:1 calls ──────────────────────────────────────────────────────────────
#: A call id names two people and the conversation they are having, so "no
#: such call" and "not your call" are ONE answer. A distinct 403 would confirm
#: that a guessed id is a real call between real people, which is the whole of
#: what there is to leak here.
ERR_404_CALL_NOT_FOUND = "error.404.video_call_not_found"
ERR_400_INVALID_CALLEE = "error.400.video_call_invalid_callee"
#: The CALL_AUTHORIZER refused this pair for this thread. Told only to
#: somebody who already knows both ends of the call they tried to place.
ERR_403_CALL_NOT_ALLOWED = "error.403.video_call_not_allowed"
ERR_409_CALL_BUSY = "error.409.video_call_busy"
ERR_409_CALL_STATE = "error.409.video_call_state"
ERR_503_CALL_PROVIDER = "error.503.video_call_provider_unavailable"

STAPEL_VIDEO_ERRORS = {
    ERR_400_INVALID_ACCESS_LEVEL: "access_level must be one of: public, scope_trusted, restricted",
    ERR_400_INVALID_WEBHOOK: "Invalid or unverifiable provider webhook",
    ERR_403_NOT_ROOM_HOST: "Only the room host may perform this action",
    ERR_403_JOIN_DENIED: "You were denied entry to this room",
    ERR_403_NOT_ROOM_PARTICIPANT: "Only a participant of this room may see this",
    ERR_400_INVALID_USAGE_PERIOD: "month must be YYYY-MM, months a positive integer, and tz an IANA time zone",
    ERR_404_ROOM_NOT_FOUND: "Room not found",
    ERR_404_PARTICIPANT_NOT_FOUND: "Waiting participant not found",
    ERR_404_SCOPE_NOT_FOUND: "Scope not found",
    ERR_404_CALL_NOT_FOUND: "Call not found",
    ERR_400_INVALID_CALLEE: "callee_id must name another user",
    ERR_403_CALL_NOT_ALLOWED: "You may not call this person",
    ERR_409_CALL_BUSY: "You are already on a call",
    ERR_409_CALL_STATE: "This call is no longer in a state that allows this",
    ERR_503_CALL_PROVIDER: "The video backend is unavailable right now",
}

register_service_errors(STAPEL_VIDEO_ERRORS)

__all__ = [
    "STAPEL_VIDEO_ERRORS",
    "ERR_400_INVALID_ACCESS_LEVEL",
    "ERR_400_INVALID_USAGE_PERIOD",
    "ERR_400_INVALID_WEBHOOK",
    "ERR_403_NOT_ROOM_HOST",
    "ERR_403_JOIN_DENIED",
    "ERR_403_NOT_ROOM_PARTICIPANT",
    "ERR_404_ROOM_NOT_FOUND",
    "ERR_404_PARTICIPANT_NOT_FOUND",
    "ERR_404_SCOPE_NOT_FOUND",
    "ERR_400_INVALID_CALLEE",
    "ERR_403_CALL_NOT_ALLOWED",
    "ERR_404_CALL_NOT_FOUND",
    "ERR_409_CALL_BUSY",
    "ERR_409_CALL_STATE",
    "ERR_503_CALL_PROVIDER",
]
