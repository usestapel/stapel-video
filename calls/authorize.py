"""Who may ring whom — the ``CALL_AUTHORIZER`` seam.

A user id is not a phone number. In a marketplace, "I can name you" must not
mean "I can make your phone ring": otherwise the id in a public listing page
is a ringer, and the first thing that happens is somebody scripting it.

So a call is authorized against the CONVERSATION it hangs off. Both parties
must be participants of ``thread_key``, read through the comm Function named
by ``CALL_PARTICIPANTS_FUNCTION`` — by default ``chat.conversation_participants``,
the same seam stapel-classified reads for conversation membership. No import
of stapel-chat: the name is configuration, the answer arrives over the bus.

**Fail-closed**, in the substrate's sense (``stapel_realtime.authorize.deny``).
An empty ``thread_key``, an unregistered function, an unreachable peer, a
malformed answer: all of them are a refusal. A lookup that reached no verdict
is never an allowance — that is the whole difference between an authorizer
and a formality, and the failure it prevents is silent (a bus outage would
otherwise turn the gate off for everyone at once, and nothing would look
wrong).

A deployment with no chat at all points ``CALL_AUTHORIZER`` at its own
callable. It does not get an open default.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def thread_participants(request, *, caller, callee_id, thread_key: str) -> bool:
    """Both parties must be participants of ``thread_key``.

    Args:
        request: the DRF request, for a host authorizer that wants the
            session. Unused here — membership is a fact about the thread, not
            about how the caller authenticated.
        caller: the authenticated user object.
        callee_id: the person being rung.
        thread_key: the opaque conversation id from the request body.

    Returns:
        Whether the call may be placed. False for every uncertainty.
    """
    if not thread_key:
        # Not an oversight to be lenient about: without a thread there is
        # nothing to check membership against, and "any user may ring any
        # user id" is the exact hole this function exists to close.
        logger.info("video: refusing a call with no thread_key")
        return False

    participants = _participants_of(thread_key)
    if participants is None:
        return False
    return str(caller.pk) in participants and str(callee_id) in participants


def _participants_of(thread_key: str) -> set | None:
    """The participant ids of one conversation, or None when unanswerable.

    None and ``set()`` are deliberately different: an empty set is a real
    answer about a thread with nobody in it, while None is "nothing here
    knows". Both refuse, but only one of them is worth an error log.
    """
    from ..conf import video_settings

    function_name = (video_settings.CALL_PARTICIPANTS_FUNCTION or "").strip()
    if not function_name:
        logger.warning(
            "video: CALL_PARTICIPANTS_FUNCTION is empty, so the default call "
            "authorizer cannot check thread membership and refuses every call. "
            "Set it, or point CALL_AUTHORIZER at a host callable."
        )
        return None

    from stapel_core.comm import call as comm_call

    try:
        answer = comm_call(function_name, {"conversation_ids": [str(thread_key)]})
    except Exception:
        # A peer that cannot answer is not a peer that said yes. Logged at
        # error because a persistent one takes calling down for everybody and
        # the symptom on the front is a button that does nothing.
        logger.exception(
            "video: %s did not answer for thread %s; refusing the call",
            function_name,
            thread_key,
        )
        return None

    entry = ((answer or {}).get("conversations") or {}).get(str(thread_key)) or {}
    if not entry.get("exists"):
        return None
    return {
        str(p.get("user_id"))
        for p in (entry.get("participants") or [])
        if p.get("user_id")
    }


def allow_any(request, *, caller, callee_id, thread_key: str) -> bool:
    """Everybody may ring everybody.

    Shipped so that a deployment which genuinely has no conversation layer —
    an internal directory, a demo — can state that decision in one settings
    line instead of writing a callable to express "yes". It is NOT the
    default, and the difference matters: an open default is a hole nobody
    chose, while this is a hole somebody signed.
    """
    return True


__all__ = ["allow_any", "thread_participants"]
