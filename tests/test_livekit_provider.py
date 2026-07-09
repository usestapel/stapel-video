"""LiveKitProvider unit tests — token building + egress/webhook requests with
the LiveKit SDK and HTTP client fully mocked (no network, no livekit-api install)."""
import pytest

from stapel_video.providers import livekit as lk
from stapel_video.providers.base import VideoProviderError


# ── Fake LiveKit SDK ────────────────────────────────────────────────────────


class _FakeAccessToken:
    def __init__(self, api_key=None, api_secret=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.identity = None
        self.name = None
        self.grants = None

    def with_identity(self, identity):
        self.identity = identity
        return self

    def with_name(self, name):
        self.name = name
        return self

    def with_ttl(self, ttl):
        self.ttl = ttl
        return self

    def with_grants(self, grants):
        self.grants = grants
        return self

    def to_jwt(self):
        return f"jwt::{self.identity}::{getattr(self.grants, 'room', None)}"


class _FakeGrants:
    def __init__(self, room_join=False, room=None, room_record=False):
        self.room_join = room_join
        self.room = room
        self.room_record = room_record


class _FakeEvent:
    event = "egress_ended"

    class egress_info:  # noqa: N801 - mimic protobuf attr access
        egress_id = "eg_9"
        status = 3

        class _FileResult:
            filename = "recordings/room.mp4"

        file_results = [_FileResult()]


class _FakeReceiver:
    def __init__(self, verifier):
        self.verifier = verifier

    def receive(self, body, auth):
        if auth == "bad":
            raise ValueError("signature mismatch")
        return _FakeEvent()


class _FakeVerifier:
    def __init__(self, api_key=None, api_secret=None):
        pass


class _FakeApi:
    AccessToken = _FakeAccessToken
    VideoGrants = _FakeGrants
    WebhookReceiver = _FakeReceiver
    TokenVerifier = _FakeVerifier


# ── Fake requests ───────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeRequests:
    RequestException = Exception

    def __init__(self):
        self.calls = []
        self.next_resp = _FakeResp(payload={"egress_id": "eg_1"})

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.next_resp


@pytest.fixture
def livekit_provider(monkeypatch, settings):
    settings.STAPEL_VIDEO = {
        "VIDEO_PROVIDER": "stapel_video.providers.livekit.LiveKitProvider",
        "LIVEKIT_URL": "wss://lk.example.com",
        "LIVEKIT_API_KEY": "key",
        "LIVEKIT_API_SECRET": "secret",
    }
    fake_requests = _FakeRequests()
    monkeypatch.setattr(lk, "_require_sdk", lambda: _FakeApi)
    monkeypatch.setattr(lk, "_require_requests", lambda: fake_requests)
    provider = lk.LiveKitProvider()
    provider._fake_requests = fake_requests
    return provider


def test_create_room_returns_join_code_as_ref(livekit_provider):
    assert livekit_provider.create_room("abc-defg-hij") == "abc-defg-hij"


def test_mint_join_token_builds_scoped_token(livekit_provider):
    token = livekit_provider.mint_join_token("abc-defg-hij", 42, "Alice")
    # Identity is user-id-prefixed + unique suffix; grant is scoped to the room.
    assert token.startswith("jwt::42_")
    assert token.endswith("::abc-defg-hij")


def test_start_room_egress_posts_composite_request(livekit_provider):
    egress_id = livekit_provider.start_room_egress("abc-defg-hij", "recordings/x.mp4")
    assert egress_id == "eg_1"
    call = livekit_provider._fake_requests.calls[-1]
    assert call["url"].endswith("/twirp/livekit.Egress/StartRoomCompositeEgress")
    assert call["url"].startswith("https://lk.example.com")  # wss -> https
    assert call["json"]["room_name"] == "abc-defg-hij"
    assert call["json"]["file_outputs"][0]["filepath"] == "recordings/x.mp4"
    assert "Authorization" in call["headers"]


def test_start_room_egress_raises_on_non_200(livekit_provider):
    livekit_provider._fake_requests.next_resp = _FakeResp(status_code=500, text="boom")
    with pytest.raises(VideoProviderError):
        livekit_provider.start_room_egress("abc-defg-hij", "recordings/x.mp4")


def test_stop_room_egress_posts_stop(livekit_provider):
    livekit_provider._fake_requests.next_resp = _FakeResp(status_code=200)
    livekit_provider.stop_room_egress("eg_1")
    call = livekit_provider._fake_requests.calls[-1]
    assert call["url"].endswith("/twirp/livekit.Egress/StopEgress")
    assert call["json"] == {"egress_id": "eg_1"}


def test_stop_room_egress_tolerates_already_finished(livekit_provider):
    livekit_provider._fake_requests.next_resp = _FakeResp(status_code=404, text="egress not found")
    # No raise: the goal (no active egress) is met.
    livekit_provider.stop_room_egress("eg_gone")


def test_parse_webhook_normalizes_egress_event(livekit_provider):
    parsed = livekit_provider.parse_webhook(b"{}", "Bearer good")
    assert parsed["event"] == "egress_ended"
    assert parsed["egress_id"] == "eg_9"
    assert parsed["storage_key"] == "recordings/room.mp4"


def test_parse_webhook_rejects_bad_signature(livekit_provider):
    with pytest.raises(VideoProviderError):
        livekit_provider.parse_webhook(b"{}", "bad")
