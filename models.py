"""Models for stapel-video.

The generic video-room core: ``Room`` (a call others join by a short code)
and ``RoomParticipant`` (a user's admission state + role in a room).

House rules (docs/library-standard.md §3.8):
- cross-service references are UUID fields, not FKs (``Room.id`` is a UUID so
  the ``video.egress_ended`` emit id is a stable cross-service handle);
- the user model is only ``settings.AUTH_USER_MODEL``;
- **no FK to Organization/Workspace/Recording** — scoping is the opaque
  ``scope_key`` string; the recording *resource* is an app-layer concern
  reached via a comm emit. The video *provider* room is referenced by an
  opaque ``provider_room_ref`` string, never a provider-SDK object.
"""
import random
import string
import uuid

from django.conf import settings
from django.db import models


def generate_join_code() -> str:
    """A human-shareable join code in the ``abc-defg-hij`` shape (3-4-3
    lowercase letters). ~26**10 space — collision-checked at create time."""
    part1 = "".join(random.choices(string.ascii_lowercase, k=3))
    part2 = "".join(random.choices(string.ascii_lowercase, k=4))
    part3 = "".join(random.choices(string.ascii_lowercase, k=3))
    return f"{part1}-{part2}-{part3}"


class AccessLevel(models.TextChoices):
    """How strangers get into a room.

    Members:
        PUBLIC: anyone with the join code is admitted instantly.
        SCOPE_TRUSTED: members of the room's scope (resolved by the
            SCOPE_PROVIDER seam) auto-admit; everyone else waits in the lobby.
        RESTRICTED: everyone but the host waits in the lobby for admission.
    """

    PUBLIC = "public", "Public"
    SCOPE_TRUSTED = "scope_trusted", "Scope-trusted"
    RESTRICTED = "restricted", "Restricted"


class ParticipantStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    ADMITTED = "admitted", "Admitted"
    DENIED = "denied", "Denied"
    LEFT = "left", "Left"


class ParticipantRole(models.TextChoices):
    HOST = "host", "Host"
    GUEST = "guest", "Guest"


class Room(models.Model):
    """A video call. Joined by ``join_code``; access governed by
    ``access_level`` + ``admit_required``. The concrete media room on the
    provider is referenced by the opaque ``provider_room_ref`` (the provider's
    own room name/id), set by ``VideoProvider.create_room``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    join_code = models.CharField(
        max_length=16, unique=True, default=generate_join_code, db_index=True
    )
    # Opaque host-supplied scope (workspace_id / org_id / tenant / ""). The
    # library never interprets it; the SCOPE_PROVIDER seam resolves membership.
    scope_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    access_level = models.CharField(
        max_length=16, choices=AccessLevel.choices, default=AccessLevel.RESTRICTED
    )
    # Whether non-auto-admitted joiners wait for a host. A host may drop the
    # lobby mid-call (admit-all), flipping this off.
    admit_required = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_video_rooms",
    )
    # Opaque reference to the provider's room (LiveKit room name, etc.). Set at
    # create time by VideoProvider.create_room; the library never parses it.
    provider_room_ref = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["scope_key", "created_at"], name="video_room_scope_created"),
            models.Index(fields=["created_by"], name="video_room_creator"),
        ]

    def __str__(self):
        return f"{self.join_code} ({self.access_level})"


class RoomParticipant(models.Model):
    """A user's presence in a room: their admission ``status`` and ``role``.

    ``joined_at`` orders the lobby FIFO and anchors the participants listing
    (stapel-core AnchorPagination — limit/offset is forbidden shelf-wide).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(
        Room, on_delete=models.CASCADE, related_name="participants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_participations",
    )
    status = models.CharField(
        max_length=10,
        choices=ParticipantStatus.choices,
        default=ParticipantStatus.WAITING,
    )
    role = models.CharField(
        max_length=8, choices=ParticipantRole.choices, default=ParticipantRole.GUEST
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "user"], name="video_participant_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["room", "status"], name="video_participant_room_status"),
            models.Index(fields=["user"], name="video_participant_user"),
        ]

    def __str__(self):
        return f"{self.user_id} @ {self.room_id} ({self.status})"
