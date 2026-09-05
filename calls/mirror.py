"""First contact with a callee this service has never met (Д320 class).

Every service in the fleet keeps a *shadow* ``users`` row, filled from one
place: the JWT middleware materialises the subject of the token it is
verifying. That covers exactly one person per request — the caller. The other
name on a call, the callee, arrives as a bare id, and the row for it appears
only when the owner's ``user.created`` fact is delivered.

Which is a race, and it is the one the fleet measured: the first call to a
seconds-old account answers ``400 invalid_callee``, and a retry a moment
later succeeds. Nothing is wrong with the account, nothing is wrong with the
request; the projection simply had not landed yet. The person on the phone
sees a call button that does not work exactly once, at the moment they are
newest — and "try again" is not a mechanism.

**So a local miss is a question, not a verdict.** Before refusing, ask the
issuer whether it knows the id, and mirror the answer on the spot. Only "the
issuer does not know this id either" is a refusal — which is what
``invalid_callee`` was always supposed to mean.

Two things this deliberately does not do:

* **It does not invent a user.** The row is written by
  ``stapel_core.django.jwt.utils.get_or_create_user_from_jwt`` — the very
  function the JWT middleware writes shadow rows with, and the one
  ``stapel_auth.projection`` applies the ``user.created`` event with. Three
  paths, one writer: a claim added to the token is a claim added here, and
  the deletion tombstone and the deactivation gate that function consults
  apply to a mirrored callee exactly as they do to an authenticated caller.
  A deleted account cannot be dialled back into existence.
* **It does not become the transport.** The answer comes over the bus, by
  the NAME in ``CALL_USER_LOOKUP_FUNCTION`` — no import of stapel-auth, and
  a deployment whose identity lives elsewhere points the name at its own
  provider. Every uncertainty (no name, no route, an exception, an answer
  with no id) is a refusal, because "somebody may be dialled because we
  could not check" is the hole ``CALL_AUTHORIZER`` spends a module closing.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["mirror_user"]


def mirror_user(user_id):
    """The user row for *user_id*, mirrored from the issuer, or ``None``.

    ``None`` means "nothing here can vouch for this id" — an unknown id, an
    unanswerable lookup, a tombstoned account. The caller refuses on it.
    """
    payload = _claims_from_issuer(user_id)
    if not payload:
        return None
    if not payload.get("user_id"):
        logger.warning(
            "video: the user lookup answered for %s without a user_id; "
            "refusing rather than mirroring a row with no identity", user_id
        )
        return None

    from stapel_core.django.jwt.utils import get_or_create_user_from_jwt

    try:
        user = get_or_create_user_from_jwt(payload)
    except Exception:
        logger.exception("video: could not mirror user %s on first contact", user_id)
        return None
    if user is None:
        # get_or_create_user_from_jwt refused: the account is tombstoned,
        # deactivated at the issuer, or this service owns its user table and
        # is not a shadow store at all. Each is a real "no", not a race.
        logger.info("video: the issuer's answer for %s was not mirrorable", user_id)
        return None
    logger.info("video: mirrored user %s on first contact", user_id)
    return user


def _claims_from_issuer(user_id) -> dict | None:
    """The issuer's claim set for one id, or ``None`` when unanswerable.

    Reachability is checked before the call rather than caught after it: on
    the ``inprocess`` transport an unregistered name raises, and a deployment
    that has no such provider should not pay an exception and a stack trace
    for every call placed to an id it simply does not hold.
    """
    from stapel_core.comm import call as comm_call
    from stapel_core.comm import function_unreachable_reason

    from ..conf import video_settings

    name = (video_settings.CALL_USER_LOOKUP_FUNCTION or "").strip()
    if not name:
        return None
    reason = function_unreachable_reason(name)
    if reason:
        logger.info(
            "video: %s is unreachable (%s), so a callee this service has not "
            "met yet cannot be confirmed and the call is refused",
            name,
            reason,
        )
        return None
    try:
        answer = comm_call(name, {"user_id": str(user_id)})
    except Exception:
        logger.exception(
            "video: %s did not answer for %s; refusing the call", name, user_id
        )
        return None
    if not answer:
        # A real answer about an id nobody knows. Not worth a stack trace:
        # this is the ordinary refusal path for a genuinely bad callee_id.
        return None
    # Both shapes are accepted: the claim set itself, and one wrapped in a
    # "user" key. A provider is somebody else's code and this is a read.
    payload = answer.get("user") if isinstance(answer.get("user"), dict) else answer
    return payload if isinstance(payload, dict) else None
