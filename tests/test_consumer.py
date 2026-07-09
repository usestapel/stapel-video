"""Realtime lobby consumer (Channels) — auth/membership guard + fan-out.

Driven through Channels' WebsocketCommunicator against the URLRouter, with a
tiny middleware injecting ``scope["user"]`` in place of the real JWT channels
middleware (that stack is stapel-core's own tested surface; here we test THIS
consumer's membership guard and group fan-out). Transaction DB so the
threaded ``database_sync_to_async`` membership check sees committed rows.
"""
import pytest
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from stapel_video.realtime import lobby_group
from stapel_video.routing import websocket_urlpatterns

pytestmark = pytest.mark.asyncio


class _InjectUser:
    """Stand-in for JWTAuthMiddleware: stamps a fixed user on the scope."""

    def __init__(self, inner, user):
        self.inner = inner
        self.user = user

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        scope["user"] = self.user
        return await self.inner(scope, receive, send)


def _app(user):
    return _InjectUser(URLRouter(websocket_urlpatterns), user)


def _make_room(member: bool):
    from django.contrib.auth import get_user_model

    from stapel_video import services
    from stapel_video.models import ParticipantStatus, RoomParticipant

    User = get_user_model()
    host = User.objects.create_user(username="host", email="h@x.io", password="x")
    room = services.create_room(host, access_level="restricted")
    guest = User.objects.create_user(username="guest", email="g@x.io", password="x")
    if member:
        RoomParticipant.objects.create(
            room=room, user=guest, status=ParticipantStatus.WAITING
        )
    return {"code": room.join_code, "host": host, "guest": guest}


async def _connect(user, code):
    comm = WebsocketCommunicator(_app(user), f"/ws/video/lobby/{code}")
    connected, code_out = await comm.connect(timeout=5)
    return comm, connected, code_out


@pytest.mark.django_db(transaction=True)
async def test_member_connects_and_receives_admission(settings):
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, _ = await _connect(ctx["guest"], ctx["code"])
    assert connected

    # A host admit pushes lobby.admitted to the room group; the socket relays it.
    layer = get_channel_layer()
    await layer.group_send(
        lobby_group(ctx["code"]),
        {"type": "lobby.admitted", "participant_id": "p1", "user_id": "u1", "token": "tok"},
    )
    msg = await comm.receive_json_from(timeout=5)
    assert msg["type"] == "lobby.admitted"
    assert msg["token"] == "tok"
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_non_member_is_rejected_forbidden():
    ctx = await database_sync_to_async(_make_room)(False)
    comm, connected, close_code = await _connect(ctx["guest"], ctx["code"])
    assert not connected
    assert close_code == 4403
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_unauthenticated_is_rejected():
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, close_code = await _connect(AnonymousUser(), ctx["code"])
    assert not connected
    assert close_code == 4401
    await comm.disconnect()
