"""What a join token actually authorizes — decoded from the signed JWT.

Security audit 2026-09-11, I-1 / §7 item 2. ``mint_join_token`` built its
grant as ``VideoGrants(room_join=True, room=…)`` and let the SDK's own
defaults decide everything else: every participant could publish, subscribe,
open a data channel and share any source, because the dataclass says so —
not because this module decided so. An authorization rule inherited from a
vendor's defaults is a rule nobody wrote and nobody can find; the next SDK
release is free to change it.

These tests use the REAL livekit SDK (the rest of the provider suite mocks
it) and read the ``video`` claim out of the signed token, because the claim
is the only thing the media server actually reads.
"""
import pytest

pytest.importorskip("livekit.api")
jwt = pytest.importorskip("jwt")

from stapel_video.providers import livekit as lk  # noqa: E402

SECRET = "a-test-secret-at-least-32-bytes-long-ok"


@pytest.fixture
def provider(settings):
    settings.STAPEL_VIDEO = {
        "VIDEO_PROVIDER": "stapel_video.providers.livekit.LiveKitProvider",
        "LIVEKIT_URL": "wss://lk.example.com",
        "LIVEKIT_API_KEY": "key",
        "LIVEKIT_API_SECRET": SECRET,
    }
    return lk.LiveKitProvider()


def _video_claim(token: str) -> dict:
    return jwt.decode(token, SECRET, algorithms=["HS256"], audience=None,
                      options={"verify_aud": False})["video"]


def test_the_default_join_grant_is_stated_in_full(provider):
    claim = _video_claim(provider.mint_join_token("abc-defg-hij", 42, "Alice"))
    assert claim == {
        "room": "abc-defg-hij",
        "roomJoin": True,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
        "canUpdateOwnMetadata": False,
    }


def test_a_read_only_seat_is_one_argument(provider):
    claim = _video_claim(provider.mint_join_token(
        "abc-defg-hij", 42, "Alice", can_publish=False, can_publish_data=False,
    ))
    assert claim == {
        "room": "abc-defg-hij",
        "roomJoin": True,
        "canPublish": False,
        "canSubscribe": True,
        "canPublishData": False,
        "canUpdateOwnMetadata": False,
    }


def test_publish_sources_can_be_narrowed(provider):
    claim = _video_claim(provider.mint_join_token(
        "abc-defg-hij", 42, "Alice",
        can_publish_sources=["camera", "microphone"],
    ))
    assert claim["canPublishSources"] == ["camera", "microphone"]


def test_the_participant_cannot_rewrite_its_own_metadata(provider):
    """The metadata blob carries the avatar the other clients render and the
    scope_key the usage meter partitions on. A participant that may rewrite
    it can re-tag its own airtime onto another tenant."""
    claim = _video_claim(provider.mint_join_token("abc-defg-hij", 42, "Alice"))
    assert claim["canUpdateOwnMetadata"] is False
