"""Dataclass DTOs — the API models of stapel-video (never ORM instances)."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class RoomResponse:
    """A video room.

    Attributes:
        id: Room id (UUID).
        join_code: Human-shareable join code (abc-defg-hij).
        scope_key: Opaque host scope (workspace/org/tenant).
        access_level: public / scope_trusted / restricted.
        admit_required: Whether the lobby is on.
        created_by_id: Creator's user id.
        provider_room_ref: Opaque provider room reference.
    """

    id: str
    join_code: str
    scope_key: str
    access_level: str
    admit_required: bool
    created_by_id: str
    provider_room_ref: str


@dataclass
class ParticipantResponse:
    """A participant in a room.

    Attributes:
        id: Participant id (UUID).
        user_id: The participant's user id.
        status: waiting / admitted / denied / left.
        role: host / guest.
        joined_at: When the participant first joined (lobby FIFO order).
    """

    id: str
    user_id: str
    status: str
    role: str
    joined_at: datetime


@dataclass
class JoinResponse:
    """The outcome of a join attempt.

    Attributes:
        status: admitted / waiting / denied.
        room: The room joined.
        participant: The caller's participant row.
        token: A signed join token (only when status == admitted).
    """

    status: str
    room: RoomResponse
    participant: ParticipantResponse
    token: Optional[str] = None


@dataclass
class AdmitResponse:
    """The outcome of a host admit action.

    Attributes:
        participant: The now-admitted participant.
        token: The join token minted for them.
    """

    participant: ParticipantResponse
    token: str


# ── Request DTOs ────────────────────────────────────────────────────────────


@dataclass
class RoomCreateRequest:
    """Create a room.

    Attributes:
        access_level: public / scope_trusted / restricted. Omit for the
            configured DEFAULT_ACCESS_LEVEL axis default.
        admit_required: Whether the lobby starts on. Omit for the configured
            DEFAULT_ADMIT_REQUIRED axis default.
        client_session_id: Stable per-browser mark — see JoinRequest.
    """

    access_level: Optional[str] = None
    admit_required: Optional[bool] = None
    client_session_id: Optional[str] = None


@dataclass
class JoinRequest:
    """Join a room.

    Attributes:
        client_session_id: A stable per-browser id the client keeps across
            reloads (and only across reloads — a genuinely new tab or device
            should send a different one). The provider folds it into the
            connection identity so a reconnect after a reload lands under the
            SAME identity and the vendor evicts the pre-reload connection on
            sight, instead of leaving a ghost tile until its disconnect
            timeout. Omit it and the identity is random per connection, which
            is the pre-0.4.0 behavior: correct, and quietly leaving one ghost
            per reload per viewer.
    """

    client_session_id: Optional[str] = None


@dataclass
class LobbyActionRequest:
    """Admit or deny a waiting participant.

    Attributes:
        participant_id: The waiting participant's id (UUID).
    """

    participant_id: str


@dataclass
class ScopeUsageRow:
    """One person's presence inside one scope, for one month.

    Attributes:
        user_id: The person's id. An ID, never a name — this library does not
            learn who anybody is, and the host resolves the display name from
            the roster it already has.
        presence_seconds: Unioned seconds present. A laptop and a phone were
            one human being present, so two devices never double-count.
        rooms: Distinct calls attended (distinct room_key), not span count.
        connections: Distinct connections — where the reconnects show up.
        first_seen: First moment present in the window (ISO-8601, clipped).
        last_seen: Last moment present in the window (ISO-8601, clipped).
    """

    user_id: str
    presence_seconds: int
    rooms: int
    connections: int
    first_seen: str
    last_seen: str


@dataclass
class ScopeUsageMonth:
    """One calendar month of one scope's usage.

    Attributes:
        month: The bucket label, "YYYY-MM", in the requested time zone.
        period_start: The month's first instant, as UTC ISO-8601.
        period_end: The month's end (exclusive), as UTC ISO-8601.
        users: One row per person, longest presence first. Empty for a month
            with no calls — present rather than omitted, so "no calls" cannot
            be mistaken for "this row failed to load".
    """

    month: str
    period_start: str
    period_end: str
    users: List[ScopeUsageRow] = field(default_factory=list)


@dataclass
class ScopeUsageResponse:
    """Per-month, per-person usage of one scope.

    Attributes:
        scope_key: The partition asked about, echoed back.
        tz: The time zone the month buckets were cut in.
        months: Newest month first. A single-month request answers with a
            one-element list, so a client renders one shape either way.
    """

    scope_key: str
    tz: str
    months: List[ScopeUsageMonth] = field(default_factory=list)


@dataclass
class ParticipantListResponse:
    """An anchor-paginated page of participants (mirrors core AnchorPagination).

    Attributes:
        items: Participants in this page (FIFO by joined_at).
        next_anchor: joined_at cursor for the next page, or null.
        prev_anchor: joined_at cursor for the previous page, or null.
        has_next: More items after this page.
        has_prev: Items before this page.
        count: Items in this page.
    """

    items: List[ParticipantResponse] = field(default_factory=list)
    next_anchor: Optional[str] = None
    prev_anchor: Optional[str] = None
    has_next: bool = False
    has_prev: bool = False
    count: int = 0
