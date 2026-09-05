"""The call inbox socket. One per person, read-only, ephemeral.

Built on ``stapel_realtime.EphemeralStreamConsumer``, like the lobby. What is
this module's own:

* **Which stream.** ``video:user:<user_id>`` — and the id comes from the
  AUTHENTICATED SCOPE, never from the URL. That is why the route carries no
  parameter (``ws/video/inbox``): a per-user stream whose id is a path segment
  is a stream whose authorization is a string comparison somebody has to
  remember to write, and forgetting it hands one person another's ring. With
  the id derived from the JWT there is nothing to compare and nothing to
  forget. The shape is copied verbatim from
  ``stapel_notifications.consumers.NotificationInboxConsumer``.
* **Who may watch it.** Anybody signed in — of their own inbox, which is the
  only inbox this consumer can name.

**No redaction, by construction.** The lobby has to strip a media token from
``lobby.admitted`` for every socket the frame does not name, because one frame
reaches a whole room. Nothing here carries a credential: the callee's token
comes back from ``POST /calls/{id}/accept``. A rule nobody has to obey is a
rule nobody can break.

Client input is ignored by design — accepting and declining are authenticated
REST calls, not socket messages. A socket that can end a call is a second,
unversioned, unaudited write path to the same state machine.
"""
from __future__ import annotations

try:
    from stapel_realtime.consumers import EphemeralStreamConsumer
except ImportError as exc:  # pragma: no cover - exercised via optional-dep test
    raise ImportError(
        "stapel_video.calls.consumers requires the optional 'stapel-realtime' "
        "dependency and its Channels extra. Install it with:\n"
        "    pip install 'stapel-video[realtime]'"
    ) from exc

from .realtime import STREAM_MODULE, USER_SCOPE, user_stream


class CallInboxConsumer(EphemeralStreamConsumer):
    """One socket ↔ one person's call inbox.

    Ephemeral: a missed frame costs a re-read of ``GET /calls/active``, which
    the client makes on mount and on every reconnect anyway. That is the
    bargain the Signal primitive is for, and it is why a lost ring is late
    rather than wrong.
    """

    module = STREAM_MODULE
    scope_type = USER_SCOPE

    async def get_stream_key(self) -> str:
        """This connection's own inbox, and no other.

        Overriding the key rather than taking it from a URL kwarg is the
        authorization: the consumer physically cannot name somebody else's
        stream, so no verdict is being trusted to get it right.
        """
        return user_stream(self._user_id())

    async def authorize(self, scope, stream_key) -> bool:
        """Signed in, and that is the whole question.

        Authentication (JWT, cookie included) has already happened in the
        middleware. There is no second gate here because there is nothing for
        it to check: the key was built from the authenticated id one line
        above, so "may this user watch this stream" is "is this user this
        user". An anonymous scope is refused, which the base consumer also
        does — stated here so reading this class does not require resolving
        which base it inherits this month.
        """
        return self._user_id() is not None


__all__ = ["CallInboxConsumer"]
