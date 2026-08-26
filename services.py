"""Service layer for stapel-video — the room lifecycle + admission logic.

Thin, testable functions the views call. The video backend is reached only
through the ``VideoProvider`` seam; the scope/membership decision only through
the ``SCOPE_PROVIDER`` seam. Recording is a *seam*: ``start_egress`` /
``stop_egress`` proxy the provider and the webhook path emits
``video.egress_ended`` — this module ships no recording pipeline and imports no
stapel-recordings model (integration is by comm event only).
"""
from __future__ import annotations

import uuid as _uuid

from django.db import IntegrityError, transaction

from .models import (
    AccessLevel,
    ParticipantRole,
    ParticipantStatus,
    Room,
    RoomParticipant,
    generate_join_code,
)
from .providers import get_video_provider
from .realtime import (
    SIGNAL_ADMITTED,
    SIGNAL_DENIED,
    SIGNAL_WAITING,
    notify_lobby,
)
from .scope import get_scope_provider


def _display_name(user) -> str:
    getter = getattr(user, "get_full_name", None)
    name = getter() if callable(getter) else ""
    return name or getattr(user, "email", "") or str(getattr(user, "pk", user))


def _avatar(user) -> str:
    """The picture that travels with the name into the call, if the host's
    user model has one.

    Duck-typed on purpose: this library does not own the user model and will
    not grow an opinion about where a picture lives. It reads the two shapes
    the fleet actually uses (``avatar_url``, and an ``avatar`` file/URL field)
    and otherwise sends "", which the provider still writes as an explicit
    empty JSON field so a client never has to branch on absence."""
    for attr in ("avatar_url", "avatar"):
        try:
            value = getattr(user, attr, None)
            if callable(value):
                value = value()
            if not value:
                continue
            url = getattr(value, "url", None)
            return str(url or value)
        except Exception:
            continue
    return ""


# ── Create ─────────────────────────────────────────────────────────────────


def create_room(
    user,
    *,
    scope_key: str = "",
    access_level: str | None = None,
    admit_required: bool | None = None,
) -> Room:
    """Create a room, provision its provider room, and auto-admit the creator
    as host. ``access_level`` / ``admit_required`` fall back to the configured
    axis defaults when None."""
    from .conf import video_settings

    if access_level is None:
        access_level = video_settings.DEFAULT_ACCESS_LEVEL
    if admit_required is None:
        admit_required = video_settings.DEFAULT_ADMIT_REQUIRED

    provider = get_video_provider()
    with transaction.atomic():
        room = _create_room_row(
            user, scope_key=scope_key, access_level=access_level,
            admit_required=admit_required,
        )
        room.provider_room_ref = provider.create_room(
            room.join_code, scope_key=scope_key
        )
        room.save(update_fields=["provider_room_ref", "updated_at"])
        RoomParticipant.objects.create(
            room=room,
            user=user,
            status=ParticipantStatus.ADMITTED,
            role=ParticipantRole.HOST,
        )
    return room


def _create_room_row(user, *, scope_key, access_level, admit_required) -> Room:
    """Insert the Room, retrying join_code collisions (unique constraint)."""
    for _ in range(5):
        try:
            with transaction.atomic():
                return Room.objects.create(
                    join_code=generate_join_code(),
                    scope_key=scope_key,
                    access_level=access_level,
                    admit_required=admit_required,
                    created_by=user,
                )
        except IntegrityError:
            continue
    raise IntegrityError("could not allocate a unique join_code after 5 tries")


# ── Lookup ─────────────────────────────────────────────────────────────────


def get_room(join_code: str) -> Room | None:
    return Room.objects.filter(join_code=join_code).first()


def participants_queryset(room: Room):
    """Base queryset for the (anchor-paginated) participants listing."""
    return room.participants.select_related("user").all()


# ── Join / admission ───────────────────────────────────────────────────────


def _mint_token(room: Room, user, client_session_id: str | None = None) -> str:
    """Mint the media token for this room, carrying the room's own scope.

    ``scope_key`` rides the grant because the grant is the only moment both
    facts are in the same process: the room knows its partition, and the
    provider is what will still be holding it when a webhook reports the stay
    an hour later. ``Room.scope_key`` is `""` for an unpartitioned host, which
    the presence writer stores as NULL rather than as a tenant named "".
    """
    return get_video_provider().mint_join_token(
        room.provider_room_ref or room.join_code,
        user.pk,
        _display_name(user),
        _avatar(user),
        client_session_id,
        scope_key=room.scope_key or None,
    )


def _should_auto_admit(room: Room, user, request) -> bool:
    if room.created_by_id == user.pk:
        return True
    if room.access_level == AccessLevel.PUBLIC:
        return True
    if room.access_level == AccessLevel.SCOPE_TRUSTED:
        if get_scope_provider().is_member(request, room.scope_key):
            return True
    # A host who dropped the lobby mid-call (admit_required off) lets anyone in.
    if not room.admit_required:
        return True
    return False


def join_room(
    user, room: Room, request, client_session_id: str | None = None
) -> dict:
    """Resolve a join against the room's access level.

    Returns ``{"status": ..., "room": room, "participant": p, "token": str?}``
    where status is ``admitted`` (token present), ``waiting`` or ``denied``.

    ``client_session_id`` is forwarded untouched to the provider — it is the
    caller's own browser mark, and a token minted without it re-ghosts on the
    next reload (see ``VideoProvider.mint_join_token``).
    """
    existing = RoomParticipant.objects.filter(room=room, user=user).first()
    if existing is not None:
        return _rejoin(room, existing, user, request, client_session_id)

    # The decision comes BEFORE the row. `consumers.py` admits anyone holding
    # a RoomParticipant row, so a row written ahead of the verdict IS the
    # verdict — it handed the lobby socket to a joiner the room was about to
    # refuse, and to one whose admission lookup had not answered yet. A
    # refusal that raises (503) now leaves nothing behind at all.
    admit = _should_auto_admit(room, user, request)
    participant, created = RoomParticipant.objects.get_or_create(
        room=room,
        user=user,
        defaults={
            "status": (
                ParticipantStatus.ADMITTED if admit else ParticipantStatus.WAITING
            ),
            "role": (
                ParticipantRole.HOST
                if room.created_by_id == user.pk
                else ParticipantRole.GUEST
            ),
        },
    )
    if not created:
        # Raced with a concurrent join; the row that won decides.
        return _rejoin(room, participant, user, request, client_session_id)
    if admit:
        return _admitted(room, participant, user, client_session_id)
    return _wait(room, participant, user)


def _rejoin(
    room: Room,
    participant: RoomParticipant,
    user,
    request,
    client_session_id: str | None = None,
) -> dict:
    """A join by someone this room already holds a row for."""
    # DENIED is sticky for this room: honour the host's rejection.
    if participant.status == ParticipantStatus.DENIED:
        return {"status": "denied", "room": room, "participant": participant}
    # A previously-admitted user who left is auto-readmitted.
    if participant.status == ParticipantStatus.LEFT:
        participant.status = ParticipantStatus.ADMITTED
        participant.left_at = None
        participant.save(update_fields=["status", "left_at"])
        return _admitted(room, participant, user, client_session_id)
    if participant.status == ParticipantStatus.ADMITTED:
        return _admitted(room, participant, user, client_session_id)
    # Still waiting — and the answer may have changed since (a host dropped
    # the lobby, the joiner joined the scope), so ask again rather than
    # sentencing them to the row they got the first time.
    if _should_auto_admit(room, user, request):
        participant.status = ParticipantStatus.ADMITTED
        participant.save(update_fields=["status"])
        return _admitted(room, participant, user, client_session_id)
    return _wait(room, participant, user)


def _wait(room: Room, participant: RoomParticipant, user) -> dict:
    """Park in the lobby and let the host clients see the arrival."""
    notify_lobby(
        room.join_code,
        SIGNAL_WAITING,
        {
            "participant_id": str(participant.id),
            "user_id": str(user.pk),
            "user_name": _display_name(user),
        },
    )
    return {"status": "waiting", "room": room, "participant": participant}


def _admitted(
    room: Room,
    participant: RoomParticipant,
    user,
    client_session_id: str | None = None,
) -> dict:
    return {
        "status": "admitted",
        "room": room,
        "participant": participant,
        "token": _mint_token(room, user, client_session_id),
    }


def admit_participant(room: Room, participant_id) -> RoomParticipant | None:
    """Admit a waiting participant (host-only — checked in the view). Mints a
    token and pushes ``lobby.admitted`` to the group. Returns the participant,
    or None if no waiting participant matches."""
    participant = _waiting(room, participant_id)
    if participant is None:
        return None
    participant.status = ParticipantStatus.ADMITTED
    participant.save(update_fields=["status"])
    token = _mint_token(room, participant.user)
    notify_lobby(
        room.join_code,
        SIGNAL_ADMITTED,
        {
            "participant_id": str(participant.id),
            "user_id": str(participant.user_id),
            # Reaches the admitted guest's own socket only — the consumer
            # strips it for every other member of the room.
            "token": token,
        },
    )
    participant.admit_token = token  # transient, for the view response
    return participant


def deny_participant(room: Room, participant_id) -> RoomParticipant | None:
    """Deny a waiting participant (host-only). Pushes ``lobby.denied``."""
    participant = _waiting(room, participant_id)
    if participant is None:
        return None
    participant.status = ParticipantStatus.DENIED
    participant.save(update_fields=["status"])
    notify_lobby(
        room.join_code,
        SIGNAL_DENIED,
        {
            "participant_id": str(participant.id),
            "user_id": str(participant.user_id),
        },
    )
    return participant


def _waiting(room: Room, participant_id) -> RoomParticipant | None:
    try:
        pid = participant_id if isinstance(participant_id, _uuid.UUID) else _uuid.UUID(
            str(participant_id)
        )
    except (ValueError, TypeError, AttributeError):
        return None
    return (
        RoomParticipant.objects.select_related("user")
        .filter(room=room, id=pid, status=ParticipantStatus.WAITING)
        .first()
    )


# ── Recording egress (seam) ─────────────────────────────────────────────────


def start_egress(room: Room, storage_key: str) -> str:
    """Proxy the provider's recording start. Returns the provider egress id.
    The host owns the storage_key (e.g. a stapel-recordings upload session)."""
    return get_video_provider().start_room_egress(
        room.provider_room_ref or room.join_code, storage_key
    )


def stop_egress(egress_id: str) -> None:
    get_video_provider().stop_room_egress(egress_id)


def handle_webhook(body: bytes, auth_header: str) -> dict:
    """Verify + decode a provider webhook and run the handler for its event type.

    Returns the normalized provider dict. Raises VideoProviderError on a bad
    signature — the view maps that to a 400.

    Dispatch is a merge registry (``stapel_video.webhooks``), not the
    ``if egress_ended`` this used to be. That branch was the reason
    ``participant_joined`` / ``participant_left`` / ``room_finished`` arrived
    at the URL, passed the signature check and were dropped: the only way to
    react to a second event was to fork the ingress or terminate the webhook
    in product code and re-implement verification there.

    An event nothing handles is a 200 with no work — most of what a media
    server sends (track_published, room_started, …) is not addressed to us,
    and answering 4xx would make the provider retry a delivery that was
    perfectly correct.
    """
    from .webhooks import get_webhook_handler

    parsed = get_video_provider().parse_webhook(body, auth_header)
    handler = get_webhook_handler(parsed.get("event") or "")
    if handler is not None:
        handler(parsed)
    return parsed
