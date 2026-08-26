"""The lobby, live: what this module puts on the wire, and where.

One stream, and it is the room's:

``video:lobby:<join_code>`` — the **lobby** of one room. Ephemeral. A guest
watches it to learn the host's verdict the moment it is given; a host watches
it to see arrivals. Everything it carries is already readable over REST
(``GET /rooms/{join_code}/lobby`` for the queue, ``POST /rooms/{join_code}/join``
for one's own status and token), which is the substrate's condition for a fact
being allowed to travel as a Signal at all.

The scope id is the **join code** — the one identifier this API addresses a
room by, on every REST route and in the socket path. A stream keyed on the
internal row id would be a second name for a room that nothing else in the
product uses, and the browser holding the join code would have to fetch the
room just to learn which stream to open.

Until 0.9.0 this module hand-rolled its own fan-out: a Channels group named
``video_lobby_<code>``, carrying bare ``{"type": "lobby.waiting", …}`` dicts.
``stapel-realtime``'s client half requires the v1 envelope and drops anything
else, so the browser client — written, tested and shipped against the
substrate — received nothing and showed its offline state. Since 0.9.0 the
emitter is ``stapel_core.comm.signal()``: the core builds the envelope, the
substrate's transport forwards it verbatim, and there is exactly one wire
shape in the fleet.

**Emitting is free; serving the socket costs the extra.** ``signal()`` is a
silent no-op on a host with no ``STAPEL_COMM["SIGNAL_TRANSPORT"]``, so an
HTTP-only deployment pays nothing for this file and its clients poll as
before. The half-configured middle — the socket served, the transport unset —
is a boot warning (``stapel_video.W005``) rather than a silent lobby.

Payload minimalism (the substrate's review-checklist item) is satisfied by the
gate: subscription to ``video:lobby:<code>`` *is* membership of that room, and
a member may read the lobby queue over REST. The one field that is **not**
admissible to every member is the admitted guest's media ``token`` — a
credential for one person, not a fact about the room — so the consumer sends
it only to the socket it belongs to. See :mod:`stapel_video.consumers`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Canonical stream-key module segment.
STREAM_MODULE = "video"

#: Scope type of the lobby stream: one room, addressed by its join code.
LOBBY_SCOPE = "lobby"

# ── signal types ─────────────────────────────────────────────────────────
# Never protocol frame types — the core refuses a signal type that claims
# one, which is what keeps a courtesy frame from being read as protocol.

#: Somebody asked to join and is parked in the lobby (payload:
#: ``participant_id``, ``user_id``, ``user_name``).
SIGNAL_WAITING = "lobby.waiting"
#: The host let somebody in (``participant_id``, ``user_id``, ``token`` —
#: the token only in the addressee's own copy of the frame).
SIGNAL_ADMITTED = "lobby.admitted"
#: The host turned somebody away (``participant_id``, ``user_id``).
SIGNAL_DENIED = "lobby.denied"

#: The three, as one set — the contract a client matches frame types against.
LOBBY_SIGNAL_TYPES = frozenset({SIGNAL_WAITING, SIGNAL_ADMITTED, SIGNAL_DENIED})

#: Payload key carrying a media credential. Present on ``lobby.admitted``
#: only, and redacted for every subscriber but the one it names.
TOKEN_FIELD = "token"


def lobby_stream(join_code: str) -> str:
    """``video:lobby:<join_code>`` — one room's ephemeral lobby stream."""
    from stapel_core.comm import stream_key

    return stream_key(STREAM_MODULE, LOBBY_SCOPE, str(join_code))


def lobby_group(join_code: str) -> str:
    """Channels group name the lobby stream maps to.

    Delegated to the substrate rather than formatted here: group naming is a
    transport detail with a length ceiling and a character set, and two
    answers to "which group is this stream" is how a fan-out reaches nobody.
    """
    from stapel_realtime.streams import group_name

    return group_name(lobby_stream(join_code))


def notify_lobby(join_code: str, type: str, payload: dict) -> dict:
    """Tell whoever is watching this room's lobby that something happened.

    Returns the wire envelope (whether or not anything was delivered — the
    return value describes the frame, not its fate). Delivery is scheduled by
    ``signal()`` through ``transaction.on_commit``, so nothing is announced
    before the row that justifies it is durable.

    Best-effort is the contract, not a caveat: no transport, no subscriber,
    redis down — the frame is dropped and nothing raises, because the truth is
    the ``RoomParticipant`` row and the REST reads still return it.
    """
    if type not in LOBBY_SIGNAL_TYPES:
        raise ValueError(
            f"{type!r} is not a lobby signal type; expected one of "
            f"{sorted(LOBBY_SIGNAL_TYPES)}"
        )
    from stapel_core.comm import signal

    try:
        return signal(lobby_stream(join_code), type, dict(payload))
    except Exception:  # pragma: no cover - a courtesy never breaks a caller
        logger.debug("video: lobby signal skipped for %s", join_code, exc_info=True)
        return {}


__all__ = [
    "LOBBY_SCOPE",
    "LOBBY_SIGNAL_TYPES",
    "SIGNAL_ADMITTED",
    "SIGNAL_DENIED",
    "SIGNAL_WAITING",
    "STREAM_MODULE",
    "TOKEN_FIELD",
    "lobby_group",
    "lobby_stream",
    "notify_lobby",
]
