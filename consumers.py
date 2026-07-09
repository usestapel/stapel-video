"""Realtime lobby consumer (Channels — optional extra).

A guest connects to their room's lobby socket to receive live admission
decisions; a host connects to see arrivals. Authentication is the SAME Stapel
JWT stack HTTP uses, via ``stapel_core.django.jwt.channels.JWTAuthMiddleware``
(G14): the connection scope carries ``scope["user"]`` already, and
unauthenticated clients never reach ``connect`` (the socket is closed 4401 in
the handshake). This consumer additionally enforces room membership so an
authenticated stranger cannot subscribe to an unrelated room's lobby.

Channels is an OPTIONAL dependency; importing this module without it raises a
clear ImportError. Nothing in the package imports it on a normal HTTP start.
"""
import json

try:
    from channels.db import database_sync_to_async
    from channels.generic.websocket import AsyncWebsocketConsumer
except ImportError as exc:  # pragma: no cover - exercised via extra-less install
    raise ImportError(
        "stapel_video.consumers requires the optional 'channels' dependency. "
        "Install it with:\n    pip install 'stapel-video[channels]'"
    ) from exc

# WebSocket close codes (RFC 6455 private range).
WS_CLOSE_UNAUTHENTICATED = 4401
WS_CLOSE_FORBIDDEN = 4403

# Group message types the consumer relays to browser clients.
_RELAYED = frozenset({"lobby.waiting", "lobby.admitted", "lobby.denied"})


class LobbyConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            await self.close(code=WS_CLOSE_UNAUTHENTICATED)
            return

        self.join_code = self.scope["url_route"]["kwargs"]["join_code"]
        if not await self._is_member(user.pk, self.join_code):
            await self.close(code=WS_CLOSE_FORBIDDEN)
            return

        self.user = user
        self.group_name = f"video_lobby_{self.join_code}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    @database_sync_to_async
    def _is_member(self, user_id, join_code):
        from .models import Room, RoomParticipant

        room = Room.objects.filter(join_code=join_code).first()
        if room is None:
            return False
        if room.created_by_id == user_id:
            return True
        return RoomParticipant.objects.filter(room=room, user_id=user_id).exists()

    async def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # This lobby socket is read-only for clients: admission decisions go
        # through the authenticated REST endpoints (host-only). Ignore input.
        return

    async def _fanout(self, event):
        """Handler for every relayed group message type (see _RELAYED)."""
        if event.get("type") in _RELAYED:
            payload = {k: v for k, v in event.items() if k != "type"}
            payload["type"] = event["type"]
            await self.send(text_data=json.dumps(payload))

    # Channels dispatches ``{"type": "lobby.waiting"}`` to a method named
    # ``lobby_waiting`` (dots -> underscores). Alias all three to _fanout.
    lobby_waiting = _fanout
    lobby_admitted = _fanout
    lobby_denied = _fanout
