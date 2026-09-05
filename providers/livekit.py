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

from .base import (
    METADATA_SCOPE_KEY,
    VideoProvider,
    VideoProviderError,
    split_identity,
)


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
        self,
        provider_room_ref: str,
        user_id,
        user_name: str,
        user_avatar: str = "",
        client_session_id: str | None = None,
        scope_key: str | None = None,
    ) -> str:
        import json
        import uuid

        api = _require_sdk()
        conf = self._conf()
        token = api.AccessToken(
            api_key=conf.LIVEKIT_API_KEY,
            api_secret=conf.LIVEKIT_API_SECRET,
        )
        # Stable identity across a reload when the caller supplies a
        # client_session_id (a per-browser mark the frontend keeps). LiveKit
        # evicts the OLD connection the instant a new one connects under the
        # same identity, so a reload kills its own pre-reload ghost tile
        # immediately instead of leaving it to rot on LiveKit's disconnect
        # timeout. No session id (older clients, server-side mints for
        # somebody else) falls back to the random suffix, so two real devices
        # under one user id still get distinct identities.
        if client_session_id:
            identity = f"{user_id}_{client_session_id}"
        else:
            identity = f"{user_id}_{uuid.uuid4().hex[:8]}"
        token = (
            token.with_identity(identity)
            .with_name(user_name)
            .with_ttl(_timedelta_seconds(conf.JOIN_TOKEN_TTL_SECONDS))
            .with_grants(api.VideoGrants(room_join=True, room=provider_room_ref))
        )
        # ALWAYS set metadata, even with an empty avatar, so every client in
        # the room parses one consistent JSON shape instead of branching on
        # metadata being "sometimes absent". This is also the field
        # rename_participant echoes back untouched.
        #
        # scope_key rides here (0.7.0) rather than in a second channel: this
        # blob is the one thing LiveKit copies from the grant onto the
        # connection and hands back on every webhook and every
        # ListParticipants, which is exactly the echo the presence writer
        # needs. Written only when there is one, so an unscoped grant produces
        # no key at all and the reader sees None rather than "".
        metadata = {"avatar": user_avatar or ""}
        if scope_key:
            metadata[METADATA_SCOPE_KEY] = str(scope_key)
        token = token.with_metadata(json.dumps(metadata))
        return token.to_jwt()

    # ── 1:1 calls ──────────────────────────────────────────────────────

    def ensure_call_room(
        self,
        provider_room_ref: str,
        *,
        max_participants: int = 2,
        empty_timeout_seconds: int = 60,
        metadata: dict | None = None,
    ) -> str:
        """``RoomService/CreateRoom`` with the two-seat cap.

        The one place this class provisions a room instead of letting LiveKit
        materialize it lazily, because lazy creation cannot carry options and
        the cap is the option that matters: ``max_participants=2`` is what
        makes a leaked token unable to add a third tile.

        Creating a room that already exists is not an error to LiveKit (it
        answers with the existing room), so this is safe to call on a retry.
        """
        import json

        requests = _require_requests()
        payload = {
            "name": provider_room_ref,
            "max_participants": int(max_participants),
            "empty_timeout": int(empty_timeout_seconds),
        }
        if metadata:
            payload["metadata"] = json.dumps(metadata)
        try:
            resp = requests.post(
                f"{self._http_url()}/twirp/livekit.RoomService/CreateRoom",
                json=payload,
                headers=self._room_create_headers(provider_room_ref),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise VideoProviderError(f"create room transport error: {exc}") from exc
        if resp.status_code != 200:
            raise VideoProviderError(
                f"create room failed: {resp.status_code} {resp.text[:300]}"
            )
        return provider_room_ref

    def mint_call_token(
        self,
        provider_room_ref: str,
        user_id,
        user_name: str,
        user_avatar: str = "",
        client_session_id: str | None = None,
        scope_key: str | None = None,
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        """A call grant, with every permission stated rather than inherited.

        ``can_publish`` and ``can_subscribe`` are what a call IS.
        ``can_publish_data=False`` is a product decision made structural: this
        fleet's messaging is stapel-chat, a call hangs off a chat thread, and
        a LiveKit data channel would be a second message store — unpersisted,
        unmoderated, invisible to erasure — living inside the media session. A
        prose "we don't use data messages" is a convention; a denied grant is
        a fact the front cannot work around.

        No ``room_admin`` (that grant is for the server's own twirp calls and
        is never handed to a browser), no ``room_record``, no ``hidden``.
        """
        import json
        import uuid

        api = _require_sdk()
        conf = self._conf()
        if client_session_id:
            identity = f"{user_id}_{client_session_id}"
        else:
            identity = f"{user_id}_{uuid.uuid4().hex[:8]}"
        ttl = ttl_seconds if ttl_seconds else conf.CALL_TOKEN_TTL_SECONDS
        token = (
            api.AccessToken(
                api_key=conf.LIVEKIT_API_KEY,
                api_secret=conf.LIVEKIT_API_SECRET,
            )
            .with_identity(identity)
            .with_name(user_name)
            .with_ttl(_timedelta_seconds(ttl))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=provider_room_ref,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=False,
                )
            )
        )
        # Same metadata contract as mint_join_token — always written, so every
        # client parses one shape, and so the scope_key echo the presence
        # writer depends on is present on a call connection too.
        metadata = {"avatar": user_avatar or ""}
        if scope_key:
            metadata[METADATA_SCOPE_KEY] = str(scope_key)
        return token.with_metadata(json.dumps(metadata)).to_jwt()

    def client_url(self) -> str:
        """``LIVEKIT_CLIENT_URL``, falling back to ``LIVEKIT_URL``.

        The fallback is right for a deployment where the browser and the
        server reach LiveKit at the same address, and silently wrong for one
        where they do not — which is every host-networked deployment. That is
        why it is a fallback with a boot warning (``stapel_video.W007``) and
        not a default nobody is told about.
        """
        conf = self._conf()
        return conf.LIVEKIT_CLIENT_URL or conf.LIVEKIT_URL or ""

    def _room_create_headers(self, provider_room_ref: str) -> dict:
        """Auth for ``CreateRoom``, which is gated on ``room_create``.

        Separate from :meth:`_room_admin_headers` because the grants are
        different: ``room_admin`` addresses an existing room, ``room_create``
        makes one. Asking for both everywhere would hand every twirp call the
        power to create rooms it has no business creating.
        """
        api = _require_sdk()
        conf = self._conf()
        token = api.AccessToken(
            api_key=conf.LIVEKIT_API_KEY,
            api_secret=conf.LIVEKIT_API_SECRET,
        ).with_grants(
            api.VideoGrants(room_create=True, room=provider_room_ref)
        )
        return {
            "Authorization": f"Bearer {token.to_jwt()}",
            "Content-Type": "application/json",
        }

    def rename_participant(
        self, provider_room_ref: str, user_id, user_name: str
    ) -> int:
        """Update the name on every live connection ``user_id`` holds here.

        Two calls, because LiveKit addresses a participant by the identity
        this class minted, not by ``user_id``: ``ListParticipants`` to find
        the connections whose identity is ``{user_id}_{suffix}``, then one
        ``UpdateParticipant`` each. Every LiveKit client in the room gets a
        ``ParticipantNameChanged`` event and re-renders, with no rejoin.

        Matching is :func:`_mine` — the ``{user_id}_`` PREFIX, separator
        included — so both identity forms this class mints (the deterministic
        ``{user_id}_{client_session_id}`` and the random-suffix fallback) are
        found, one person on a laptop and a phone moves as two connections,
        and one user id can never match another's. Bare
        ``startswith(user_id)`` would be a correctness bug the day ids stop
        being fixed-width UUIDs.

        The participant's metadata is passed through untouched. LiveKit's
        UpdateParticipant overwrites metadata with whatever the request
        carries, so omitting it would silently erase the avatar (or whatever
        the host put there) as a side effect of a rename — a repair that
        breaks a neighbouring field is not a repair.
        """
        requests = _require_requests()
        base = self._http_url()
        headers = self._room_admin_headers(provider_room_ref)
        participants = self._list_participants(provider_room_ref)
        if participants is None:
            return 0
        renamed = 0
        for participant in _mine(participants, user_id):
            identity = participant.get("identity") or ""
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

    def remove_participant(self, provider_room_ref: str, user_id) -> int:
        """Disconnect every live connection ``user_id`` holds in this room.

        The same two-call, per-identity shape as
        :meth:`rename_participant` — ``ListParticipants`` then one
        ``RemoveParticipant`` per matched identity — for the same reason:
        one person is N connections, and removing one of them is not a kick.
        Returns how many were disconnected.
        """
        requests = _require_requests()
        base = self._http_url()
        headers = self._room_admin_headers(provider_room_ref)
        participants = self._list_participants(provider_room_ref)
        if participants is None:
            return 0
        removed = 0
        for participant in _mine(participants, user_id):
            try:
                resp = requests.post(
                    f"{base}/twirp/livekit.RoomService/RemoveParticipant",
                    json={
                        "room": provider_room_ref,
                        "identity": participant.get("identity") or "",
                    },
                    headers=headers,
                    timeout=10,
                )
            except requests.RequestException as exc:
                raise VideoProviderError(
                    f"remove participant transport error: {exc}"
                ) from exc
            if resp.status_code != 200:
                # They hung up between the two calls — the goal (that
                # connection is gone) is met either way.
                if resp.status_code == 404:
                    continue
                raise VideoProviderError(
                    f"remove participant failed: {resp.status_code} "
                    f"{resp.text[:300]}"
                )
            removed += 1
        return removed

    def list_participants(self, provider_room_ref: str) -> list | None:
        """The contract half of :meth:`_list_participants` — normalized rows.

        Same twirp call, but the rows come back in the shape the rest of the
        fleet speaks (``identity`` + its decomposition + ``joined_at`` as an
        aware datetime) instead of LiveKit's wire dicts, which
        :meth:`rename_participant` and :meth:`remove_participant` still want
        raw because they hand ``metadata`` straight back.
        """
        raw = self._list_participants(provider_room_ref)
        if raw is None:
            return None
        return [_participant_dict(p) for p in raw]

    def _list_participants(self, provider_room_ref: str):
        """``ListParticipants`` for a room, or None when the room is not live.

        LiveKit creates rooms lazily, so a room nobody is in does not exist
        and answers twirp ``not_found`` (HTTP 404). "Nobody in there" is the
        honest reading of that, not a failure — and it is the answer both
        callers want. Keyed on the STATUS, not the prose: the message carries
        no substring worth matching, and the code is the part of the contract
        that holds still.
        """
        requests = _require_requests()
        try:
            resp = requests.post(
                f"{self._http_url()}/twirp/livekit.RoomService/ListParticipants",
                json={"room": provider_room_ref},
                headers=self._room_admin_headers(provider_room_ref),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise VideoProviderError(
                f"list participants transport error: {exc}"
            ) from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise VideoProviderError(
                f"list participants failed: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json().get("participants") or []

    # ── Room metadata ──────────────────────────────────────────────────

    def get_room_metadata(self, provider_room_ref: str) -> dict:
        requests = _require_requests()
        try:
            resp = requests.post(
                f"{self._http_url()}/twirp/livekit.RoomService/ListRooms",
                json={"names": [provider_room_ref]},
                headers=self._room_admin_headers(provider_room_ref),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise VideoProviderError(f"list rooms transport error: {exc}") from exc
        if resp.status_code != 200:
            raise VideoProviderError(
                f"list rooms failed: {resp.status_code} {resp.text[:300]}"
            )
        try:
            rooms = resp.json().get("rooms") or []
        except ValueError as exc:
            raise VideoProviderError(f"list rooms returned non-JSON: {exc}") from exc
        if not rooms:
            # A room nobody is in has not been materialized — no metadata,
            # which is what an empty dict says.
            return {}
        import json

        raw = rooms[0].get("metadata") or ""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise VideoProviderError(f"room metadata is not JSON: {exc}") from exc
        return parsed if isinstance(parsed, dict) else {}

    def update_room_metadata(self, provider_room_ref: str, metadata: dict) -> bool:
        import json

        requests = _require_requests()
        try:
            resp = requests.post(
                f"{self._http_url()}/twirp/livekit.RoomService/UpdateRoomMetadata",
                json={
                    "room": provider_room_ref,
                    "metadata": json.dumps(metadata or {}),
                },
                headers=self._room_admin_headers(provider_room_ref),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise VideoProviderError(
                f"update room metadata transport error: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise VideoProviderError(
                f"update room metadata failed: {resp.status_code} "
                f"{resp.text[:300]}"
            )
        return True

    # ── Health ─────────────────────────────────────────────────────────

    def probe_reachable(self) -> bool:
        """``ListRooms`` with an empty filter — the cheapest call that
        exercises the exact path the real ones use (credentials, headers,
        network) without touching any room's state. Never raises."""
        try:
            requests = _require_requests()
            resp = requests.post(
                f"{self._http_url()}/twirp/livekit.RoomService/ListRooms",
                json={"names": []},
                headers=self._room_admin_headers("__stapel_video_health_probe__"),
                timeout=3,
            )
            return resp.status_code == 200
        except Exception:
            # A health probe that can itself fail the health endpoint is not a
            # probe. Unreachable, misconfigured and SDK-less all read "no".
            return False

    # ── Recording egress ───────────────────────────────────────────────

    def _http_url(self) -> str:
        url = self._conf().LIVEKIT_URL or ""
        return url.replace("ws://", "http://").replace("wss://", "https://")

    def _room_admin_headers(self, provider_room_ref: str) -> dict:
        """Auth for the RoomService twirp API, scoped to ONE room.

        LiveKit's admin check is ``room_admin AND grant.room == <the room in
        the request>`` — a grant without the room name is refused, so the ref
        is a required argument rather than a convenience. ``room_list`` rides
        along because ``ListRooms`` (the metadata read and the health probe)
        is gated on that grant, not on ``room_admin``.
        """
        api = _require_sdk()
        conf = self._conf()
        token = api.AccessToken(
            api_key=conf.LIVEKIT_API_KEY,
            api_secret=conf.LIVEKIT_API_SECRET,
        ).with_grants(
            api.VideoGrants(
                room_admin=True, room_list=True, room=provider_room_ref
            )
        )
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
        room = getattr(event, "room", None)
        participant = getattr(event, "participant", None)
        return {
            "event": getattr(event, "event", None),
            # Additive since 0.6.0 — see VideoProvider.parse_webhook. The four
            # egress keys below are byte-identical to what 0.5.x returned.
            "event_id": getattr(event, "id", None) or None,
            "event_ts": _epoch_seconds(getattr(event, "created_at", None)),
            "room": _room_dict(room),
            "participant": _participant_dict(participant),
            "egress_id": egress_id,
            "status": _egress_status_name(info) if egress_id else None,
            "storage_key": storage_key,
        }


def _mine(participants, user_id) -> list:
    """The listed participants that are live connections of ``user_id``.

    One matcher for every per-connection operation (rename, remove), because
    they must agree: a rename that finds a connection a kick does not is a
    defect waiting for the day the two are read side by side. The separator is
    part of the prefix so ``user 1`` never matches ``user 12``; the bare
    equality arm covers identities minted before the ``{user_id}_{suffix}``
    convention existed.
    """
    user_id = str(user_id)
    prefix = f"{user_id}_"
    return [
        p
        for p in participants
        if (identity := (p.get("identity") or ""))
        and (identity == user_id or identity.startswith(prefix))
    ]


def _field(source, *names):
    """One field off either a protobuf message or a twirp JSON dict.

    The two readings of the same LiveKit type arrive by different doors: the
    webhook receiver hands back protobuf objects (attributes, snake_case),
    while the RoomService twirp endpoints answer JSON (keys, camelCase by the
    protobuf JSON mapping). One accessor keeps ``_room_dict`` /
    ``_participant_dict`` single so the webhook and the poller can never
    disagree about what a participant is.
    """
    if source is None:
        return None
    for name in names:
        if isinstance(source, dict):
            value = source.get(name)
        else:
            value = getattr(source, name, None)
        if value not in (None, ""):
            return value
    return None


def _epoch_seconds(value):
    """A LiveKit unix-seconds timestamp as an aware UTC datetime, or None.

    LiveKit stamps events and joins on ITS clock, in whole seconds, and a
    zero is protobuf's "unset" rather than 1970 — a span opened at the epoch
    would be a 56-year presence record, so it reads as no timestamp at all.
    """
    from datetime import datetime, timezone

    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _room_dict(room) -> dict | None:
    name = _field(room, "name")
    if not name:
        return None
    return {"name": str(name), "sid": str(_field(room, "sid") or "")}


def _participant_dict(participant) -> dict | None:
    identity = _field(participant, "identity")
    if not identity:
        return None
    identity = str(identity)
    user_id, connection_id = split_identity(identity)
    return {
        "identity": identity,
        "user_id": user_id,
        "connection_id": connection_id,
        "name": str(_field(participant, "name") or ""),
        "joined_at": _epoch_seconds(_field(participant, "joined_at", "joinedAt")),
        "scope_key": _scope_key(participant),
    }


def _scope_key(participant) -> str | None:
    """The grant's ``scope_key``, read back out of the connection metadata.

    LiveKit copies the token's metadata onto the participant and repeats it on
    every webhook and every ``ListParticipants``, which is why
    :meth:`LiveKitProvider.mint_join_token` puts it there — the echo IS the
    transport. Unparseable or absent metadata is ``None``, never a raised
    exception: this runs inside webhook ingest, and a host that writes its own
    non-JSON metadata must lose the scope on that connection, not the event.
    """
    import json

    raw = _field(participant, "metadata")
    if not raw:
        return None
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get(METADATA_SCOPE_KEY)
    return str(value) if value else None


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
