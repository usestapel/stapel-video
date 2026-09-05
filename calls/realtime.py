"""The ring, live: one stream per person, and what travels on it.

``video:user:<user_id>`` — one person's ephemeral call inbox. A call is the
one thing in this module addressed to a PERSON rather than to a room: the
lobby stream (:mod:`stapel_video.realtime`) fans out to everybody in a room
and redacts what is not theirs, which works because a lobby is a shared fact.
A ring is not a shared fact. It is addressed to exactly one person, and the
right shape for that is the one stapel-chat and stapel-notifications already
use — ``<mod>:user:<id>``, with the id taken from the authenticated scope and
never from the URL (:mod:`stapel_video.calls.consumers`).

**Why not ``notifications:user:<id>``.** That stream exists and already
reaches every signed-in browser, so putting the ring on it would save a
socket. It carries feed rows (``feed_item_payload``), and its module segment
says who owns the vocabulary. A call frame there is either a fabricated feed
row or another module's signal set growing a video verb — and the next
question ("why is a missed call in my notification bell twice?") is the bill
for it. The push notification in :mod:`stapel_video.calls.notify` is the part
that genuinely belongs to notifications, and it goes there.

**No credential ever rides these frames.** The lobby's ``lobby.admitted``
carries a media token and therefore needs per-socket redaction; the ring does
not. ``call.incoming`` says a call exists, and the callee's token comes back
from ``POST /calls/{id}/accept`` — an authenticated request by the person it
belongs to. That is not a small saving: redaction is a rule somebody has to
keep obeying, and a frame with nothing to redact cannot be got wrong.

Best-effort, like every Signal: no transport, no subscriber, redis down — the
frame is dropped and nothing raises, because the truth is the ``Call`` row and
``GET /calls/active`` still returns it. That is exactly why the client is
required to re-read it on reconnect (SPEC §7.1).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Canonical stream-key module segment — the same one the lobby uses. The
#: scope type is what distinguishes them.
STREAM_MODULE = "video"

#: Scope type of the call inbox: one person.
USER_SCOPE = "user"

# ── signal types ─────────────────────────────────────────────────────────
# Never protocol frame types (hello/welcome/live/ping/...); the core refuses a
# signal type that claims one.

#: Somebody is calling you (payload: ``call_id``, ``caller_id``,
#: ``thread_key``, ``media``, ``started_at``, ``expires_at``). To the callee.
SIGNAL_INCOMING = "call.incoming"
#: They picked up (``call_id``, ``answered_at``). To the caller.
SIGNAL_ACCEPTED = "call.accepted"
#: They refused (``call_id``). To the caller.
SIGNAL_DECLINED = "call.declined"
#: It is over, however it ended (``call_id``, ``state``, ``end_reason``,
#: ``duration_seconds``). To BOTH parties — a ringing overlay has to close on
#: the callee's screen too when the caller gives up.
SIGNAL_ENDED = "call.ended"

#: The four, as one set — the contract a client matches frame types against,
#: and what :func:`notify_user` validates against so a typo is an exception
#: here rather than a frame nobody ever receives.
CALL_SIGNAL_TYPES = frozenset(
    {SIGNAL_INCOMING, SIGNAL_ACCEPTED, SIGNAL_DECLINED, SIGNAL_ENDED}
)


def user_stream(user_id) -> str:
    """``video:user:<user_id>`` — one person's ephemeral call inbox."""
    from stapel_core.comm import stream_key

    return stream_key(STREAM_MODULE, USER_SCOPE, str(user_id))


def user_group(user_id) -> str:
    """Channels group name the call inbox maps to.

    Delegated to the substrate rather than formatted here, for the reason
    :func:`stapel_video.realtime.lobby_group` gives: two answers to "which
    group is this stream" is how a fan-out reaches nobody.
    """
    from stapel_realtime.streams import group_name

    return group_name(user_stream(user_id))


def notify_user(user_id, type: str, payload: dict) -> dict:
    """Put one frame on one person's call inbox.

    Returns the wire envelope whether or not anything was delivered — the
    return value describes the frame, not its fate. Delivery is scheduled by
    ``signal()`` through ``transaction.on_commit``, so a ring is never
    announced before the row that justifies it is durable.
    """
    if type not in CALL_SIGNAL_TYPES:
        raise ValueError(
            f"{type!r} is not a call signal type; expected one of "
            f"{sorted(CALL_SIGNAL_TYPES)}"
        )
    if not user_id:
        return {}
    from stapel_core.comm import signal

    try:
        return signal(user_stream(user_id), type, dict(payload))
    except Exception:  # pragma: no cover - a courtesy never breaks a caller
        logger.debug("video: call signal skipped for %s", user_id, exc_info=True)
        return {}


__all__ = [
    "CALL_SIGNAL_TYPES",
    "SIGNAL_ACCEPTED",
    "SIGNAL_DECLINED",
    "SIGNAL_ENDED",
    "SIGNAL_INCOMING",
    "STREAM_MODULE",
    "USER_SCOPE",
    "notify_user",
    "user_group",
    "user_stream",
]
