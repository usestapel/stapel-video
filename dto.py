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
    """

    access_level: Optional[str] = None
    admit_required: Optional[bool] = None


@dataclass
class LobbyActionRequest:
    """Admit or deny a waiting participant.

    Attributes:
        participant_id: The waiting participant's id (UUID).
    """

    participant_id: str


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
