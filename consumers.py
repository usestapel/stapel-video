"""The lobby socket. One per room, read-only, ephemeral.

Built on ``stapel_realtime.EphemeralStreamConsumer`` — the substrate that
exists so the fleet stops writing its fourth WebSocket. Nothing here
implements a socket: it names its stream, says who may watch it, and holds
back the one payload field that is not everybody's.

Until 0.9.0 this file *was* an implementation: its own close codes, its own
membership check, and a fan-out that sent bare ``{"type": "lobby.waiting", …}``
dicts. The browser client is built on ``@stapel/realtime``, whose ``decodeFrame``
requires the v1 envelope and **drops** anything else — so the lobby panel sat
in its offline state while the server was talking. One wire shape, owned by
the substrate, is the whole point of this release.

What is left that is genuinely this module's:

* **Which stream.** ``video:lobby:<join_code>`` (:mod:`stapel_video.realtime`),
  derived from the URL segment the socket is mounted on.
* **Who may watch it.** Room membership — the host, or somebody with a
  participant row (waiting, admitted or denied: a denied guest is told the
  verdict on the socket they are already holding).
* **The token redaction.** ``lobby.admitted`` carries a media credential for
  one person; every member of the room is on this stream. The frame reaches
  the addressee with its token and everybody else without it.

Client input is ignored by design: verdicts are host-only REST calls. That is
the substrate's default posture and this module has no reason to be the
exception chat is.

``stapel-realtime``'s Channels extra is optional. Importing this module
without it raises a clear ImportError, and it is never imported at app-ready
time — :mod:`stapel_video.routing` resolves it lazily, so a host that serves
HTTP only never loads an ASGI stack.
"""
from __future__ import annotations

try:
    from channels.db import database_sync_to_async
    from stapel_realtime.consumers import EphemeralStreamConsumer
except ImportError as exc:  # pragma: no cover - exercised via optional-dep test
    raise ImportError(
        "stapel_video.consumers requires the optional 'stapel-realtime' "
        "dependency and its Channels extra. Install it with:\n"
        "    pip install 'stapel-video[realtime]'"
    ) from exc

from .realtime import LOBBY_SCOPE, SIGNAL_ADMITTED, STREAM_MODULE, TOKEN_FIELD


def _is_member(user_id, join_code: str) -> bool:
    """Is this user the host of the room, or a participant in it?

    Runs in a thread (it touches the ORM). Any answer but a positive one is
    ``False``: an unknown join code, a stranger, a missing user.
    """
    from .models import Room, RoomParticipant

    room = Room.objects.filter(join_code=join_code).first()
    if room is None:
        return False
    if room.created_by_id == user_id:
        return True
    return RoomParticipant.objects.filter(room=room, user_id=user_id).exists()


class LobbyConsumer(EphemeralStreamConsumer):
    """One socket ↔ one room's lobby stream.

    Ephemeral by nature, not by economy: a missed frame costs a re-read of
    ``GET /rooms/{join_code}/lobby`` (host) or a re-``POST`` of the join
    (guest), both of which the client makes anyway on load. That is exactly
    the bargain the Signal primitive is for — and the reason a lobby that
    loses a frame is late, never wrong.
    """

    module = STREAM_MODULE
    scope_type = LOBBY_SCOPE
    stream_key_kwarg = "join_code"

    async def authorize(self, scope, stream_key) -> bool:
        """You may watch the lobby of a room you belong to.

        Authentication (G14, cookie included since core 0.44.2) has already
        happened in the middleware; this is the second gate, and it is the one
        that stops an authenticated stranger from subscribing to somebody
        else's room. Fail-closed: an unparseable key or an unknown room is a
        refusal, never a guess at a scope.
        """
        from stapel_realtime.streams import InvalidStreamKey, parse_stream_key

        user_id = self._user_id()
        if user_id is None:
            return False
        try:
            key = parse_stream_key(stream_key)
        except InvalidStreamKey:
            return False
        if key.module != STREAM_MODULE or key.scope_type != LOBBY_SCOPE:
            return False
        return await database_sync_to_async(_is_member)(user_id, key.scope_id)

    async def realtime_signal(self, event):
        """Forward the signal — minus a credential that is not this socket's.

        ``lobby.admitted`` is one frame with two readerships: the guest who
        was let in needs the media token it carries, and every other member of
        the room needs to know a row changed. Fanning the token out to the
        whole stream would hand a waiting guest somebody else's credential for
        the call, so it is stripped for everyone the frame does not name.

        The frame is otherwise forwarded verbatim, envelope included: this
        overrides *what one socket is shown*, never the wire shape.
        """
        frame = event.get("frame")
        if isinstance(frame, dict) and frame.get("type") == SIGNAL_ADMITTED:
            payload = frame.get("payload") or {}
            if TOKEN_FIELD in payload and not self._addresses_me(payload):
                frame = dict(frame)
                frame["payload"] = {
                    k: v for k, v in payload.items() if k != TOKEN_FIELD
                }
                event = dict(event, frame=frame)
        return await super().realtime_signal(event)

    def _addresses_me(self, payload) -> bool:
        user_id = self._user_id()
        return user_id is not None and str(payload.get("user_id")) == str(user_id)


__all__ = ["LobbyConsumer"]
