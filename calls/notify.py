"""The push — the half of ringing that reaches a phone with the screen off.

The socket in :mod:`stapel_video.calls.realtime` rings a browser that is open.
A push rings a phone that is in a pocket, and those are different problems
with different transports. Both fire; neither is a fallback for the other.

The send surface is ``stapel_core.notifications.request_notification`` — the
fleet's only one. It publishes ``notification.requested`` on the bus and
stapel-notifications' push channel fans it to the recipient's device tokens.
No import of stapel-notifications happens here, and none is possible: the
name is on the bus, the delivery is somebody else's process.

**The two types are the HOST's to register.** ``STAPEL_NOTIFICATIONS["TYPES"]``
is a settings dict, and a library cannot write into another module's
namespace. So this module names ``call.incoming`` / ``call.missed`` and the
deployment declares them; an unregistered type is logged and dropped by the
consumer, which is why the names are constants here and repeated in the
fleet's settings rather than being spelled twice by hand.

**The ring push is gated on the callee NOT being live** (0.11.1). The gap this
module named in 0.11.0 — nothing in the fleet answers "does this user have a
live realtime session" — was closed by stapel-realtime 0.2.0's
``realtime.is_live``, and the gate is asked here rather than reimplemented:
only the process holding the socket knows the answer, which is exactly why it
is a comm Function and not an import.

The gate is asked with no ``family``, deliberately: the question is "is this
person watching anything at all", because a browser open on any page of the
app is a browser that will draw the incoming-call overlay.

**Every uncertainty pushes.** No such Function in this deployment, no route to
it, an exception, a malformed answer — all of them fall through to the
unconditional push that was the behaviour before the oracle existed. The two
failure modes are not symmetric: a redundant banner next to a ringing overlay
is noise, and a suppressed push is a call that never reached a phone in a
pocket, which is the case this whole module exists for. ``call.missed`` is
never gated — by then the person demonstrably was not reachable.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Notification type for a call that is ringing right now. The host registers
#: it in ``STAPEL_NOTIFICATIONS["TYPES"]``; an unregistered type is dropped by
#: the consumer with a log line, never delivered as a generic message.
TYPE_INCOMING = "call.incoming"

#: Notification type for a call that rang out unanswered.
TYPE_MISSED = "call.missed"


def notify_incoming(call) -> bool:
    """Push "X is calling you" to the callee's devices, unless they are watching.

    Returns whether a push was requested — False both for a suppressed one and
    for a send the bus refused, because the caller (:func:`_ring`) does
    nothing with either and a call is not held up by its own courtesies.
    """
    if _callee_is_live(call):
        logger.debug(
            "video: call %s rings a live callee, skipping the push", call.id
        )
        return False
    return _request(TYPE_INCOMING, call)


def notify_missed(call) -> bool:
    """Push "you missed a call from X" to the callee's devices."""
    return _request(TYPE_MISSED, call)


def _callee_is_live(call) -> bool:
    """Does the callee have a live realtime session right now?

    True only when the fleet's oracle says so in this many words. The Function
    may be absent (a deployment without stapel-realtime installed, or one
    whose transport has no route to it), it may raise, and it may answer
    something without a ``live`` key — each of those is *unknown*, and unknown
    pushes, because an unheard call costs more than a redundant banner.

    Reachability is checked before the call rather than caught after it: on
    the ``inprocess`` transport an unregistered name raises per ring, and a
    deployment that simply does not run presence should not pay an exception
    and a stack trace for every call it places.
    """
    from stapel_core.comm import call as comm_call
    from stapel_core.comm import function_unreachable_reason

    from ..conf import video_settings

    name = (video_settings.CALL_PRESENCE_FUNCTION or "").strip()
    if not name:
        return False
    reason = function_unreachable_reason(name)
    if reason:
        logger.debug("video: %s is unreachable (%s); pushing anyway", name, reason)
        return False
    try:
        answer = comm_call(name, {"user_id": str(call.callee_id)})
    except Exception:
        logger.warning(
            "video: %s did not answer for %s; pushing anyway",
            name,
            call.callee_id,
            exc_info=True,
        )
        return False
    return bool((answer or {}).get("live"))


def _request(notification_type: str, call) -> bool:
    from stapel_core.notifications import request_notification

    return bool(
        request_notification(
            notification_type,
            user_id=str(call.callee_id),
            variables={
                "call_id": str(call.id),
                "caller_id": str(call.caller_id),
                "caller_name": _caller_name(call),
                "thread_key": call.thread_key,
                "media": call.media,
            },
        )
    )


def _caller_name(call) -> str:
    """The caller's name as the recipient's phone should show it.

    Read the same duck-typed way ``stapel_video.services._display_name`` reads
    it — this library does not own the user model and will not grow a second
    opinion about where a name lives. An empty answer is fine: the
    notification template decides what to say when there is no name, and a
    template is where copy belongs.
    """
    from ..services import _display_name

    try:
        return _display_name(call.caller)
    except Exception:  # pragma: no cover - a name is never worth a failed push
        return ""


__all__ = ["TYPE_INCOMING", "TYPE_MISSED", "notify_incoming", "notify_missed"]
