"""Channels routing for the realtime lobby (optional ``channels`` extra).

Mount in the host ASGI app under the JWT auth middleware (G14)::

    from channels.routing import ProtocolTypeRouter, URLRouter
    from stapel_core.django.jwt.channels import JWTAuthMiddlewareStack
    from stapel_video.routing import websocket_urlpatterns

    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    })
"""
from django.urls import path

from .consumers import LobbyConsumer

websocket_urlpatterns = [
    path("ws/video/lobby/<str:join_code>", LobbyConsumer.as_asgi()),
]
