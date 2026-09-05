"""1:1 calls — the state machine, the gate, and the three witnesses of an end.

Four properties this file exists to hold, because each of them fails silently
if it breaks:

* a third party gets **404**, identically to a call that does not exist;
* one live call per user, including the same-role race;
* a ring that runs out is ``missed`` whether or not the sweeper ever runs;
* whoever ends the call, the duration is recorded exactly once.
"""
import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from stapel_video.calls.models import Call, CallEndReason, CallState
from stapel_video.tests.fakeprovider import FakeProvider

pytestmark = pytest.mark.django_db


THREAD = "conv-1"


@pytest.fixture(autouse=True)
def _open_gate(settings):
    """The default authorizer asks a chat service that is not running here.

    Every test but the authorizer's own is about the state machine, so the
    gate is opened deliberately and once — rather than each test learning to
    ignore a 403, which is how a gate ends up disabled in production by the
    same reflex.
    """
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.allow_any",
        "CALL_THREAD_MESSAGE_FUNCTION": "",
        "CALL_NOTIFY_ON_RING": False,
    }


@pytest.fixture
def third_user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="carol", email="carol@example.com", password="x"
    )


def _client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


def _place(api_client, caller, callee, **extra):
    body = {"callee_id": str(callee.pk), "thread_key": THREAD, **extra}
    return _client(api_client, caller).post(reverse("video-calls"), body, format="json")


# ── Placing a call ─────────────────────────────────────────────────────────


def test_placing_a_call_rings_and_hands_the_caller_a_token(api_client, user, other_user):
    resp = _place(api_client, user, other_user, client_session_id="tab-1")
    assert resp.status_code == 201, resp.data
    body = resp.data
    assert body["call"]["state"] == CallState.RINGING
    assert body["call"]["caller_id"] == str(user.pk)
    assert body["call"]["callee_id"] == str(other_user.pk)
    assert body["call"]["room_name"] == f"call-{body['call']['id']}"
    assert body["token"]
    assert body["url"] == "wss://fake.example/rtc"
    # The ring carries a deadline the client counts down against, so a clock
    # skew shows as a second rather than as an overlay outliving its call.
    assert body["call"]["expires_at"]


def test_the_room_is_provisioned_capped_at_two(api_client, user, other_user):
    _place(api_client, user, other_user)
    assert len(FakeProvider.call_rooms) == 1
    room_ref, max_participants, _empty = FakeProvider.call_rooms[0]
    assert room_ref.startswith("call-")
    # The second lock. A grant is a signed string and a signed string can be
    # copied; a media server that refuses the third connection cannot be
    # talked out of it.
    assert max_participants == 2


def test_the_grant_is_the_call_grant_with_the_call_ttl(api_client, user, other_user, settings):
    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "CALL_TOKEN_TTL_SECONDS": 900}
    _place(api_client, user, other_user, client_session_id="tab-1")
    assert len(FakeProvider.call_mints) == 1
    room_ref, minted_for, _name, _avatar, session, _scope, ttl = FakeProvider.call_mints[0]
    assert minted_for == str(user.pk)
    assert session == "tab-1"
    assert ttl == 900
    # And NOT through the room path — a room token carries the permissive
    # default grant and the hour-long room TTL.
    assert FakeProvider.mints == []


def test_a_provider_without_the_call_methods_still_places_a_call(
    api_client, user, other_user, settings
):
    """The fallback works, and the test asserts what it costs.

    A provider predating 0.11.0 has neither the cap nor the explicit grant.
    The call still connects — that is the point of the fallback — but it is
    minted through ``mint_join_token``, which is exactly the downgrade the
    warning in ``services._mint`` names.
    """
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "VIDEO_PROVIDER": "stapel_video.tests.test_calls.LegacyProvider",
    }
    resp = _place(api_client, user, other_user)
    assert resp.status_code == 201
    assert FakeProvider.mints and not FakeProvider.call_mints


class LegacyProvider(FakeProvider):
    """A 0.10.0-era provider: no call room, no call token, no client url."""

    def ensure_call_room(self, *args, **kwargs):
        raise NotImplementedError

    def mint_call_token(self, *args, **kwargs):
        raise NotImplementedError

    def client_url(self):
        raise NotImplementedError


def test_calling_yourself_is_not_a_call(api_client, user):
    resp = _place(api_client, user, user)
    assert resp.status_code == 400
    assert resp.data["localizable_error"] == "error.400.video_call_invalid_callee"


def test_calling_a_stranger_who_does_not_exist(api_client, user):
    resp = _client(api_client, user).post(
        reverse("video-calls"),
        {"callee_id": str(uuid.uuid4()), "thread_key": THREAD},
        format="json",
    )
    assert resp.status_code == 400


def test_a_provider_failure_leaves_a_failed_call_not_a_ringing_one(
    api_client, user, other_user, settings
):
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "VIDEO_PROVIDER": "stapel_video.tests.test_calls.BrokenProvider",
    }
    resp = _place(api_client, user, other_user)
    assert resp.status_code == 503
    call = Call.objects.get()
    assert call.state == CallState.FAILED
    assert call.end_reason == CallEndReason.PROVIDER_ERROR
    # And the parties are free to try again immediately: a failed call is not
    # a live one, so the busy gate does not hold them hostage to it.
    assert not call.is_live


class BrokenProvider(FakeProvider):
    def ensure_call_room(self, *args, **kwargs):
        from stapel_video.providers import VideoProviderError

        raise VideoProviderError("the media server is down")


# ── Authorization: the third party ─────────────────────────────────────────


@pytest.mark.parametrize(
    "route,method",
    [
        ("video-call-detail", "get"),
        ("video-call-accept", "post"),
        ("video-call-decline", "post"),
        ("video-call-hangup", "post"),
        ("video-call-token", "post"),
    ],
)
def test_a_third_user_gets_404_on_every_action(
    api_client, user, other_user, third_user, route, method
):
    """404, not 403, and on every verb — not only the read.

    A call id names two people and the conversation they are having, so
    confirming that a guessed id is a real call IS the leak. "No such call"
    and "not your call" are one answer.
    """
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    client = _client(api_client, third_user)
    resp = getattr(client, method)(reverse(route, args=[call_id]), {}, format="json")
    assert resp.status_code == 404
    assert resp.data["localizable_error"] == "error.404.video_call_not_found"


def test_the_authorizer_refuses_a_call_with_no_thread(api_client, user, other_user, settings):
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.thread_participants",
    }
    resp = _client(api_client, user).post(
        reverse("video-calls"), {"callee_id": str(other_user.pk)}, format="json"
    )
    assert resp.status_code == 403
    assert Call.objects.count() == 0


def test_the_authorizer_refuses_when_nothing_can_answer(
    api_client, user, other_user, settings
):
    """A bus that cannot answer is not a bus that said yes.

    The failure this guards is silent and total: an outage of the
    conversation service would otherwise turn the gate off for everybody at
    once, and nothing about it would look wrong.
    """
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.thread_participants",
        "CALL_PARTICIPANTS_FUNCTION": "chat.not_registered_here",
    }
    resp = _place(api_client, user, other_user)
    assert resp.status_code == 403


def test_the_authorizer_admits_two_members_of_the_thread(
    api_client, user, other_user, settings
):
    from stapel_core.comm import function, function_registry

    name = "test.participants"

    @function(name)
    def _participants(payload):
        return {
            "conversations": {
                THREAD: {
                    "exists": True,
                    "participants": [
                        {"user_id": str(user.pk)},
                        {"user_id": str(other_user.pk)},
                    ],
                }
            }
        }

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.thread_participants",
        "CALL_PARTICIPANTS_FUNCTION": name,
    }
    try:
        assert _place(api_client, user, other_user).status_code == 201
    finally:
        function_registry._providers.pop(name, None)


def test_the_authorizer_refuses_somebody_outside_the_thread(
    api_client, user, other_user, third_user, settings
):
    from stapel_core.comm import function, function_registry

    name = "test.participants_two"

    @function(name)
    def _participants(payload):
        return {
            "conversations": {
                THREAD: {
                    "exists": True,
                    "participants": [
                        {"user_id": str(user.pk)},
                        {"user_id": str(other_user.pk)},
                    ],
                }
            }
        }

    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.thread_participants",
        "CALL_PARTICIPANTS_FUNCTION": name,
    }
    try:
        # A member of the thread cannot ring somebody who is not in it.
        assert _place(api_client, user, third_user).status_code == 403
    finally:
        function_registry._providers.pop(name, None)


# ── One live call per user ─────────────────────────────────────────────────


def test_a_second_call_by_the_same_caller_is_refused(
    api_client, user, other_user, third_user
):
    assert _place(api_client, user, other_user).status_code == 201
    resp = _place(api_client, user, third_user)
    assert resp.status_code == 409
    assert resp.data["localizable_error"] == "error.409.video_call_busy"


def test_a_call_to_somebody_already_ringing_is_refused(
    api_client, user, other_user, third_user
):
    """The gate is about the PAIR, not about the caller.

    This is the case the two partial unique constraints cannot express — the
    callee is busy in the callee role and the new caller is free — and the
    reason the query in _refuse_if_busy is the gate rather than the index.
    """
    assert _place(api_client, user, other_user).status_code == 201
    resp = _place(api_client, third_user, other_user)
    assert resp.status_code == 409


def test_a_finished_call_frees_both_parties(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    _client(api_client, user).post(reverse("video-call-hangup", args=[call_id]), {}, format="json")
    assert _place(api_client, user, other_user).status_code == 201


def test_accept_refuses_a_party_who_got_onto_another_call_meanwhile(
    api_client, user, other_user, third_user
):
    """The cross-role race, resolved where it has to be.

    Two rings can exist at once (the create gate loses that race by design).
    At most one of them may become an ACCEPTED call, and accept is where that
    is decided — so the losing ring answers 409 instead of putting somebody
    into two conversations.
    """
    # `user` is the CALLER of a live call...
    _place(api_client, user, other_user)
    # ...and, forged past the gate exactly as the race would leave it, the
    # CALLEE of another. Neither partial unique constraint is violated by
    # this pair of rows — which is precisely why the constraints are a
    # backstop and the check in accept is the decision.
    second = Call.objects.create(
        thread_key=THREAD,
        caller=third_user,
        callee=user,
        room_name="call-forged",
        state=CallState.RINGING,
    )
    resp = _client(api_client, user).post(
        reverse("video-call-accept", args=[second.pk]), {}, format="json"
    )
    assert resp.status_code == 409
    assert resp.data["localizable_error"] == "error.409.video_call_busy"


# ── The state machine ──────────────────────────────────────────────────────


def test_accept_hands_the_callee_their_own_token(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    resp = _client(api_client, other_user).post(
        reverse("video-call-accept", args=[call_id]), {"client_session_id": "phone"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["call"]["state"] == CallState.ACCEPTED
    assert resp.data["call"]["answered_at"]
    assert resp.data["token"]
    # Minted for the CALLEE, on the second mint — the ring frame carried none.
    assert FakeProvider.call_mints[-1][1] == str(other_user.pk)


def test_the_caller_may_not_accept_their_own_call(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    resp = _client(api_client, user).post(
        reverse("video-call-accept", args=[call_id]), {}, format="json"
    )
    assert resp.status_code == 403


def test_the_caller_may_not_decline(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    resp = _client(api_client, user).post(
        reverse("video-call-decline", args=[call_id]), {}, format="json"
    )
    assert resp.status_code == 403


def test_decline_is_terminal(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    resp = _client(api_client, other_user).post(
        reverse("video-call-decline", args=[call_id]), {}, format="json"
    )
    assert resp.status_code == 200
    assert resp.data["state"] == CallState.DECLINED
    assert resp.data["end_reason"] == CallEndReason.DECLINED
    # And nothing moves it again.
    again = _client(api_client, other_user).post(
        reverse("video-call-decline", args=[call_id]), {}, format="json"
    )
    assert again.status_code == 409
    accept = _client(api_client, other_user).post(
        reverse("video-call-accept", args=[call_id]), {}, format="json"
    )
    assert accept.status_code == 409


def test_hangup_records_the_duration_once(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    _client(api_client, other_user).post(
        reverse("video-call-accept", args=[call_id]), {}, format="json"
    )
    call = Call.objects.get(pk=call_id)
    Call.objects.filter(pk=call_id).update(
        answered_at=timezone.now() - timedelta(seconds=192)
    )
    resp = _client(api_client, user).post(
        reverse("video-call-hangup", args=[call_id]), {}, format="json"
    )
    assert resp.status_code == 200
    assert resp.data["state"] == CallState.ENDED
    assert 190 <= resp.data["duration_seconds"] <= 195
    first_ended_at = Call.objects.get(pk=call_id).ended_at

    # A second hangup by the other party is a no-op, not a second duration.
    again = _client(api_client, other_user).post(
        reverse("video-call-hangup", args=[call_id]), {}, format="json"
    )
    assert again.status_code == 409
    assert Call.objects.get(pk=call_id).ended_at == first_ended_at
    assert call.pk == uuid.UUID(call_id)


def test_a_caller_hanging_up_mid_ring_ends_rather_than_misses(
    api_client, user, other_user
):
    """Somebody was there and stopped waiting.

    A different fact from nobody answering, and the thread line says so — so
    the state has to be different too.
    """
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    resp = _client(api_client, user).post(
        reverse("video-call-hangup", args=[call_id]), {}, format="json"
    )
    assert resp.data["state"] == CallState.ENDED
    assert resp.data["end_reason"] == CallEndReason.HANGUP
    assert resp.data["duration_seconds"] == 0


# ── The ring timeout ───────────────────────────────────────────────────────


def test_an_overdue_ring_reads_as_missed_without_the_sweeper(
    api_client, user, other_user, settings
):
    """The answer is right whether or not anything is scheduled.

    A deployment whose beat schedule is misconfigured then has late thread
    lines and late pushes — not calls that ring forever, and not an accept
    that succeeds four minutes after the caller gave up.
    """
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    Call.objects.filter(pk=call_id).update(
        started_at=timezone.now() - timedelta(seconds=120)
    )
    resp = _client(api_client, user).get(reverse("video-call-detail", args=[call_id]))
    assert resp.data["state"] == CallState.MISSED
    assert resp.data["end_reason"] == CallEndReason.RING_TIMEOUT
    assert resp.data["expires_at"] is None


def test_an_overdue_ring_cannot_be_accepted(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    Call.objects.filter(pk=call_id).update(
        started_at=timezone.now() - timedelta(seconds=120)
    )
    resp = _client(api_client, other_user).post(
        reverse("video-call-accept", args=[call_id]), {}, format="json"
    )
    assert resp.status_code == 409


def test_the_sweeper_expires_a_ring_nobody_ever_reads(api_client, user, other_user):
    from stapel_video.calls.sweeper import sweep_calls

    call_id = _place(api_client, user, other_user).data["call"]["id"]
    Call.objects.filter(pk=call_id).update(
        started_at=timezone.now() - timedelta(seconds=120)
    )
    result = sweep_calls()
    assert result["expired"] == 1
    assert Call.objects.get(pk=call_id).state == CallState.MISSED


# ── Closing: the three witnesses ───────────────────────────────────────────


def _accept(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    _client(api_client, other_user).post(
        reverse("video-call-accept", args=[call_id]), {}, format="json"
    )
    return Call.objects.get(pk=call_id)


def test_a_participant_left_webhook_ends_the_call_and_still_meters_it(
    api_client, user, other_user
):
    """One event, two facts, and the composite builtin owes both.

    The regression this guards is the quiet one: composing the call closer
    over the presence handler and forgetting the span means calls end
    correctly and stop being billable, which nothing notices until a month
    is invoiced.
    """
    import json

    from stapel_video.models import ParticipantSpan

    call = _accept(api_client, user, other_user)
    now = int(timezone.now().timestamp())
    body = json.dumps(
        {
            "event": "participant_left",
            "created_at": now,
            "room": {"name": call.room_name, "sid": "RM_1"},
            "participant": {
                "identity": f"{user.pk}_tab-1",
                "joined_at": now - 100,
                "name": "Alice",
            },
        }
    ).encode()
    resp = api_client.post(
        reverse("video-webhook"), body, content_type="application/json",
        HTTP_AUTHORIZATION="valid",
    )
    assert resp.status_code == 200
    call.refresh_from_db()
    assert call.state == CallState.ENDED
    assert call.end_reason == CallEndReason.REMOTE_LEFT
    span = ParticipantSpan.objects.get(room_key=call.room_name)
    assert span.left_at is not None


def test_a_room_finished_webhook_ends_the_call(api_client, user, other_user):
    import json

    call = _accept(api_client, user, other_user)
    body = json.dumps(
        {
            "event": "room_finished",
            "created_at": int(timezone.now().timestamp()),
            "room": {"name": call.room_name, "sid": "RM_1"},
        }
    ).encode()
    resp = api_client.post(
        reverse("video-webhook"), body, content_type="application/json",
        HTTP_AUTHORIZATION="valid",
    )
    assert resp.status_code == 200
    call.refresh_from_db()
    assert call.state == CallState.ENDED
    assert call.end_reason == CallEndReason.ROOM_FINISHED


def test_the_sweeper_closes_a_call_whose_room_is_gone(api_client, user, other_user):
    from stapel_video.calls.sweeper import sweep_calls

    call = _accept(api_client, user, other_user)
    Call.objects.filter(pk=call.pk).update(
        answered_at=timezone.now() - timedelta(minutes=5)
    )
    # The provider reports no such room — which for a lazy media server is
    # the same fact as "nobody is in there".
    FakeProvider.live = {}
    result = sweep_calls()
    assert result["reconciled"] == 1
    call.refresh_from_db()
    assert call.state == CallState.ENDED
    assert call.end_reason == CallEndReason.RECONCILED


def test_the_sweeper_leaves_a_call_that_is_still_connecting_alone(
    api_client, user, other_user
):
    """The defect this guards presents as "calls hang up by themselves".

    An accepted call is a promise about two browsers that have not dialled
    yet: the token was handed over milliseconds ago and the room is still
    empty. Without the connect grace the reconciler kills every call within
    one sweep interval of it being answered, with a healthy webhook path and
    a green everything.
    """
    from stapel_video.calls.sweeper import sweep_calls

    call = _accept(api_client, user, other_user)
    FakeProvider.live = {}
    result = sweep_calls()
    assert result["reconciled"] == 0
    call.refresh_from_db()
    assert call.state == CallState.ACCEPTED


def test_the_sweeper_leaves_a_call_with_both_parties_connected(
    api_client, user, other_user
):
    from stapel_video.calls.sweeper import sweep_calls

    call = _accept(api_client, user, other_user)
    Call.objects.filter(pk=call.pk).update(
        answered_at=timezone.now() - timedelta(minutes=5)
    )
    FakeProvider.live = {
        call.room_name: [
            {"identity": f"{user.pk}_a", "joined_at": 1},
            {"identity": f"{other_user.pk}_b", "joined_at": 1},
        ]
    }
    assert sweep_calls()["reconciled"] == 0
    call.refresh_from_db()
    assert call.state == CallState.ACCEPTED


def test_the_sweeper_caps_a_call_nobody_hangs_up(api_client, user, other_user, settings):
    from stapel_video.calls.sweeper import sweep_calls

    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "CALL_MAX_DURATION_SECONDS": 60}
    call = _accept(api_client, user, other_user)
    Call.objects.filter(pk=call.pk).update(
        answered_at=timezone.now() - timedelta(minutes=5)
    )
    assert sweep_calls()["capped"] == 1
    call.refresh_from_db()
    assert call.state == CallState.ENDED
    assert call.end_reason == CallEndReason.MAX_DURATION


def test_a_webhook_for_an_unknown_room_ends_nothing(api_client, user, other_user):
    import json

    call = _accept(api_client, user, other_user)
    body = json.dumps(
        {
            "event": "room_finished",
            "created_at": int(timezone.now().timestamp()),
            "room": {"name": "call-somebody-elses", "sid": "RM_2"},
        }
    ).encode()
    api_client.post(
        reverse("video-webhook"), body, content_type="application/json",
        HTTP_AUTHORIZATION="valid",
    )
    call.refresh_from_db()
    assert call.state == CallState.ACCEPTED


# ── The active-call read, and the token re-mint ────────────────────────────


def test_active_call_answers_both_parties_and_nobody_else(
    api_client, user, other_user, third_user
):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    for party in (user, other_user):
        resp = _client(api_client, party).get(reverse("video-call-active"))
        assert resp.data["call"]["id"] == call_id
    assert _client(api_client, third_user).get(reverse("video-call-active")).data["call"] is None


def test_active_call_is_null_rather_than_absent(api_client, user):
    resp = _client(api_client, user).get(reverse("video-call-active"))
    assert resp.status_code == 200
    assert resp.data["call"] is None


def test_active_call_does_not_report_a_ring_that_has_run_out(
    api_client, user, other_user
):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    Call.objects.filter(pk=call_id).update(
        started_at=timezone.now() - timedelta(seconds=120)
    )
    assert _client(api_client, user).get(reverse("video-call-active")).data["call"] is None


def test_the_token_can_be_reminted_while_the_call_is_live(api_client, user, other_user):
    """Without this, the TTL is a ceiling on reconnecting.

    A media token is presented again on every full reconnect and nothing
    re-mints it automatically, so a client coming back from a tunnel with an
    expired grant fails in a way that reads as a network fault.
    """
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    resp = _client(api_client, user).post(
        reverse("video-call-token", args=[call_id]), {"client_session_id": "tab-2"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["token"]
    assert resp.data["url"] == "wss://fake.example/rtc"


def test_a_finished_call_mints_no_more_tokens(api_client, user, other_user):
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    _client(api_client, user).post(reverse("video-call-hangup", args=[call_id]), {}, format="json")
    resp = _client(api_client, user).post(
        reverse("video-call-token", args=[call_id]), {}, format="json"
    )
    assert resp.status_code == 409
