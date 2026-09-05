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

**Named gap: this is not gated on the callee being offline.** Nothing in the
fleet answers "does this user have a live realtime session" — stapel-realtime
has no presence at all, and stapel-chat's is about chat sockets and is
module-private. Gating on a fact nobody can supply would mean either
inventing an oracle here (a fourth presence implementation) or guessing. So
the push is sent every time and the client suppresses the banner for a call it
is already ringing in-app. When a fleet-level liveness Function exists, gate
it there and delete this paragraph — do not build a private one.
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
    """Push "X is calling you" to the callee's devices."""
    return _request(TYPE_INCOMING, call)


def notify_missed(call) -> bool:
    """Push "you missed a call from X" to the callee's devices."""
    return _request(TYPE_MISSED, call)


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
