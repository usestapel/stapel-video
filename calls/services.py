"""The call lifecycle — every transition, and nobody else's business.

Thin, testable functions the views call. Three rules hold everywhere in here:

1. **Every terminal transition is a conditional UPDATE** filtered on the state
   it is leaving, and returns whether IT was the one that moved the row. A
   call has several independent witnesses of its end — the person who pressed
   the button, a ``participant_left`` webhook, a ``room_finished`` webhook,
   the reconciler — and they arrive out of order and more than once. First one
   wins outright; the rest are no-ops. A ``save()`` would let the last writer
   restate the duration.
2. **Frames and pushes go out only when the row moved.** They hang off the
   return value of that UPDATE, not off the intent to write, so an
   at-least-once redelivery does not ring anybody twice.
3. **A courtesy never breaks a transition.** The signal, the push and the
   chat line are all wrapped: the call has already ended and the row is the
   truth. The reverse — a hangup that 500s because a chat service is down —
   would be the library deciding that its own bookkeeping outranks the user's
   button.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from ..providers import VideoProviderError, get_video_provider
from ..scope import get_scope_provider
from .models import (
    LIVE_STATES,
    Call,
    CallEndReason,
    CallMedia,
    CallState,
    room_name_for,
)
from .realtime import (
    SIGNAL_ACCEPTED,
    SIGNAL_DECLINED,
    SIGNAL_ENDED,
    SIGNAL_INCOMING,
    notify_user,
)

logger = logging.getLogger(__name__)


class CallBusy(Exception):
    """One of the two parties is already on a call."""


class CallNotAllowed(Exception):
    """The ``CALL_AUTHORIZER`` refused this pair for this thread."""


class InvalidCallee(Exception):
    """No such person, or the caller rang themselves."""


class CallProviderUnavailable(Exception):
    """The video backend could not give us a room or a grant."""


class InvalidCallState(Exception):
    """The call is not in a state this action can be taken from."""

    def __init__(self, state):
        self.state = state
        super().__init__(f"call is {state}")


# ── Reads ──────────────────────────────────────────────────────────────────


def get_call(call_id, user) -> Call | None:
    """One call, only for its two parties.

    A stranger gets ``None``, which the view turns into 404 — the same answer
    a call that does not exist gets. A 403 would confirm that a guessed id is
    real, and a call id names two people and a conversation.

    Reading a ringing call whose deadline has passed transitions it first, so
    the answer is never a call that is over pretending to still be ringing.
    That is what makes correctness independent of the sweeper running.
    """
    call = Call.objects.filter(pk=call_id).first()
    if call is None or not call.involves(user.pk):
        return None
    return _expire_if_overdue(call)


def active_call_for(user) -> Call | None:
    """This person's live call, if any — what a client re-reads on reconnect.

    The socket is best-effort, so a dropped ``call.incoming`` would otherwise
    leave a real call that never rang, and a dropped ``call.ended`` a ring
    that never stops. This read is the repair for both, which is why the
    front is required to call it on mount and on every reconnect (SPEC §7.1).
    """
    call = (
        Call.objects.filter(Q(caller=user) | Q(callee=user), state__in=LIVE_STATES)
        .order_by("-started_at")
        .first()
    )
    if call is None:
        return None
    call = _expire_if_overdue(call)
    return call if call.is_live else None


# ── Create ─────────────────────────────────────────────────────────────────


def create_call(
    caller,
    *,
    callee_id,
    request,
    thread_key: str = "",
    media: str = CallMedia.VIDEO,
    client_session_id: str | None = None,
) -> tuple[Call, str, str]:
    """Ring somebody. Returns ``(call, token, url)`` for the CALLER.

    The callee's token is not minted here and does not travel on the ring —
    it is handed over by :func:`accept_call`, to an authenticated request by
    the person it belongs to. A credential on a broadcast frame is a
    redaction rule somebody has to keep obeying; one that is never broadcast
    cannot be got wrong.

    Order of operations, and it matters: authorize, then gate the pair, then
    write the row, then talk to the provider. The provider call is the slow,
    failable one, and doing it before the row would either hold a transaction
    open across a network call or leave provisioned rooms nothing points at.
    Doing it after means a provider failure has a row to mark ``failed`` —
    which is a call the caller can be told about, rather than a 503 with
    nothing behind it.
    """
    from ..conf import video_settings

    callee = _resolve_callee(caller, callee_id)
    thread_key = str(thread_key or "")

    authorizer = video_settings.CALL_AUTHORIZER
    if not authorizer(
        request, caller=caller, callee_id=callee.pk, thread_key=thread_key
    ):
        raise CallNotAllowed(
            f"user {caller.pk} may not call {callee.pk} on thread {thread_key!r}"
        )

    scope_key = get_scope_provider().resolve(request) or ""

    # The id is allocated HERE, not by the insert's default, because
    # ``room_name`` is unique and derived from it. Inserting with a
    # placeholder name and patching it afterwards would make every concurrent
    # create collide on that placeholder — a unique constraint doing the
    # opposite of its job.
    call_id = uuid.uuid4()
    try:
        with transaction.atomic():
            _refuse_if_busy(caller.pk, callee.pk)
            call = Call.objects.create(
                id=call_id,
                thread_key=thread_key,
                caller=caller,
                callee=callee,
                room_name=room_name_for(call_id),
                scope_key=scope_key,
                media=media or CallMedia.VIDEO,
                state=CallState.RINGING,
            )
    except IntegrityError as exc:
        # The partial-unique backstop fired: a concurrent create by the same
        # party in the same role won the race. Same answer as the gate.
        raise CallBusy(str(exc)) from exc

    try:
        _provision_room(call)
        token = _mint(call, caller, client_session_id)
    except (VideoProviderError, ImportError) as exc:
        _fail(call, CallEndReason.PROVIDER_ERROR)
        raise CallProviderUnavailable(str(exc)) from exc

    _ring(call)
    return call, token, _client_url()


def _resolve_callee(caller, callee_id):
    from django.contrib.auth import get_user_model

    if not callee_id:
        raise InvalidCallee("callee_id is required")
    if str(callee_id) == str(caller.pk):
        raise InvalidCallee("a call needs two people")
    try:
        callee = get_user_model().objects.filter(pk=callee_id).first()
    except Exception:
        # A malformed pk (a non-UUID where the user model wants one) raises
        # from the queryset, and it is the same fact as "no such user".
        callee = None
    if callee is None:
        raise InvalidCallee(f"no such user: {callee_id}")
    return callee


def _refuse_if_busy(*user_ids) -> None:
    """The GATE for "one live call per user".

    The two partial unique constraints on the model are a backstop, not this:
    they cannot express the cross-role case (A is the callee of one call and
    the caller of another violates neither), and this query can. The residual
    race — A rings B in the same instant B rings A — leaves two ringing calls,
    and :func:`accept_call` re-runs this check so at most one is ever
    answered.
    """
    pairs = [str(uid) for uid in user_ids]
    if Call.objects.filter(
        Q(caller_id__in=pairs) | Q(callee_id__in=pairs), state__in=LIVE_STATES
    ).exists():
        raise CallBusy(f"one of {pairs} is already on a call")


def _provision_room(call: Call) -> None:
    """Ask the provider for a room capped at two.

    The cap is the SECOND lock, and the reason it is worth a round trip: the
    grant already names one room, but a grant is a signed string that can be
    copied, and a media server that refuses a third connection cannot be
    talked out of it. A provider that does not implement the method is
    tolerated — media rooms are typically lazy and the grant is still a gate —
    but it is said out loud once, because "the cap is not in force" is not
    something to discover from a screenshot with three faces on it.
    """
    from ..conf import video_settings

    provider = get_video_provider()
    try:
        provider.ensure_call_room(
            call.room_name,
            max_participants=2,
            empty_timeout_seconds=int(
                video_settings.CALL_ROOM_EMPTY_TIMEOUT_SECONDS or 60
            ),
        )
    except NotImplementedError:
        logger.warning(
            "%s cannot provision a call room, so the two-participant cap is "
            "not enforced by the media server; the token grant is the only "
            "gate on this deployment",
            type(provider).__name__,
        )


def _mint(call: Call, user, client_session_id: str | None) -> str:
    """This party's media credential for this call.

    Falls back to ``mint_join_token`` for a provider that predates
    ``mint_call_token`` — with a warning naming what was NOT applied, because
    the fallback silently restores the permissive default grant and the long
    room TTL, and a fallback nobody is told about is a downgrade nobody
    chose.
    """
    from ..conf import video_settings
    from ..services import _avatar, _display_name

    provider = get_video_provider()
    ttl = int(video_settings.CALL_TOKEN_TTL_SECONDS or 3600)
    try:
        return provider.mint_call_token(
            call.room_name,
            user.pk,
            _display_name(user),
            _avatar(user),
            client_session_id,
            scope_key=call.scope_key or None,
            ttl_seconds=ttl,
        )
    except NotImplementedError:
        logger.warning(
            "%s has no mint_call_token; falling back to mint_join_token, so "
            "this grant carries the provider's default permissions and "
            "JOIN_TOKEN_TTL_SECONDS instead of the call's explicit grant and "
            "CALL_TOKEN_TTL_SECONDS",
            type(provider).__name__,
        )
        return provider.mint_join_token(
            call.room_name,
            user.pk,
            _display_name(user),
            _avatar(user),
            client_session_id,
            scope_key=call.scope_key or None,
        )


def mint_token_for(call: Call, user, client_session_id: str | None = None) -> tuple[str, str]:
    """Re-mint a live call's credential for one of its parties.

    Behind ``POST /calls/{id}/token``, and it exists because a media token is
    presented AGAIN on every full reconnect and nothing re-mints it
    automatically. Without this, the token's TTL is a hard ceiling on coming
    back from a tunnel, and the failure looks like a network fault rather
    than an expiry.
    """
    return _mint(call, user, client_session_id), _client_url()


def _client_url() -> str:
    """Where the browser connects. Not where WE connect.

    A provider that cannot answer returns "" rather than raising: the token
    is still valid and a host may be serving the media URL to its front by
    its own means. ``stapel_video.W007`` is where a deployment finds out it
    is handing browsers an address only this process can reach — at boot,
    rather than from a client that mints a good token and never connects.
    """
    try:
        return get_video_provider().client_url() or ""
    except NotImplementedError:
        return ""
    except Exception:
        logger.exception("video: the provider could not report its client URL")
        return ""


# ── Transitions ────────────────────────────────────────────────────────────


def accept_call(call: Call, user, client_session_id: str | None = None) -> tuple[Call, str, str]:
    """The callee picks up. Returns ``(call, token, url)`` for the CALLEE."""
    if str(call.callee_id) != str(user.pk):
        raise CallNotAllowed("only the callee may accept a call")
    call = _expire_if_overdue(call)
    if call.state != CallState.RINGING:
        raise InvalidCallState(call.state)

    now = timezone.now()
    with transaction.atomic():
        # Re-run the gate. The create-time check loses one race by design —
        # the cross-role one, where A rings B in the same instant somebody
        # rings A — and the two partial unique constraints cannot see it
        # either, because being the caller of one call and the callee of
        # another violates neither. This is where it is decided: two rings
        # may exist, at most one of them may be ANSWERED.
        pair = [str(call.caller_id), str(call.callee_id)]
        if (
            Call.objects.filter(
                Q(caller_id__in=pair) | Q(callee_id__in=pair),
                state__in=LIVE_STATES,
            )
            .exclude(pk=call.pk)
            .exists()
        ):
            raise CallBusy("one of the parties is already on another call")
        moved = Call.objects.filter(pk=call.pk, state=CallState.RINGING).update(
            state=CallState.ACCEPTED, answered_at=now
        )
    if not moved:
        call.refresh_from_db()
        raise InvalidCallState(call.state)

    call.state = CallState.ACCEPTED
    call.answered_at = now
    token = _mint(call, user, client_session_id)
    _safe(
        notify_user,
        call.caller_id,
        SIGNAL_ACCEPTED,
        {"call_id": str(call.id), "answered_at": now.isoformat()},
    )
    return call, token, _client_url()


def decline_call(call: Call, user) -> Call:
    """The callee refuses. Only from ``ringing``."""
    if str(call.callee_id) != str(user.pk):
        raise CallNotAllowed("only the callee may decline a call")
    call = _expire_if_overdue(call)
    if not _terminate(call, CallState.DECLINED, CallEndReason.DECLINED, from_states=(CallState.RINGING,)):
        raise InvalidCallState(call.state)
    _safe(notify_user, call.caller_id, SIGNAL_DECLINED, {"call_id": str(call.id)})
    _announce_ended(call)
    return call


def hangup_call(call: Call, user) -> Call:
    """Either party ends it, from either live state.

    A caller hanging up while it still rings is an ``ended`` call with a zero
    duration, not a ``missed`` one: somebody was there and stopped waiting,
    which is a different fact from nobody answering, and the thread line says
    so.
    """
    if not call.involves(user.pk):
        raise CallNotAllowed("only a party of this call may hang up")
    call = _expire_if_overdue(call)
    if not _terminate(call, CallState.ENDED, CallEndReason.HANGUP, from_states=LIVE_STATES):
        raise InvalidCallState(call.state)
    _announce_ended(call)
    return call


def close_from_room(room_name: str, *, at=None, reason: str) -> Call | None:
    """End the call this media room belongs to. The three witnesses' one door.

    Called by the ``participant_left`` and ``room_finished`` webhook handlers
    and by the reconciler. Idempotent by construction: the conditional UPDATE
    inside :func:`_terminate` means the second witness to arrive changes
    nothing and reports so.

    Looked up by ``room_name``, never by slicing the ``call-`` prefix off:
    the name is written by one function (:func:`~stapel_video.calls.models.room_name_for`)
    and a host that overrides it must not silently stop matching here.
    """
    call = Call.objects.filter(room_name=room_name, state__in=LIVE_STATES).first()
    if call is None:
        return None
    if not _terminate(call, CallState.ENDED, reason, from_states=LIVE_STATES, at=at):
        return None
    _announce_ended(call)
    return call


def expire_call(call: Call) -> bool:
    """Ring timeout: ``ringing`` → ``missed``. Returns whether it moved."""
    if not _terminate(
        call, CallState.MISSED, CallEndReason.RING_TIMEOUT, from_states=(CallState.RINGING,)
    ):
        return False
    _announce_ended(call)
    _safe(_notify_missed, call)
    return True


def _fail(call: Call, reason: str) -> None:
    _terminate(call, CallState.FAILED, reason, from_states=LIVE_STATES)


def _terminate(call: Call, state: str, reason: str, *, from_states, at=None) -> bool:
    """The one write that ends a call. Returns whether THIS call moved it.

    Conditional on the state being left, so two witnesses racing resolve to
    whichever landed first and the loser is a no-op rather than a second,
    different ``ended_at``. The in-memory instance is updated only when the
    database row was, so a caller cannot report a transition that did not
    happen.
    """
    at = at or timezone.now()
    moved = Call.objects.filter(pk=call.pk, state__in=tuple(from_states)).update(
        state=state, end_reason=reason, ended_at=at
    )
    if not moved:
        call.refresh_from_db()
        return False
    call.state = state
    call.end_reason = reason
    call.ended_at = at
    return True


def _expire_if_overdue(call: Call) -> Call:
    """Transition a ringing call whose deadline has passed, on READ.

    The sweeper is what makes the transition happen for a client that never
    asks; this is what makes the ANSWER right without one. A deployment whose
    beat schedule is misconfigured then has late thread lines and late
    pushes — not calls that ring forever, and not an accept that succeeds
    four minutes after the caller gave up.
    """
    if call.state != CallState.RINGING or call.started_at is None:
        return call
    from ..conf import video_settings

    horizon = call.started_at + timedelta(
        seconds=int(video_settings.CALL_RING_TIMEOUT_SECONDS or 45)
    )
    if timezone.now() < horizon:
        return call
    expire_call(call)
    return call


# ── Telling people ─────────────────────────────────────────────────────────


def _ring(call: Call) -> None:
    """Everything that happens the moment a call starts ringing."""
    from ..conf import video_settings

    expires_at = call.started_at + timedelta(
        seconds=int(video_settings.CALL_RING_TIMEOUT_SECONDS or 45)
    )
    _safe(
        notify_user,
        call.callee_id,
        SIGNAL_INCOMING,
        {
            "call_id": str(call.id),
            "caller_id": str(call.caller_id),
            "thread_key": call.thread_key,
            "media": call.media,
            "started_at": call.started_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
    )
    if video_settings.CALL_NOTIFY_ON_RING:
        from .notify import notify_incoming

        _safe(notify_incoming, call)


def _announce_ended(call: Call) -> None:
    """The end frame goes to BOTH parties, and the thread gets its line.

    Both, because a ringing overlay has to close on the callee's screen when
    the caller gives up, and a call panel has to close on the caller's when
    the callee hangs up. One frame type, two recipients, no branching in the
    client.
    """
    payload = {
        "call_id": str(call.id),
        "state": call.state,
        "end_reason": call.end_reason,
        "duration_seconds": call.duration_seconds,
    }
    for user_id in (call.caller_id, call.callee_id):
        _safe(notify_user, user_id, SIGNAL_ENDED, payload)

    from .thread import post_call_line

    _safe(post_call_line, call)


def _notify_missed(call: Call) -> None:
    from .notify import notify_missed

    notify_missed(call)


def _safe(fn, *args, **kwargs):
    """Run a courtesy. A failure here never reaches the caller.

    Every use is a side effect that follows a transition which has already
    committed: a frame, a push, a chat line. The call is over and the row is
    the truth, so the honest failure mode is a log line — not a 500 on a
    hangup because a chat service is restarting.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("video: a call-side effect failed (%s)", getattr(fn, "__name__", fn))
        return None


__all__ = [
    "CallBusy",
    "CallNotAllowed",
    "CallProviderUnavailable",
    "InvalidCallState",
    "InvalidCallee",
    "accept_call",
    "active_call_for",
    "close_from_room",
    "create_call",
    "decline_call",
    "expire_call",
    "get_call",
    "hangup_call",
    "mint_token_for",
]
