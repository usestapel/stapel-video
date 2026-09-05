"""Channels routes — discovered, not hand-wired.

``stapel_realtime.build_websocket_application()`` walks INSTALLED_APPS and
collects every ``<app>.routing.websocket_urlpatterns``, so a host that
assembles its ASGI app the canonical way gets the lobby socket without naming
it::

    # asgi.py — the whole file
    from django.core.asgi import get_asgi_application
    from stapel_realtime.asgi import build_websocket_application

    application = build_websocket_application(
        http_application=get_asgi_application()
    )

That builds ``OriginGuard(JWTAuthMiddlewareStack(URLRouter(patterns)))`` — the
origin guard included, which the hand-written ``ProtocolTypeRouter`` this
module used to ask for did not have. A browser authenticates this socket with
its JWT **cookie**, and a cookie is ambient authority: the guard is what
stands between a cookie-authenticated socket and cross-site hijacking, so it
is not an optional layer to compose by hand.

Two mounts:

    ws/video/lobby/<join_code>    one room's lobby (ephemeral)
    ws/video/inbox                one PERSON's call inbox (ephemeral)

The join code in the path is the lobby stream's scope id —
``video:lobby:<code>`` — and it is not a secret: subscription is authorized
separately and fail-closed (:mod:`stapel_video.consumers`).

The call inbox carries **no** id in its path, and that is the authorization
rather than an omission. ``video:user:<id>`` is built from the authenticated
scope inside :class:`~stapel_video.calls.consumers.CallInboxConsumer`, so the
consumer physically cannot name somebody else's ring — where a path parameter
would make "is this your inbox?" a comparison a future edit can drop. Same
shape as ``ws/notifications/inbox`` and ``ws/chat/inbox``.
"""
from django.urls import path

from .calls.consumers import CallInboxConsumer
from .consumers import LobbyConsumer

websocket_urlpatterns = [
    path("ws/video/lobby/<str:join_code>", LobbyConsumer.as_asgi()),
    path("ws/video/inbox", CallInboxConsumer.as_asgi()),
]
