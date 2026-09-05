"""The ring: what goes on the wire, who may listen, and what leaves the app.

Three surfaces, and the reason each is tested separately from the state
machine is that each fails silently on its own — a call that works perfectly
and never rings anybody looks, from the server's side, exactly like a call
that works.
"""
import json

import pytest
from django.urls import reverse

from stapel_video.calls import realtime as call_realtime
from stapel_video.calls.models import Call, CallState

pytestmark = pytest.mark.django_db

THREAD = "conv-ring"


@pytest.fixture(autouse=True)
def _open_gate(settings):
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.allow_any",
        "CALL_THREAD_MESSAGE_FUNCTION": "",
        "CALL_NOTIFY_ON_RING": False,
    }


@pytest.fixture
def frames(monkeypatch):
    """Every frame this module puts on a call inbox, in order.

    Patched at ``notify_user`` rather than at ``signal()``: the assertion
    worth making is about the (recipient, type, payload) triple this module
    chose, not about the substrate's envelope — which stapel-realtime already
    tests and which this module must not restate.
    """
    captured = []
    real = call_realtime.notify_user

    def _capture(user_id, type, payload):
        captured.append((str(user_id), type, payload))
        return real(user_id, type, payload)

    monkeypatch.setattr(call_realtime, "notify_user", _capture)
    import stapel_video.calls.services as services

    monkeypatch.setattr(services, "notify_user", _capture)
    return captured


def _client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


def _place(api_client, caller, callee):
    return _client(api_client, caller).post(
        reverse("video-calls"),
        {"callee_id": str(callee.pk), "thread_key": THREAD},
        format="json",
    )


# ── The stream key ─────────────────────────────────────────────────────────


def test_the_inbox_stream_is_addressed_to_one_person(user):
    assert call_realtime.user_stream(user.pk) == f"video:user:{user.pk}"


def test_a_typo_in_a_signal_type_raises_here_rather_than_reaching_nobody(user):
    with pytest.raises(ValueError):
        call_realtime.notify_user(user.pk, "call.incomming", {})


# ── The four frames ────────────────────────────────────────────────────────


def test_the_ring_goes_to_the_callee_and_carries_no_credential(
    api_client, user, other_user, frames
):
    """The absence of a token is the point, not an omission.

    The lobby stream has to redact a media token for every socket its frame
    does not name. A ring carries none — the callee's grant comes back from
    an authenticated POST — so there is no redaction rule for a future edit
    to drop.
    """
    resp = _place(api_client, user, other_user)
    assert frames == [
        (
            str(other_user.pk),
            call_realtime.SIGNAL_INCOMING,
            frames[0][2],
        )
    ]
    payload = frames[0][2]
    assert payload["call_id"] == resp.data["call"]["id"]
    assert payload["caller_id"] == str(user.pk)
    assert payload["thread_key"] == THREAD
    assert payload["expires_at"]
    assert "token" not in payload
    assert not any("token" in str(v).lower() for v in payload.values())


def test_accept_tells_the_caller(api_client, user, other_user, frames):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    frames.clear()
    _client(api_client, other_user).post(
        reverse("video-call-accept", args=[call_id]), {}, format="json"
    )
    assert [(uid, t) for uid, t, _ in frames] == [
        (str(user.pk), call_realtime.SIGNAL_ACCEPTED)
    ]


def test_decline_tells_the_caller_and_closes_both_screens(
    api_client, user, other_user, frames
):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    frames.clear()
    _client(api_client, other_user).post(
        reverse("video-call-decline", args=[call_id]), {}, format="json"
    )
    types = [(uid, t) for uid, t, _ in frames]
    assert (str(user.pk), call_realtime.SIGNAL_DECLINED) in types
    # And `ended` reaches BOTH — a ringing overlay has to close on the
    # callee's own screen too, or the phone they just declined on keeps
    # ringing until the client's own timeout.
    assert (str(user.pk), call_realtime.SIGNAL_ENDED) in types
    assert (str(other_user.pk), call_realtime.SIGNAL_ENDED) in types


def test_the_end_frame_carries_the_duration_to_both_parties(
    api_client, user, other_user, frames
):
    from datetime import timedelta

    from django.utils import timezone

    call_id = _place(api_client, user, other_user).data["call"]["id"]
    _client(api_client, other_user).post(
        reverse("video-call-accept", args=[call_id]), {}, format="json"
    )
    Call.objects.filter(pk=call_id).update(
        answered_at=timezone.now() - timedelta(seconds=95)
    )
    frames.clear()
    _client(api_client, user).post(
        reverse("video-call-hangup", args=[call_id]), {}, format="json"
    )
    ended = [f for f in frames if f[1] == call_realtime.SIGNAL_ENDED]
    assert {uid for uid, _, _ in ended} == {str(user.pk), str(other_user.pk)}
    for _uid, _type, payload in ended:
        assert payload["state"] == CallState.ENDED
        assert 93 <= payload["duration_seconds"] <= 97


def test_a_ring_that_runs_out_closes_the_callee_screen_too(
    api_client, user, other_user, frames
):
    from datetime import timedelta

    from django.utils import timezone

    from stapel_video.calls.sweeper import sweep_calls

    call_id = _place(api_client, user, other_user).data["call"]["id"]
    Call.objects.filter(pk=call_id).update(
        started_at=timezone.now() - timedelta(seconds=120)
    )
    frames.clear()
    sweep_calls()
    ended = [f for f in frames if f[1] == call_realtime.SIGNAL_ENDED]
    assert {uid for uid, _, _ in ended} == {str(user.pk), str(other_user.pk)}
    assert ended[0][2]["state"] == CallState.MISSED


# ── The push ───────────────────────────────────────────────────────────────


@pytest.fixture
def pushes(monkeypatch):
    """Every ``request_notification`` this module makes."""
    captured = []

    def _request(notification_type, **kwargs):
        captured.append((notification_type, kwargs))
        return True

    import stapel_video.calls.notify as notify

    monkeypatch.setattr(notify, "_request", lambda t, call: captured.append((t, call)))
    return captured


def test_a_ring_also_pushes_when_the_axis_is_on(
    api_client, user, other_user, pushes, settings
):
    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "CALL_NOTIFY_ON_RING": True}
    _place(api_client, user, other_user)
    from stapel_video.calls.notify import TYPE_INCOMING

    assert [t for t, _ in pushes] == [TYPE_INCOMING]


def test_the_ring_push_can_be_turned_off(api_client, user, other_user, pushes, settings):
    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "CALL_NOTIFY_ON_RING": False}
    _place(api_client, user, other_user)
    assert pushes == []


def test_a_missed_call_always_pushes(api_client, user, other_user, pushes, settings):
    """Independent of CALL_NOTIFY_ON_RING, and deliberately so.

    Turning the ring push off is a decision about noise while somebody is
    being called. A missed call is the one notification the feature is FOR:
    the person was not reachable, which is exactly when a durable message
    matters.
    """
    from datetime import timedelta

    from django.utils import timezone

    from stapel_video.calls.notify import TYPE_MISSED
    from stapel_video.calls.sweeper import sweep_calls

    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "CALL_NOTIFY_ON_RING": False}
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    Call.objects.filter(pk=call_id).update(
        started_at=timezone.now() - timedelta(seconds=120)
    )
    sweep_calls()
    assert [t for t, _ in pushes] == [TYPE_MISSED]


def test_a_failing_push_never_breaks_a_call(api_client, user, other_user, settings, monkeypatch):
    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "CALL_NOTIFY_ON_RING": True}
    import stapel_video.calls.notify as notify

    def _boom(*args, **kwargs):
        raise RuntimeError("the bus is down")

    monkeypatch.setattr(notify, "notify_incoming", _boom)
    assert _place(api_client, user, other_user).status_code == 201


# ── The thread line ────────────────────────────────────────────────────────


def test_the_thread_line_is_a_marker_with_its_one_argument():
    from stapel_video.calls.thread import call_body

    call = Call(state=CallState.ENDED)
    call.answered_at = None
    assert call_body(call) == "video.call.ended:0"
    assert call_body(Call(state=CallState.MISSED)) == "video.call.missed"
    assert call_body(Call(state=CallState.DECLINED)) == "video.call.declined"
    # A call that never rang gets no line: the callee was never told it
    # existed, and the caller was told by the 503.
    assert call_body(Call(state=CallState.FAILED)) == ""
    assert call_body(Call(state=CallState.RINGING)) == ""


def test_the_call_line_is_posted_through_the_named_function(
    api_client, user, other_user, settings
):
    from stapel_core.comm import function, function_registry

    posted = []
    name = "test.post_system_message"

    @function(name)
    def _post(payload):
        posted.append(payload)
        return {"message_id": "m1", "seq": 7}

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_THREAD_MESSAGE_FUNCTION": name,
    }
    try:
        call_id = _place(api_client, user, other_user).data["call"]["id"]
        _client(api_client, other_user).post(
            reverse("video-call-decline", args=[call_id]), {}, format="json"
        )
    finally:
        function_registry._providers.pop(name, None)

    assert posted == [
        {
            "conversation_id": THREAD,
            "body": "video.call.declined",
            # The call's own id, so an at-least-once redelivery writes one
            # line rather than two.
            "client_msg_id": f"video-call-{call_id}",
        }
    ]


def test_a_call_with_no_thread_writes_no_line(api_client, user, other_user, settings):
    from stapel_core.comm import function, function_registry

    posted = []
    name = "test.post_system_message_2"

    @function(name)
    def _post(payload):
        posted.append(payload)
        return {}

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_THREAD_MESSAGE_FUNCTION": name,
    }
    try:
        resp = _client(api_client, user).post(
            reverse("video-calls"), {"callee_id": str(other_user.pk)}, format="json"
        )
        call_id = resp.data["call"]["id"]
        _client(api_client, user).post(
            reverse("video-call-hangup", args=[call_id]), {}, format="json"
        )
    finally:
        function_registry._providers.pop(name, None)
    assert posted == []


def test_a_chat_service_that_is_down_never_breaks_a_hangup(
    api_client, user, other_user, settings
):
    """The library's bookkeeping does not outrank the user's button."""
    from stapel_core.comm import function, function_registry

    name = "test.post_system_message_3"

    @function(name)
    def _post(payload):
        raise RuntimeError("chat is restarting")

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_THREAD_MESSAGE_FUNCTION": name,
    }
    try:
        call_id = _place(api_client, user, other_user).data["call"]["id"]
        resp = _client(api_client, user).post(
            reverse("video-call-hangup", args=[call_id]), {}, format="json"
        )
    finally:
        function_registry._providers.pop(name, None)
    assert resp.status_code == 200
    assert Call.objects.get(pk=call_id).state == CallState.ENDED


# ── Live rooms: the rename push has to find a call ─────────────────────────


def test_a_person_on_a_call_is_in_a_live_room(api_client, user, other_user):
    """The silent one this closes.

    ``DefaultLiveRoomsProvider`` reads Room rows, and a Call writes none. Left
    alone it would answer an empty list for somebody who is on a call right
    now — so a rename would reach every conference tile and no call tile, with
    nothing raised and nothing logged.
    """
    from stapel_video.live_rooms import DefaultLiveRoomsProvider

    call_id = _place(api_client, user, other_user).data["call"]["id"]
    rooms = DefaultLiveRoomsProvider().live_rooms_for_user(user.pk)
    assert rooms == [f"call-{call_id}"]
    assert DefaultLiveRoomsProvider().live_rooms_for_user(other_user.pk) == [
        f"call-{call_id}"
    ]

    _client(api_client, user).post(
        reverse("video-call-hangup", args=[call_id]), {}, format="json"
    )
    assert DefaultLiveRoomsProvider().live_rooms_for_user(user.pk) == []


# ── The socket ─────────────────────────────────────────────────────────────


ORIGIN = "https://app.example.test"


def _browser_headers(token=None, origin=ORIGIN) -> list:
    """Handshake headers a browser can actually produce — see test_consumer.py.

    No ``Authorization``: ``new WebSocket(url)`` takes no headers, and a
    socket smoke-tested with one proves nothing about the handshake that
    happens in production.
    """
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if token is not None:
        headers.append((b"cookie", f"stapel_jwt={token}".encode()))
    assert not any(name == b"authorization" for name, _ in headers)
    return headers


async def _connect_inbox(token=None):
    from channels.testing import WebsocketCommunicator
    from stapel_realtime.asgi import build_websocket_application

    comm = WebsocketCommunicator(
        build_websocket_application(),
        "/ws/video/inbox",
        headers=_browser_headers(token),
    )
    connected, detail = await comm.connect(timeout=5)
    return comm, connected, detail


def _mint(user) -> str:
    from stapel_core.django.jwt.provider import jwt_provider

    access, _refresh = jwt_provider.create_tokens(user)
    return access


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_browser_opens_its_own_call_inbox_with_its_cookie_alone():
    """The whole handshake, on the real stack, exactly as a browser makes it.

    And the authorization is structural rather than checked: the route
    carries no id, so the consumer builds ``video:user:<id>`` from the
    authenticated scope and physically cannot name somebody else's ring.
    """
    from channels.db import database_sync_to_async
    from django.contrib.auth import get_user_model

    from stapel_video.calls.realtime import (
        SIGNAL_INCOMING,
        notify_user,
        user_stream,
    )

    User = get_user_model()
    callee = await database_sync_to_async(User.objects.create_user)(
        username="ringme", email="r@x.io", password="x"
    )
    token = await database_sync_to_async(_mint)(callee)

    comm, connected, detail = await _connect_inbox(token)
    assert connected, detail
    # The client's half of the protocol, exactly as @stapel/realtime does it.
    await comm.send_json_to({"v": 1, "type": "hello", "payload": {}})
    welcome = await comm.receive_json_from(timeout=5)
    assert welcome["type"] == "welcome"
    assert welcome["payload"]["ephemeral"] is True

    await database_sync_to_async(notify_user)(
        callee.pk, SIGNAL_INCOMING, {"call_id": "c1", "caller_id": "u2"}
    )
    frame = await comm.receive_json_from(timeout=5)
    # The fleet's v1 envelope, verbatim — the shape @stapel/realtime's
    # decodeFrame accepts and anything else silently drops.
    assert frame["v"] == 1
    assert frame["type"] == SIGNAL_INCOMING
    assert frame["stream"] == user_stream(callee.pk)
    assert frame["payload"]["call_id"] == "c1"
    await comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_unauthenticated_browser_gets_no_inbox():
    comm, connected, _detail = await _connect_inbox(None)
    assert not connected
    await comm.disconnect()


def test_the_websocket_route_carries_no_user_id():
    """A regression guard with a one-line failure and a large consequence.

    The day somebody adds ``<str:user_id>`` to this route to "make it
    symmetrical with the lobby", the consumer's key still comes from the
    scope — but the next edit, making the consumer read the kwarg, is a
    one-liner that hands anyone anybody's ring.
    """
    from stapel_video.routing import websocket_urlpatterns

    inbox = [p for p in websocket_urlpatterns if "inbox" in str(p.pattern)]
    assert len(inbox) == 1
    assert str(inbox[0].pattern) == "ws/video/inbox"


def test_the_webhook_ingress_is_still_signature_checked(api_client, user, other_user):
    """The call closer rides the webhook path; the path's gate is unchanged."""
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    call = Call.objects.get(pk=call_id)
    body = json.dumps(
        {"event": "room_finished", "room": {"name": call.room_name}}
    ).encode()
    resp = api_client.post(
        reverse("video-webhook"), body, content_type="application/json",
        HTTP_AUTHORIZATION="invalid",
    )
    assert resp.status_code == 400
    assert Call.objects.get(pk=call_id).state == CallState.RINGING
