"""The lobby socket, driven the way a browser drives it.

Every handshake here sends what a browser sends and **nothing a browser
cannot**: the JWT cookie in the handshake headers, an ``Origin``, and no
``Authorization`` header — ``_browser_headers`` asserts the absence rather
than trusting the test author. That is the whole point of this file: the
previous suite injected ``scope["user"]`` with a stand-in middleware, so it
proved the consumer's membership guard and proved nothing about the only
handshake that happens in production. A socket smoke-tested with an
``Authorization`` header is how chat's lobby stayed dark for months.

So the application under test is the real stack — ``OriginGuard`` ->
``JWTAuthMiddlewareStack`` -> ``URLRouter`` — assembled by
``stapel_realtime.build_websocket_application()`` from INSTALLED_APPS, exactly
as a host's three-line ``asgi.py`` assembles it. The token is a real one from
``jwt_provider``; nothing is monkeypatched.

The second half of the file asserts the **wire**: the exact v1 envelope
``{v, type, stream, payload}`` that ``@stapel/realtime``'s ``decodeFrame``
accepts, the three payload types, and the redaction that keeps one guest's
media token off every other guest's socket.
"""
import json

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from stapel_video.realtime import (
    SIGNAL_ADMITTED,
    SIGNAL_DENIED,
    SIGNAL_WAITING,
    lobby_stream,
)

pytestmark = pytest.mark.asyncio

ORIGIN = "https://app.example.test"
FOREIGN_ORIGIN = "https://evil.example.test"


# ── the browser's half of the handshake ─────────────────────────────────


def _browser_headers(token: str | None = None, origin: str | None = ORIGIN) -> list:
    """Handshake headers a browser can actually produce.

    A cookie the browser attaches on its own, an ``Origin`` it stamps on every
    WebSocket handshake, and no third thing: ``new WebSocket(url)`` takes no
    headers, which is the fact the whole cookie branch of core 0.44.2 exists
    for.
    """
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if token is not None:
        headers.append((b"cookie", f"stapel_jwt={token}".encode()))
    assert not any(name == b"authorization" for name, _ in headers), (
        "a browser cannot set an Authorization header on a WebSocket "
        "handshake — a test that sends one proves nothing about this socket"
    )
    return headers


def _application():
    """The host's whole WebSocket stack, built the canonical way."""
    from stapel_realtime.asgi import build_websocket_application

    return build_websocket_application()


def _mint(user) -> str:
    from stapel_core.django.jwt.provider import jwt_provider

    access, _refresh = jwt_provider.create_tokens(user)
    return access


def _make_room(member: bool):
    from django.contrib.auth import get_user_model

    from stapel_video import services
    from stapel_video.models import ParticipantStatus, RoomParticipant

    User = get_user_model()
    host = User.objects.create_user(username="host", email="h@x.io", password="x")
    room = services.create_room(host, access_level="restricted")
    guest = User.objects.create_user(username="guest", email="g@x.io", password="x")
    other = User.objects.create_user(username="other", email="o@x.io", password="x")
    if member:
        RoomParticipant.objects.create(
            room=room, user=guest, status=ParticipantStatus.WAITING
        )
    RoomParticipant.objects.create(
        room=room, user=other, status=ParticipantStatus.WAITING
    )
    return {
        "code": room.join_code,
        "host": host,
        "guest": guest,
        "other": other,
        "guest_token": _mint(guest),
        "host_token": _mint(host),
        "other_token": _mint(other),
    }


async def _connect(code, token=None, origin=ORIGIN):
    comm = WebsocketCommunicator(
        _application(),
        f"/ws/video/lobby/{code}",
        headers=_browser_headers(token, origin),
    )
    connected, detail = await comm.connect(timeout=5)
    return comm, connected, detail


async def _signal(code, type_, payload):
    """Emit exactly as ``services`` does, from the async test's thread."""
    from stapel_video.realtime import notify_lobby

    return await database_sync_to_async(notify_lobby)(code, type_, payload)


# ── the handshake ────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_a_browser_opens_the_lobby_with_its_cookie_alone():
    """The handshake a real client makes, admitted.

    FAILS ON stapel-core < 0.44.2: no cookie branch in the Channels
    extractor, so this closes 4401 — which is the production symptom, and the
    reason this module's floor moved.
    """
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, detail = await _connect(ctx["code"], ctx["guest_token"])
    assert connected, f"the browser handshake was refused (close {detail})"
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_handshake_with_no_cookie_is_unauthenticated():
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, close_code = await _connect(ctx["code"], token=None)
    assert not connected
    assert close_code == 4401
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_cookie_from_a_foreign_page_is_refused():
    """Cross-Site WebSocket Hijacking, refused at the origin.

    The attacker's page cannot read the cookie, but the browser attaches it to
    a handshake that page opens — WebSockets have neither same-origin policy
    nor CORS. A valid cookie from an unlisted origin must never become a
    subscription.
    """
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, close_code = await _connect(
        ctx["code"], ctx["guest_token"], origin=FOREIGN_ORIGIN
    )
    assert not connected
    assert close_code == 4403
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_an_authenticated_stranger_is_not_a_member():
    """Authentication says who; the authorize gate says what they may watch."""
    ctx = await database_sync_to_async(_make_room)(False)
    comm, connected, close_code = await _connect(ctx["code"], ctx["guest_token"])
    assert not connected
    assert close_code == 4403
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_an_unknown_room_is_refused_not_guessed():
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, close_code = await _connect("no-such-room", ctx["guest_token"])
    assert not connected
    assert close_code == 4403
    await comm.disconnect()


# ── the wire ─────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_a_waiting_guest_arrives_as_a_v1_envelope():
    """The exact frame ``@stapel/realtime``'s decodeFrame accepts.

    Asserted key by key, including ``v`` and ``stream``: a bare
    ``{"type": "lobby.waiting", …}`` — what this module sent until 0.9.0 — is
    dropped by the client, and the panel that drops it shows an empty lobby
    while the server believes it announced somebody.
    """
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, _ = await _connect(ctx["code"], ctx["host_token"])
    assert connected

    await _signal(
        ctx["code"],
        SIGNAL_WAITING,
        {"participant_id": "p1", "user_id": "u1", "user_name": "Ada"},
    )
    frame = await comm.receive_json_from(timeout=5)
    assert frame == {
        "v": 1,
        "type": "lobby.waiting",
        "stream": lobby_stream(ctx["code"]),
        "payload": {"participant_id": "p1", "user_id": "u1", "user_name": "Ada"},
    }
    # Ephemeral, structurally: no seq means no journal, and no mode flag to
    # get wrong. The lobby has no history and the REST read is the truth.
    assert "seq" not in frame
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_the_stream_key_is_the_room_and_only_the_room():
    """A frame for another room's lobby never reaches this socket."""
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, _ = await _connect(ctx["code"], ctx["guest_token"])
    assert connected

    await _signal(
        "zzz-zzzz-zzz",
        SIGNAL_WAITING,
        {"participant_id": "p9", "user_id": "u9", "user_name": "Nobody"},
    )
    assert await comm.receive_nothing(timeout=0.4)
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_denied_arrives_on_the_socket_the_guest_is_already_holding():
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, _ = await _connect(ctx["code"], ctx["guest_token"])
    assert connected

    await _signal(
        ctx["code"], SIGNAL_DENIED, {"participant_id": "p1", "user_id": "u1"}
    )
    frame = await comm.receive_json_from(timeout=5)
    assert frame["type"] == "lobby.denied"
    assert frame["payload"] == {"participant_id": "p1", "user_id": "u1"}
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_the_admit_token_reaches_only_the_guest_it_names():
    """One frame, two readerships.

    Every member of the room watches this stream, and ``lobby.admitted``
    carries a media credential for exactly one of them. The addressee's socket
    gets the token; the other waiting guest gets the same frame without it —
    otherwise a stranger in the lobby could pick up somebody else's admission
    and walk into the call with it.
    """
    ctx = await database_sync_to_async(_make_room)(True)
    mine, ok_mine, _ = await _connect(ctx["code"], ctx["guest_token"])
    theirs, ok_theirs, _ = await _connect(ctx["code"], ctx["other_token"])
    assert ok_mine and ok_theirs

    await _signal(
        ctx["code"],
        SIGNAL_ADMITTED,
        {
            "participant_id": "p1",
            "user_id": str(ctx["guest"].pk),
            "token": "media-credential",
        },
    )

    own = await mine.receive_json_from(timeout=5)
    other = await theirs.receive_json_from(timeout=5)

    assert own["type"] == other["type"] == "lobby.admitted"
    assert own["payload"]["token"] == "media-credential"
    assert "token" not in other["payload"]
    # Redacted, not blanked: the other member still learns the row changed.
    assert other["payload"]["participant_id"] == "p1"
    assert "media-credential" not in json.dumps(other)

    await mine.disconnect()
    await theirs.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_the_socket_is_read_only_for_the_client():
    """Verdicts are host-only REST calls; the socket accepts no writes.

    The substrate answers an unaccepted client frame with ``error{bad_type}``
    rather than silence, so a client that tries learns why.
    """
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, _ = await _connect(ctx["code"], ctx["host_token"])
    assert connected

    await comm.send_json_to(
        {"v": 1, "type": "admit", "payload": {"participant_id": "p1"}}
    )
    frame = await comm.receive_json_from(timeout=5)
    assert frame["type"] == "error"
    assert frame["payload"]["code"] == "bad_type"
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_hello_is_answered_with_an_ephemeral_welcome():
    """The substrate's resume handshake, degenerate: nothing to replay."""
    ctx = await database_sync_to_async(_make_room)(True)
    comm, connected, _ = await _connect(ctx["code"], ctx["guest_token"])
    assert connected

    await comm.send_json_to({"v": 1, "type": "hello", "payload": {}})
    frame = await comm.receive_json_from(timeout=5)
    assert frame["type"] == "welcome"
    assert frame["payload"] == {"ephemeral": True}
    assert frame["stream"] == lobby_stream(ctx["code"])
    await comm.disconnect()


# ── the emit path, without a socket ──────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_a_lobby_signal_may_not_claim_a_protocol_frame_type():
    """The three types are this module's word; ``live`` is the protocol's."""
    with pytest.raises(ValueError):
        await _signal("aaa-bbbb-ccc", "live", {"participant_id": "p1"})
