"""In-process VideoProvider fake for tests — never touches the network.

Tokens are deterministic strings; egress ids are counters; ``parse_webhook``
treats the body as JSON and a caller-supplied ``valid`` flag stands in for a
signature check. Exercises the full seam without LiveKit installed.
"""
import json

from stapel_video.providers.base import VideoProvider, VideoProviderError


class FakeProvider(VideoProvider):
    _egress_seq = 0

    def create_room(self, join_code: str, *, scope_key: str = "") -> str:
        return f"fake-room::{join_code}"

    def mint_join_token(self, provider_room_ref, user_id, user_name) -> str:
        return f"faketoken::{provider_room_ref}::{user_id}"

    def start_room_egress(self, provider_room_ref: str, storage_key: str) -> str:
        FakeProvider._egress_seq += 1
        return f"eg_{FakeProvider._egress_seq}"

    def stop_room_egress(self, egress_id: str) -> None:
        return None

    def parse_webhook(self, body: bytes, auth_header: str) -> dict:
        # A signed provider sends a valid Authorization header; the fake treats
        # a missing/"invalid" header as a bad signature.
        if not auth_header or auth_header == "invalid":
            raise VideoProviderError("bad signature")
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise VideoProviderError(f"malformed body: {exc}") from exc
        return {
            "event": data.get("event"),
            "egress_id": data.get("egress_id"),
            "status": data.get("status"),
            "storage_key": data.get("storage_key"),
        }
