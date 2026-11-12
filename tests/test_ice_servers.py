"""ice_servers on every media grant: STUN + time-limited TURN, empty by default."""
import base64
import hashlib
import hmac

import pytest
from django.test import override_settings
from django.urls import reverse

from stapel_video import checks
from stapel_video.ice import ice_servers_for, turn_credential

pytestmark = pytest.mark.django_db

STUN = ["stun:stun.example.com:3478"]
TURN = ["turn:turn.example.com:3478?transport=udp", "turns:turn.example.com:443?transport=tcp"]
SECRET = "s3cret"


@pytest.fixture
def rtc(settings):
    def configure(**rtc):
        settings.STAPEL_VIDEO = {
            **settings.STAPEL_VIDEO,
            "CALL_AUTHORIZER": "stapel_video.calls.authorize.allow_any",
            "CALL_THREAD_MESSAGE_FUNCTION": "",
            "CALL_NOTIFY_ON_RING": False,
            **rtc,
        }

    configure()
    return configure


def _place(api_client, caller, callee):
    api_client.force_authenticate(user=caller)
    return api_client.post(
        reverse("video-calls"),
        {"callee_id": str(callee.pk), "thread_key": "conv-1"},
        format="json",
    )


def test_the_default_is_an_empty_list_not_a_third_party_host(rtc, api_client, user, other_user):
    resp = _place(api_client, user, other_user)
    assert resp.status_code == 201, resp.data
    assert resp.data["ice_servers"] == []


def test_stun_only(rtc, api_client, user, other_user):
    rtc(RTC_STUN_SERVERS=STUN)
    resp = _place(api_client, user, other_user)
    assert resp.data["ice_servers"] == [{"urls": STUN, "username": "", "credential": ""}]


def test_stun_and_turn_merge_with_a_per_user_time_limited_credential(
    rtc, api_client, user, other_user
):
    rtc(RTC_STUN_SERVERS=STUN, RTC_TURN_SERVERS=TURN, RTC_TURN_SECRET=SECRET,
        RTC_TURN_CREDENTIAL_TTL_SECONDS=600)
    resp = _place(api_client, user, other_user)
    stun, turn = resp.data["ice_servers"]
    assert stun == {"urls": STUN, "username": "", "credential": ""}
    assert turn["urls"] == TURN
    expiry, who = turn["username"].split(":")
    assert who == str(user.pk)
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), turn["username"].encode(), hashlib.sha1).digest()
    ).decode()
    assert turn["credential"] == expected
    assert SECRET not in str(resp.data)


def test_turn_without_a_secret_is_not_served(rtc, api_client, user, other_user):
    rtc(RTC_TURN_SERVERS=TURN)
    resp = _place(api_client, user, other_user)
    assert resp.data["ice_servers"] == []


def test_accept_and_remint_carry_the_list(rtc, api_client, user, other_user):
    rtc(RTC_STUN_SERVERS=STUN, RTC_TURN_SERVERS=TURN, RTC_TURN_SECRET=SECRET)
    call_id = _place(api_client, user, other_user).data["call"]["id"]
    api_client.force_authenticate(user=other_user)
    accepted = api_client.post(reverse("video-call-accept", args=[call_id]), {}, format="json")
    assert accepted.status_code == 200, accepted.data
    assert [s["urls"] for s in accepted.data["ice_servers"]] == [STUN, TURN]
    assert accepted.data["ice_servers"][1]["username"].endswith(f":{other_user.pk}")
    reminted = api_client.post(reverse("video-call-token", args=[call_id]), {}, format="json")
    assert reminted.status_code == 200, reminted.data
    assert [s["urls"] for s in reminted.data["ice_servers"]] == [STUN, TURN]


def test_room_grants_carry_it_and_a_lobby_wait_does_not(rtc, api_client, user, other_user):
    rtc(RTC_STUN_SERVERS=STUN)
    api_client.force_authenticate(user=user)
    created = api_client.post(
        "/video/api/v1/rooms", {"access_level": "restricted"}, format="json"
    )
    assert created.data["ice_servers"] == [{"urls": STUN, "username": "", "credential": ""}]
    api_client.force_authenticate(user=other_user)
    waiting = api_client.post(
        f"/video/api/v1/rooms/{created.data['room']['join_code']}/join", {}, format="json"
    )
    assert waiting.data["status"] == "waiting"
    assert waiting.data["ice_servers"] == []


def test_env_string_form_is_split(settings):
    settings.STAPEL_VIDEO = {"RTC_STUN_SERVERS": "stun:a.example:3478, stun:b.example:3478"}
    assert ice_servers_for(1)[0].urls == ["stun:a.example:3478", "stun:b.example:3478"]


def test_turn_credential_expiry_is_now_plus_ttl():
    username, _ = turn_credential("k", 7, ttl=60, now=1000.0)
    assert username == "1060:7"


@override_settings(DEBUG=False, STAPEL_VIDEO={})
def test_w008_warns_when_both_are_empty_outside_a_mock_posture():
    assert [m.id for m in checks.check_ice_servers_are_configured(None)] == ["stapel_video.W008"]


@override_settings(DEBUG=True, STAPEL_VIDEO={})
def test_w008_is_silent_in_debug():
    assert checks.check_ice_servers_are_configured(None) == []


@override_settings(
    DEBUG=False, STAPEL_VIDEO={},
    STAPEL_POSTURE={"PRESET": "public_space", "OPTIONS": {"stage": "prototype"}},
)
def test_w008_is_silent_in_a_prototype_posture():
    assert checks.check_ice_servers_are_configured(None) == []


@override_settings(DEBUG=False, STAPEL_VIDEO={"RTC_STUN_SERVERS": STUN})
def test_w008_is_silent_with_stun():
    assert checks.check_ice_servers_are_configured(None) == []


@override_settings(DEBUG=True, STAPEL_VIDEO={"RTC_TURN_SERVERS": TURN})
def test_w008_names_turn_without_a_secret_even_in_debug():
    msgs = checks.check_ice_servers_are_configured(None)
    assert [m.id for m in msgs] == ["stapel_video.W008"]
    assert "RTC_TURN_SECRET" in msgs[0].msg
