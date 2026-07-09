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
from .realtime import notify_lobby
from .scope import get_scope_provider


def _display_name(user) -> str:
    getter = getattr(user, "get_full_name", None)
    name = getter() if callable(getter) else ""
    return name or getattr(user, "email", "") or str(getattr(user, "pk", user))


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


def _mint_token(room: Room, user) -> str:
    return get_video_provider().mint_join_token(
        room.provider_room_ref or room.join_code, user.pk, _display_name(user)
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


def join_room(user, room: Room, request) -> dict:
    """Resolve a join against the room's access level.

    Returns ``{"status": ..., "room": room, "participant": p, "token": str?}``
    where status is ``admitted`` (token present), ``waiting`` or ``denied``.
    """
    participant, created = RoomParticipant.objects.get_or_create(
        room=room,
        user=user,
        defaults={
            "status": ParticipantStatus.WAITING,
            "role": (
                ParticipantRole.HOST
                if room.created_by_id == user.pk
                else ParticipantRole.GUEST
            ),
        },
    )

    if not created:
        # DENIED is sticky for this room: honour the host's rejection.
        if participant.status == ParticipantStatus.DENIED:
            return {"status": "denied", "room": room, "participant": participant}
        # A previously-admitted user who left is auto-readmitted.
        if participant.status == ParticipantStatus.LEFT:
            participant.status = ParticipantStatus.ADMITTED
            participant.left_at = None
            participant.save(update_fields=["status", "left_at"])
            return _admitted(room, participant, user)
        if participant.status == ParticipantStatus.ADMITTED:
            return _admitted(room, participant, user)

    if _should_auto_admit(room, user, request):
        participant.status = ParticipantStatus.ADMITTED
        participant.save(update_fields=["status"])
        return _admitted(room, participant, user)

    # Wait for a host — notify the lobby (host clients see the new arrival).
    notify_lobby(
        room.join_code,
        {
            "type": "lobby.waiting",
            "participant_id": str(participant.id),
            "user_id": str(user.pk),
            "user_name": _display_name(user),
        },
    )
    return {"status": "waiting", "room": room, "participant": participant}


def _admitted(room: Room, participant: RoomParticipant, user) -> dict:
    return {
        "status": "admitted",
        "room": room,
        "participant": participant,
        "token": _mint_token(room, user),
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
        {
            "type": "lobby.admitted",
            "participant_id": str(participant.id),
            "user_id": str(participant.user_id),
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
        {
            "type": "lobby.denied",
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
    """Verify + decode a provider webhook and, on an egress-ended event, emit
    ``video.egress_ended`` so stapel-recordings (or any subscriber) can finalize
    the upload. Returns the normalized provider dict. Raises VideoProviderError
    on a bad signature — the view maps that to a 400."""
    from stapel_core.comm import emit

    parsed = get_video_provider().parse_webhook(body, auth_header)
    if _is_egress_ended(parsed):
        with transaction.atomic():
            emit(
                "video.egress_ended",
                {
                    "egress_id": parsed.get("egress_id"),
                    "status": parsed.get("status"),
                    "storage_key": parsed.get("storage_key"),
                },
                key=str(parsed.get("egress_id") or ""),
            )
    return parsed


def _is_egress_ended(parsed: dict) -> bool:
    """A recording finished. LiveKit emits a dedicated ``egress_ended`` event;
    some deployments only send ``egress_updated`` carrying a terminal status."""
    event = (parsed.get("event") or "").lower()
    status = (parsed.get("status") or "").upper()
    if event == "egress_ended":
        return True
    return event == "egress_updated" and status in (
        "EGRESS_COMPLETE",
        "EGRESS_ABORTED",
        "EGRESS_FAILED",
    )
