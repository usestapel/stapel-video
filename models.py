"""Models for stapel-video.

The generic video-room core: ``Room`` (a call others join by a short code)
and ``RoomParticipant`` (a user's admission state + role in a room), plus
``ParticipantSpan`` — the presence meter (who was connected where, from when
to when).

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


class SpanCloseReason(models.TextChoices):
    """Why a presence span stopped — the provenance of its ``left_at``.

    Members:
        EXPLICIT: the person (or the host) said so — a leave button, a kick.
        WEBHOOK: the media server said so — a ``participant_left`` event,
            which is the only witness that survives a client crash.
        SWEEPER: nobody said so; the reconciler noticed the connection was
            gone and closed the span at the last moment it was confirmed
            present. An estimate, bounded by the sweep interval, and marked
            as one so a reader can tell it from an observed departure.
    """

    EXPLICIT = "explicit", "Explicit leave"
    WEBHOOK = "webhook", "Provider webhook"
    SWEEPER = "sweeper", "Reconciled by the sweeper"


class ParticipantSpan(models.Model):
    """One connection's stay in one room: ``[joined_at, left_at)``.

    THE unit of presence metering, and deliberately not ``RoomParticipant``.
    That row is admission STATE, unique per (room, user) forever: its
    ``joined_at`` is the first knock of a lifetime, its ``left_at`` is reset
    to NULL when the person comes back, and the interval that just ended is
    physically destroyed by the return. State and history are different
    tables, and money is computed from the history one.

    Append-only by contract: a span is written once when the connection
    opens and closed once when it ends, and **a closed span is never touched
    again**. A grace-window rejoin creates a NEW span rather than reopening
    the old one, so the product's UX policy about what counts as "the same
    session" can change freely without restating a billing period. The only
    field that moves on an open span is ``last_seen_at`` (the sweeper's
    watermark), and it stops moving the moment the span closes.

    Nothing here is a ForeignKey, on purpose (library-standard §3.8 and the
    stapel-agent ledger precedent):

    - ``room_key`` is an opaque string the host chooses — the provider room
      ref, this module's join code, its own room id. The library never parses
      it and never joins on it, so a host that runs its own Room model meters
      through the same table.
    - ``user_id`` is a CharField, not a FK to AUTH_USER_MODEL. A FK would
      make ``gdpr.VideoGDPRProvider.delete`` cascade the meter away with the
      account, and the reporting question is explicitly "who was in a call
      during the period", answered the same whether or not that person still
      has an account. Erasure pseudonymizes the column instead
      (``presence.pseudonymize_user``): the counters survive, the person
      does not.
    - ``connection_id`` is the ``client_session_id`` half of the provider
      identity ``{user_id}_{client_session_id}``. A laptop and a phone are
      two connections of one user, which is why time is unioned per user
      (two devices are not two billable people) while joins and leaves are
      recorded per connection.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room_key = models.CharField(max_length=255, db_index=True)
    user_id = models.CharField(max_length=64, db_index=True)
    connection_id = models.CharField(max_length=128, db_index=True)
    #: The provider's server timestamp for the join, never our receipt time.
    joined_at = models.DateTimeField()
    #: NULL means "still connected as far as this instance knows" — the
    #: sweeper is what makes that claim expire instead of standing forever.
    left_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(
        max_length=16, choices=SpanCloseReason.choices, blank=True, default=""
    )
    #: The last moment this connection was CONFIRMED present (by the sweeper
    #: polling the media server). A span closed by the sweeper closes here,
    #: not at "now": a lost ``participant_left`` must cost at most one sweep
    #: interval of over-count, not the hours until somebody noticed.
    last_seen_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at", "id"]
        constraints = [
            # The idempotency key of the whole ingest path. Webhook delivery
            # is at-least-once and unordered, so the same connection's join
            # can arrive twice, and a `participant_left` can arrive before
            # the `participant_joined` it belongs to. Both paths key the span
            # on the provider's own joined_at, so a redelivery or an
            # out-of-order pair collides here and is a no-op instead of a
            # second, double-counted stay.
            models.UniqueConstraint(
                fields=["connection_id", "joined_at"], name="video_span_uniq"
            ),
        ]
        indexes = [
            # The meter's own queries: one person's period, one room's period.
            models.Index(fields=["user_id", "joined_at"], name="video_span_user_joined"),
            models.Index(fields=["room_key", "joined_at"], name="video_span_room_joined"),
            # The sweeper's query: everything still open, cheaply.
            models.Index(fields=["left_at"], name="video_span_open"),
        ]

    def __str__(self):
        end = self.left_at.isoformat() if self.left_at else "open"
        return f"{self.user_id}@{self.room_key} {self.joined_at.isoformat()}..{end}"

    @property
    def is_open(self) -> bool:
        return self.left_at is None
