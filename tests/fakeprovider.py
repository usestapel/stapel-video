"""In-process VideoProvider fake for tests — never touches the network.

Tokens are deterministic strings; egress ids are counters; ``parse_webhook``
treats the body as JSON and a caller-supplied ``valid`` flag stands in for a
signature check. Exercises the full seam without LiveKit installed.

``parse_webhook`` normalizes the same shape LiveKitProvider does (the four
egress keys plus room/participant/timestamps), reading unix seconds out of the
JSON body the way the real one reads them off the protobuf, so an ingest test
exercises the actual contract rather than a fixture the code was written
around. ``live`` stands in for the media server's roster.
"""
import json
from datetime import datetime, timezone

from stapel_video.providers.base import (
    METADATA_SCOPE_KEY,
    VideoProvider,
    VideoProviderError,
    split_identity,
)


def _epoch(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds > 0 else None


def _participant(raw):
    if not raw:
        return None
    identity = str(raw.get("identity") or "")
    if not identity:
        return None
    user_id, connection_id = split_identity(identity)
    return {
        "identity": identity,
        "user_id": user_id,
        "connection_id": connection_id,
        "name": str(raw.get("name") or ""),
        "joined_at": _epoch(raw.get("joined_at")),
        # The scope echo, read out of the same per-connection metadata blob
        # LiveKitProvider uses, so an ingest test exercises the real contract
        # rather than a shortcut key the fake invented.
        "scope_key": _scope_key(raw.get("metadata")),
    }


def _scope_key(metadata):
    if not metadata:
        return None
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(METADATA_SCOPE_KEY)
    return str(value) if value else None


class FakeProvider(VideoProvider):
    _egress_seq = 0
    #: Every ``rename_participant`` call, as (room_ref, user_id, name) — the
    #: class holds it so a test can read it without owning the instance
    #: ``get_video_provider()`` built.
    renames: list = []
    #: Every ``mint_join_token`` call, as (room_ref, user_id, name, avatar,
    #: client_session_id, scope_key). Same class-level trick as ``renames``.
    mints: list = []
    #: Every ``remove_participant`` call, as (room_ref, user_id).
    removals: list = []
    #: Every ``mint_call_token`` call, as (room_ref, user_id, name, avatar,
    #: client_session_id, scope_key, ttl_seconds).
    call_mints: list = []
    #: Every ``ensure_call_room`` call, as (room_ref, max_participants,
    #: empty_timeout_seconds).
    call_rooms: list = []
    #: What ``client_url`` answers.
    client_url_value: str = "wss://fake.example/rtc"
    #: room_ref -> metadata dict, for the metadata pair.
    metadata: dict = {}
    #: room_ref -> list of raw participant dicts the media server would
    #: report, or None for "that room does not exist". Drives
    #: ``list_participants`` and therefore the presence sweeper.
    live: dict = {}

    def create_room(self, join_code: str, *, scope_key: str = "") -> str:
        return f"fake-room::{join_code}"

    def mint_join_token(
        self,
        provider_room_ref,
        user_id,
        user_name,
        user_avatar: str = "",
        client_session_id=None,
        scope_key=None,
    ) -> str:
        FakeProvider.mints.append(
            (provider_room_ref, str(user_id), user_name, user_avatar,
             client_session_id, scope_key)
        )
        identity = (
            f"{user_id}_{client_session_id}" if client_session_id else f"{user_id}_rnd"
        )
        return f"faketoken::{provider_room_ref}::{identity}"

    def ensure_call_room(
        self,
        provider_room_ref,
        *,
        max_participants: int = 2,
        empty_timeout_seconds: int = 60,
        metadata=None,
    ) -> str:
        FakeProvider.call_rooms.append(
            (provider_room_ref, max_participants, empty_timeout_seconds)
        )
        return provider_room_ref

    def mint_call_token(
        self,
        provider_room_ref,
        user_id,
        user_name,
        user_avatar: str = "",
        client_session_id=None,
        scope_key=None,
        *,
        ttl_seconds=None,
    ) -> str:
        FakeProvider.call_mints.append(
            (provider_room_ref, str(user_id), user_name, user_avatar,
             client_session_id, scope_key, ttl_seconds)
        )
        identity = (
            f"{user_id}_{client_session_id}" if client_session_id else f"{user_id}_rnd"
        )
        return f"fakecalltoken::{provider_room_ref}::{identity}"

    def client_url(self) -> str:
        return FakeProvider.client_url_value

    def rename_participant(self, provider_room_ref, user_id, user_name) -> int:
        FakeProvider.renames.append((provider_room_ref, str(user_id), user_name))
        return 1

    def remove_participant(self, provider_room_ref, user_id) -> int:
        FakeProvider.removals.append((provider_room_ref, str(user_id)))
        return 1

    def get_room_metadata(self, provider_room_ref) -> dict:
        return dict(FakeProvider.metadata.get(provider_room_ref, {}))

    def update_room_metadata(self, provider_room_ref, metadata: dict) -> bool:
        FakeProvider.metadata[provider_room_ref] = dict(metadata or {})
        return True

    def probe_reachable(self) -> bool:
        return True

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
            "event_id": data.get("id"),
            "event_ts": _epoch(data.get("created_at")),
            "room": (
                {
                    "name": str((data.get("room") or {}).get("name") or ""),
                    "sid": str((data.get("room") or {}).get("sid") or ""),
                }
                if data.get("room")
                else None
            ),
            "participant": _participant(data.get("participant")),
            "egress_id": data.get("egress_id"),
            "status": data.get("status"),
            "storage_key": data.get("storage_key"),
        }

    def list_participants(self, provider_room_ref: str):
        raw = FakeProvider.live.get(provider_room_ref, None)
        if raw is None:
            return None
        return [p for p in (_participant(entry) for entry in raw) if p]
