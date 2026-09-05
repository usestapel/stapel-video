"""The line the call leaves in the conversation it hung off.

«Звонок · 3:12». «Пропущенный звонок». «Звонок отклонён». A call that leaves
no trace in the thread is a call the two people cannot refer to afterwards —
and in a marketplace the thread is the record of the deal.

Reached by NAME, not by import: ``CALL_THREAD_MESSAGE_FUNCTION`` (default
``chat.post_system_message``). Modules never import each other, and the fact
that stapel-chat happens to run in the same process on a monolith is a
deployment shape, not a licence.

**Why a marker key and not a rendered string.** The body carried here is
``video.call.ended:188``, not "Звонок · 3:12". A rendered string freezes one
language and one format into a row that outlives both: the reader may not be
the writer's locale, and «3:12» is a choice about how to show 188 seconds
that the front should be free to change. Chat's own system lines already work
this way (``chat.support.assigned``); this one carries its single argument
after a colon because a chat message has nowhere else to put it.

**Named gap:** a chat ``Message`` has no structured-parameter field, so the
duration rides in the body string. Closing it properly is a JSON column plus
its migration, the realtime payload, the DTO and three emit schemas — more
than this thread needs. The marker keeps its meaning if that field ever
arrives, so this is a compromise that can be retired rather than one that has
to be lived with.
"""
from __future__ import annotations

import logging

from .models import CallState

logger = logging.getLogger(__name__)

#: Body markers. The argument, where there is one, follows a colon.
MARKER_ENDED = "video.call.ended"
MARKER_MISSED = "video.call.missed"
MARKER_DECLINED = "video.call.declined"

#: Which finished states earn a line. ``failed`` deliberately earns none: the
#: callee was never told the call existed, and the caller was told by the 503
#: — a thread line about it would be the first either party heard of a call
#: that never happened.
_MARKERS = {
    CallState.ENDED: MARKER_ENDED,
    CallState.MISSED: MARKER_MISSED,
    CallState.DECLINED: MARKER_DECLINED,
}


def call_body(call) -> str:
    """The message body for a finished call, or "" when it deserves none."""
    marker = _MARKERS.get(call.state)
    if marker is None:
        return ""
    if marker == MARKER_ENDED:
        return f"{MARKER_ENDED}:{call.duration_seconds}"
    return marker


def post_call_line(call) -> bool:
    """Write the call's system line into its thread. Returns whether it went.

    Every "no" here is quiet and ordinary: a call with no thread, a
    deployment that turned the seam off, a state that has no line. Only a
    peer that was asked and failed is worth a log — and even that never
    reaches the caller, because by the time this runs the call is over and
    the ``Call`` row is the truth.

    Idempotent at the far end: ``client_msg_id`` is the call's own id, so an
    at-least-once redelivery writes one line, not two. This is the reason the
    function is worth having a narrow contract for rather than being a
    generic "post a message" — the idempotency key is a fact about the call.
    """
    from ..conf import video_settings

    if not call.thread_key:
        return False
    function_name = (video_settings.CALL_THREAD_MESSAGE_FUNCTION or "").strip()
    if not function_name:
        return False
    body = call_body(call)
    if not body:
        return False

    from stapel_core.comm import call as comm_call

    try:
        comm_call(
            function_name,
            {
                "conversation_id": str(call.thread_key),
                "body": body,
                "client_msg_id": f"video-call-{call.id}",
            },
        )
    except Exception:
        logger.exception(
            "video: %s could not write the call line into thread %s",
            function_name,
            call.thread_key,
        )
        return False
    return True


__all__ = [
    "MARKER_DECLINED",
    "MARKER_ENDED",
    "MARKER_MISSED",
    "call_body",
    "post_call_line",
]
