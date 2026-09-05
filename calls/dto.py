"""Dataclass DTOs for the call API — never ORM instances."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class CallResponse:
    """One call, as both parties see it.

    Attributes:
        id: Call id (UUID).
        thread_key: The chat conversation this call hangs off, echoed back.
        caller_id: Who rang.
        callee_id: Who was rung.
        room_name: The provider room ref (``call-<id>``) — the name the
            client hands to the media SDK.
        media: audio / video — what the caller asked for.
        state: ringing / accepted / declined / missed / ended / failed.
        end_reason: Why it stopped, "" while it has not.
        started_at: When the ring began (ISO-8601).
        answered_at: When it was accepted, or null.
        ended_at: When it stopped, or null.
        duration_seconds: Connected seconds — ``ended_at - answered_at``.
            Zero for a call that was never answered: a missed call took
            nobody's time, and reporting its ring length would put ring time
            into whatever reads this next.
        expires_at: When the ring runs out, while it is ringing; null
            otherwise. The client counts down against this rather than
            against its own copy of the timeout, so a clock skew shows up as
            a second, not as a ring that outlives the server's.
    """

    id: str
    thread_key: str
    caller_id: str
    callee_id: str
    room_name: str
    media: str
    state: str
    end_reason: str
    started_at: str
    answered_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: int = 0
    expires_at: Optional[str] = None


@dataclass
class CallTokenResponse:
    """A call plus the credential and address one party dials with.

    Attributes:
        call: The call.
        token: This party's signed media token. Never anybody else's, and
            never carried on a realtime frame.
        url: The media server the token is for — where the BROWSER connects,
            which on a host-networked deployment is not where the server
            connects.
    """

    call: CallResponse
    token: str
    url: str


@dataclass
class ActiveCallResponse:
    """This person's live call, or the absence of one.

    Attributes:
        call: The live call, or null. Present-and-null rather than a 204, so
            a client parses one shape and "no call" cannot be mistaken for
            "this request failed".
    """

    call: Optional[CallResponse] = None


@dataclass
class MediaTokenResponse:
    """A re-minted credential for a call already in progress.

    Attributes:
        token: A fresh signed media token for the caller of this request.
        url: Where the browser connects.
    """

    token: str
    url: str


# ── Request DTOs ────────────────────────────────────────────────────────────


@dataclass
class CallCreateRequest:
    """Ring somebody.

    Attributes:
        callee_id: The person to ring.
        thread_key: The chat conversation this call belongs to. The default
            authorizer requires it and refuses without one — a user id is not
            a phone number, and membership of a conversation is what makes it
            one.
        media: audio / video. Defaults to video.
        client_session_id: A stable per-browser id, exactly as on
            ``JoinRequest``: it makes the connection identity deterministic so
            a reload evicts its own pre-reload ghost instead of leaving one.
    """

    callee_id: str
    thread_key: str = ""
    media: str = "video"
    client_session_id: Optional[str] = None


@dataclass
class CallSessionRequest:
    """Accept a call, or re-mint its token.

    Attributes:
        client_session_id: See ``CallCreateRequest``.
    """

    client_session_id: Optional[str] = None


__all__ = [
    "ActiveCallResponse",
    "CallCreateRequest",
    "CallResponse",
    "CallSessionRequest",
    "CallTokenResponse",
    "MediaTokenResponse",
]
