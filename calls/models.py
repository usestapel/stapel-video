"""The call record, and the one state machine that owns it.

A :class:`Call` is two named people, one media room and one clock. Everything
this package does is a transition on it, and every transition is a conditional
UPDATE filtered on the state it is leaving — the shape
``stapel_video.presence.close_span`` already uses, for the same reason. A call
has several independent witnesses of its end (the person who pressed the
button, the media server's webhook, the reconciler), they arrive out of order
and more than once, and the first one to land must win outright. A ``save()``
would let the last one restate the duration.

**Why there is no ``Room`` row.** A :class:`~stapel_video.models.Room` is
entered with a ``join_code``: a shareable secret that admits its holder,
policed afterwards by a lobby. A call has no such object. Its two parties are
decided before anything connects, there is no third seat to police, and a
code that circulates is precisely the thing a private conversation must not
have. Sharing the tables would also mean two state machines answering "is
this person in this call" — ``Call.state`` and ``RoomParticipant.status`` —
which is one more than can ever be right.

**Duration is derived, never stored.** ``ended_at - answered_at``, computed on
read. Two witnesses must not be able to persist two different answers to how
long a call was; the only way to guarantee that is to have nowhere to put the
second one.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class CallState(models.TextChoices):
    """Where a call is.

    Members:
        RINGING: the callee has been told and has not answered. The only
            state with a deadline attached (``CALL_RING_TIMEOUT_SECONDS``).
        ACCEPTED: both parties hold a token; media is (or is about to be) up.
        DECLINED: the callee said no.
        MISSED: nobody said anything and the ring ran out.
        ENDED: it happened and it is over. The only state with a duration.
        FAILED: the provider refused the room or the grant, so there was
            never anything to answer. Distinct from ENDED on purpose: a
            support question about a call that never rang is a different
            question from one about a call that did.
    """

    RINGING = "ringing", "Ringing"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    MISSED = "missed", "Missed"
    ENDED = "ended", "Ended"
    FAILED = "failed", "Failed"


#: The states in which a person is considered to be ON a call — the set the
#: "one live call per user" gate and the sweeper both read. Named once,
#: because a second spelling of it somewhere would be a second answer to
#: whether somebody is busy.
LIVE_STATES = (CallState.RINGING, CallState.ACCEPTED)

#: The states nothing moves out of. A transition that targets one of these is
#: the last write to the row.
TERMINAL_STATES = (
    CallState.DECLINED,
    CallState.MISSED,
    CallState.ENDED,
    CallState.FAILED,
)


class CallEndReason(models.TextChoices):
    """Why it stopped — the provenance of ``ended_at``.

    The distinction that matters is between what somebody DID and what we
    INFERRED. ``HANGUP`` and ``DECLINED`` are acts. ``REMOTE_LEFT`` and
    ``ROOM_FINISHED`` are the media server reporting. ``RECONCILED`` is
    nobody reporting anything and the sweeper noticing — an estimate, bounded
    by one sweep interval, and marked as one so a reader can tell it from an
    observed departure. Same rule as ``SpanCloseReason``.

    Members:
        HANGUP: a party pressed the button.
        DECLINED: the callee refused the ring.
        RING_TIMEOUT: the ring ran out with no answer.
        REMOTE_LEFT: the media server reported a participant leaving.
        ROOM_FINISHED: the media server reported the room closing.
        RECONCILED: the sweeper found the room gone or short of two
            connections. An inference, not a report.
        MAX_DURATION: the call hit ``CALL_MAX_DURATION_SECONDS``. A call
            nobody ever hangs up is otherwise metered forever.
        PROVIDER_ERROR: the room or the grant could not be created.
    """

    HANGUP = "hangup", "Hung up"
    DECLINED = "declined", "Declined"
    RING_TIMEOUT = "ring_timeout", "Ring timed out"
    REMOTE_LEFT = "remote_left", "The other party left"
    ROOM_FINISHED = "room_finished", "The media room finished"
    RECONCILED = "reconciled", "Reconciled by the sweeper"
    MAX_DURATION = "max_duration", "Maximum duration reached"
    PROVIDER_ERROR = "provider_error", "The video backend refused"


class CallMedia(models.TextChoices):
    """What the caller asked for. The front's audio-only fallback sets
    ``AUDIO``; nothing on the server behaves differently, it travels so the
    callee's ring can say "аудиозвонок" instead of guessing."""

    AUDIO = "audio", "Audio"
    VIDEO = "video", "Video"


def room_name_for(call_id) -> str:
    """The provider room name of a call: ``call-<uuid>``.

    One function, because the name is written by the create path and read by
    the webhook path, the sweeper and the token mint — four places that must
    agree on a string. It is deliberately NOT derivable in the other
    direction by anything but a lookup: the webhook handlers query
    ``Call.objects.filter(room_name=...)`` rather than slicing the prefix off,
    so a host that overrode the name never silently stops matching.
    """
    return f"call-{call_id}"


class Call(models.Model):
    """One person ringing one other person.

    Cross-service references are opaque strings, not FKs
    (library-standard §3.8): ``thread_key`` is the chat conversation this call
    hangs off and this module never resolves, parses or joins on it. The two
    parties ARE FKs, because they are ``settings.AUTH_USER_MODEL`` — the one
    model this library is allowed to point at.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: The chat conversation this call belongs to. Opaque: the library never
    #: interprets it, and the authorizer hands it to whatever answers
    #: ``CALL_PARTICIPANTS_FUNCTION``. Blank means "no thread", which the
    #: default authorizer refuses — see calls/authorize.py.
    thread_key = models.CharField(max_length=255, blank=True, default="", db_index=True)

    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_calls_made",
    )
    callee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_calls_received",
    )

    #: The provider room ref — ``call-<id>``. Unique because it is the key the
    #: webhook path looks a call up by, and two calls answering to one room
    #: name would make a departure ambiguous.
    room_name = models.CharField(max_length=255, unique=True)

    #: The reporting partition, resolved by the SCOPE_PROVIDER seam at create
    #: time and put on the grant, exactly as Room.scope_key is. "" for a host
    #: that partitions nothing; the presence writer stores that as NULL.
    scope_key = models.CharField(max_length=255, blank=True, default="", db_index=True)

    media = models.CharField(
        max_length=8, choices=CallMedia.choices, default=CallMedia.VIDEO
    )
    state = models.CharField(
        max_length=16, choices=CallState.choices, default=CallState.RINGING
    )
    end_reason = models.CharField(
        max_length=16, choices=CallEndReason.choices, blank=True, default=""
    )

    #: When the ring began. Also the deadline's anchor: a ringing call is
    #: over at ``started_at + CALL_RING_TIMEOUT_SECONDS``.
    started_at = models.DateTimeField(auto_now_add=True)
    #: When the callee accepted. NULL for every call that was never answered,
    #: which is what makes an unanswered call's duration zero rather than the
    #: length of its ring.
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "video"
        ordering = ["-started_at"]
        constraints = [
            # The BACKSTOP for "one live call per user", not the gate. The
            # gate is the query in services.create_call, because these two
            # constraints cannot express the cross-role case: A being the
            # callee of one call and the caller of another violates neither.
            # They close the same-role race — two simultaneous creates by one
            # caller — which is the one a retrying client actually produces.
            models.UniqueConstraint(
                fields=["caller"],
                condition=Q(state__in=("ringing", "accepted")),
                name="video_call_one_live_as_caller",
            ),
            models.UniqueConstraint(
                fields=["callee"],
                condition=Q(state__in=("ringing", "accepted")),
                name="video_call_one_live_as_callee",
            ),
            # A call to oneself is not a call. Checked here as well as in the
            # serializer because the serializer is a seam a host may swap.
            models.CheckConstraint(
                condition=~Q(caller=models.F("callee")),
                name="video_call_two_parties",
            ),
        ]
        indexes = [
            # The gate's query and /calls/active: this user's live calls.
            models.Index(fields=["caller", "state"], name="video_call_caller_state"),
            models.Index(fields=["callee", "state"], name="video_call_callee_state"),
            # The sweeper's query: everything still live, oldest first.
            models.Index(fields=["state", "started_at"], name="video_call_state_started"),
            models.Index(fields=["thread_key", "started_at"], name="video_call_thread"),
        ]

    def __str__(self):
        return f"{self.caller_id} -> {self.callee_id} ({self.state})"

    # ── Derived ────────────────────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        return self.state in LIVE_STATES

    @property
    def duration_seconds(self) -> int:
        """How long the two of them were actually connected.

        Zero — not the ring length — for a call that was never answered: a
        missed call took no one's time, and reporting the 45 seconds it rang
        would put ring time into whatever reads this next.
        """
        if not self.answered_at or not self.ended_at:
            return 0
        return max(0, int((self.ended_at - self.answered_at).total_seconds()))

    def other_party_id(self, user_id):
        """The id of whichever party ``user_id`` is not, or None for a
        stranger. Used to address the peer's own frames without every caller
        re-writing the same ternary."""
        user_id = str(user_id)
        if str(self.caller_id) == user_id:
            return self.callee_id
        if str(self.callee_id) == user_id:
            return self.caller_id
        return None

    def involves(self, user_id) -> bool:
        return self.other_party_id(user_id) is not None


__all__ = [
    "LIVE_STATES",
    "TERMINAL_STATES",
    "Call",
    "CallEndReason",
    "CallMedia",
    "CallState",
    "room_name_for",
]
