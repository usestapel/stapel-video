"""LiveKit implementation of the VideoProvider seam.

The LiveKit SDK (``livekit-api``) is an OPTIONAL extra (``[livekit]``): every
import of it is *lazy*, inside the method that uses it, so this module — and
the default ``VIDEO_PROVIDER`` dotted path pointing at it — resolves on a plain
install. Calling a method without the extra raises a clear ImportError telling
you to ``pip install 'stapel-video[livekit]'``.

Credentials are read lazily from the ``STAPEL_VIDEO`` conf namespace (never
freezes an env value at import time — library-standard §8.1 rule 1).

Recording egress lives here too (seam only in v0.1.0 — this library ships no
pipeline): the app tells LiveKit to write the room-composite file straight to
the recordings object store at a caller-supplied ``storage_key``, and the
webhook receiver verifies LiveKit's signed events. The host owns the storage
lifecycle via the ``video.egress_ended`` comm emit.
"""
from __future__ import annotations

from .base import VideoProvider, VideoProviderError


def _require_sdk():
    try:
        from livekit import api  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via extra-less install
        raise ImportError(
            "stapel_video.providers.livekit requires the optional 'livekit' "
            "extra, which is not installed. Install it with:\n"
            "    pip install 'stapel-video[livekit]'"
        ) from exc
    return api


def _require_requests():
    try:
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "stapel_video.providers.livekit egress requires the optional "
            "'livekit' extra (pulls in requests). Install it with:\n"
            "    pip install 'stapel-video[livekit]'"
        ) from exc
    return requests


class LiveKitProvider(VideoProvider):
    def _conf(self):
        from ..conf import video_settings

        return video_settings

    def create_room(self, join_code: str, *, scope_key: str = "") -> str:
        # LiveKit creates a room lazily on first join, so the room name IS the
        # provider ref — no network call needed at create time. The join_code
        # doubles as the LiveKit room name.
        return join_code

    def mint_join_token(
        self, provider_room_ref: str, user_id, user_name: str
    ) -> str:
        import uuid

        api = _require_sdk()
        conf = self._conf()
        token = api.AccessToken(
            api_key=conf.LIVEKIT_API_KEY,
            api_secret=conf.LIVEKIT_API_SECRET,
        )
        # Unique identity per connection to allow multi-device joins.
        identity = f"{user_id}_{uuid.uuid4().hex[:8]}"
        token = (
            token.with_identity(identity)
            .with_name(user_name)
            .with_ttl(_timedelta_seconds(conf.JOIN_TOKEN_TTL_SECONDS))
            .with_grants(api.VideoGrants(room_join=True, room=provider_room_ref))
        )
        return token.to_jwt()

    def rename_participant(
        self, provider_room_ref: str, user_id, user_name: str
    ) -> int:
        """Update the name on every live connection ``user_id`` holds here.

        Two calls, because LiveKit addresses a participant by the identity
        this class minted, not by ``user_id``: ``ListParticipants`` to find
        the connections whose identity is ``{user_id}_{suffix}``, then one
        ``UpdateParticipant`` each. Every LiveKit client in the room gets a
        ``ParticipantNameChanged`` event and re-renders, with no rejoin.

        Matching is on the ``{user_id}_`` PREFIX, deliberately: the suffix is
        per-connection, so one person on a laptop and a phone is two live
        identities and both must move. The separator is included in the
        prefix so one user id can never match another's — bare
        ``startswith(user_id)`` would be a correctness bug the day ids stop
        being fixed-width UUIDs.

        The participant's metadata is passed through untouched. LiveKit's
        UpdateParticipant overwrites metadata with whatever the request
        carries, so omitting it would silently erase the avatar (or whatever
        the host put there) as a side effect of a rename — a repair that
        breaks a neighbouring field is not a repair.
        """
        requests = _require_requests()
        prefix = f"{user_id}_"
        base = self._http_url()
        headers = self._room_admin_headers(provider_room_ref)
        try:
            resp = requests.post(
                f"{base}/twirp/livekit.RoomService/ListParticipants",
                json={"room": provider_room_ref},
                headers=headers,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise VideoProviderError(f"list participants transport error: {exc}") from exc
        if resp.status_code != 200:
            # LiveKit creates rooms lazily, so a room nobody is in does not
            # exist and answers twirp `not_found` (HTTP 404) with the message
            # "requested room does not exist". "Nobody to rename" is the
            # honest reading of that, not a failure. Keyed on the STATUS, not
            # the prose: the message carries no substring worth matching, and
            # the code is the part of the contract that holds still.
            if resp.status_code == 404:
                return 0
            raise VideoProviderError(
                f"list participants failed: {resp.status_code} {resp.text[:300]}"
            )
        participants = resp.json().get("participants") or []
        renamed = 0
        for participant in participants:
            identity = participant.get("identity") or ""
            if not identity.startswith(prefix):
                continue
            if (participant.get("name") or "") == user_name:
                continue  # already correct — at-least-once delivery, stay idempotent
            payload = {
                "room": provider_room_ref,
                "identity": identity,
                "name": user_name,
                "metadata": participant.get("metadata") or "",
            }
            try:
                update = requests.post(
                    f"{base}/twirp/livekit.RoomService/UpdateParticipant",
                    json=payload,
                    headers=headers,
                    timeout=10,
                )
            except requests.RequestException as exc:
                raise VideoProviderError(
                    f"update participant transport error: {exc}"
                ) from exc
            if update.status_code != 200:
                # The person hung up between the two calls ("participant does
                # not exist", twirp not_found). A race we lose harmlessly:
                # they carry the new name on their next join.
                if update.status_code == 404:
                    continue
                raise VideoProviderError(
                    f"update participant failed: {update.status_code} "
                    f"{update.text[:300]}"
                )
            renamed += 1
        return renamed

    # ── Recording egress ───────────────────────────────────────────────

    def _http_url(self) -> str:
        url = self._conf().LIVEKIT_URL or ""
        return url.replace("ws://", "http://").replace("wss://", "https://")

    def _room_admin_headers(self, provider_room_ref: str) -> dict:
        """Auth for the RoomService twirp API, scoped to ONE room.

        LiveKit's admin check is ``room_admin AND grant.room == <the room in
        the request>`` — a grant without the room name is refused, so the ref
        is a required argument rather than a convenience.
        """
        api = _require_sdk()
        conf = self._conf()
        token = api.AccessToken(
            api_key=conf.LIVEKIT_API_KEY,
            api_secret=conf.LIVEKIT_API_SECRET,
        ).with_grants(api.VideoGrants(room_admin=True, room=provider_room_ref))
        return {
            "Authorization": f"Bearer {token.to_jwt()}",
            "Content-Type": "application/json",
        }

    def _egress_headers(self) -> dict:
        api = _require_sdk()
        conf = self._conf()
        token = api.AccessToken(
            api_key=conf.LIVEKIT_API_KEY,
            api_secret=conf.LIVEKIT_API_SECRET,
        ).with_grants(api.VideoGrants(room_record=True))
        return {
            "Authorization": f"Bearer {token.to_jwt()}",
            "Content-Type": "application/json",
        }

    def start_room_egress(self, provider_room_ref: str, storage_key: str) -> str:
        requests = _require_requests()
        conf = self._conf()
        payload = {
            "room_name": provider_room_ref,
            "file_outputs": [
                {
                    "file_type": "MP4",
                    "filepath": storage_key,
                    # No timestamp templating: the file must land exactly at
                    # the caller's storage key (a recordings upload session).
                    "disable_manifest": True,
                    "s3": {
                        "access_key": conf.EGRESS_S3_ACCESS_KEY,
                        "secret": conf.EGRESS_S3_SECRET_KEY,
                        "endpoint": conf.EGRESS_S3_ENDPOINT,
                        "bucket": conf.EGRESS_S3_BUCKET,
                        "force_path_style": True,
                    },
                }
            ],
        }
        try:
            resp = requests.post(
                f"{self._http_url()}/twirp/livekit.Egress/StartRoomCompositeEgress",
                json=payload,
                headers=self._egress_headers(),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise VideoProviderError(f"egress transport error: {exc}") from exc
        if resp.status_code != 200:
            raise VideoProviderError(
                f"egress start failed: {resp.status_code} {resp.text[:300]}"
            )
        body = resp.json()
        egress_id = body.get("egress_id") or body.get("egressId")
        if not egress_id:
            raise VideoProviderError(f"egress start returned no id: {body}")
        return egress_id

    def stop_room_egress(self, egress_id: str) -> None:
        requests = _require_requests()
        try:
            resp = requests.post(
                f"{self._http_url()}/twirp/livekit.Egress/StopEgress",
                json={"egress_id": egress_id},
                headers=self._egress_headers(),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise VideoProviderError(f"egress stop transport error: {exc}") from exc
        # An already-completed/aborted egress returns an error we tolerate.
        if resp.status_code != 200 and "not found" not in resp.text.lower():
            raise VideoProviderError(
                f"egress stop failed: {resp.status_code} {resp.text[:300]}"
            )

    def parse_webhook(self, body: bytes, auth_header: str) -> dict:
        api = _require_sdk()
        conf = self._conf()
        receiver = api.WebhookReceiver(
            api.TokenVerifier(
                api_key=conf.LIVEKIT_API_KEY,
                api_secret=conf.LIVEKIT_API_SECRET,
            )
        )
        try:
            event = receiver.receive(body.decode("utf-8"), auth_header or "")
        except Exception as exc:  # bad signature / malformed body
            raise VideoProviderError(f"invalid webhook: {exc}") from exc
        info = getattr(event, "egress_info", None)
        egress_id = getattr(info, "egress_id", None) or None
        # The recordings host keyed the file on the egress filepath (storage_key).
        storage_key = None
        if info is not None:
            for file_result in getattr(info, "file_results", None) or []:
                storage_key = getattr(file_result, "filename", None) or storage_key
        return {
            "event": getattr(event, "event", None),
            "egress_id": egress_id,
            "status": _egress_status_name(info) if egress_id else None,
            "storage_key": storage_key,
        }


def _timedelta_seconds(seconds):
    from datetime import timedelta

    return timedelta(seconds=int(seconds))


def _egress_status_name(info) -> str | None:
    try:
        return (
            info.DESCRIPTOR.fields_by_name["status"]
            .enum_type.values_by_number[info.status]
            .name
        )
    except Exception:
        return None
