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

    # ── Recording egress ───────────────────────────────────────────────

    def _http_url(self) -> str:
        url = self._conf().LIVEKIT_URL or ""
        return url.replace("ws://", "http://").replace("wss://", "https://")

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
