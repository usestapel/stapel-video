"""What stapel-video erases when a subject is erased — one operation.

stapel-video was a declared data owner that answered no
``gdpr.owner.probe``: its :class:`~stapel_video.gdpr.VideoGDPRProvider` ran
in-process in a monolith and the ``user.deleted`` handler in ``actions.py``
really did erase, but an owners-health board reported ``video: alive=false``
forever, because liveness is answered by the subscriber that erases and
there was none. A fleet's erasure then waits on this module until it times
out.

So the operation lives here once and is reached three ways:

* :class:`~stapel_video.gdpr.VideoGDPRProvider` — the in-process registry
  the orchestrator walks in a monolith (``provider.delete(user_id)``);
* the ``gdpr.erasure.requested`` subscriber that
  :func:`stapel_core.gdpr.register_gdpr_owner` builds from this callable in
  ``apps.ready()`` — the path that also answers the probe;
* the deprecated ``user.deleted`` signal, subscribed by the same helper
  (stapel-gdpr still emits it for one minor). ``actions.py`` no longer
  carries its own handler: two handlers for one signal is two erasures to
  keep in step.

**The meter is not deleted; it is pseudonymized.** This module carries a
ledger — ``ParticipantSpan``, the intervals a licence is counted from — and
the fleet's rule for a ledger-bearing owner is scrub the person, keep the
counters. A span holds no text, so what is personal about it *is* the
``user_id`` column, and
:func:`stapel_video.presence.pseudonymize_user` rewrites it to a stable
keyed digest: the same subject stays one subject, so distinct counts and
pair overlaps do not move, and nothing reversible is left. Deleting the rows
instead would silently restate closed reporting periods. ``ParticipantSpan``
semantics are untouched by this release — this is the erasure discipline the
column was made a ``CharField`` for.

Rooms and admissions are the person's own trail and go: a room the subject
hosted is hard-deleted (cascading to its participant rows), and their
admission to somebody else's room is removed while that room survives for
its host.
"""
from __future__ import annotations

#: The name this module answers to in ``STAPEL_GDPR["DATA_OWNERS"]`` — the
#: same string as ``VideoGDPRProvider.section``, because an owner with two
#: names is an owner whose receipts land on nobody's part.
OWNER = "video"

#: Subject types this module can really erase, and therefore the only ones it
#: claims and answers ``gdpr.owner.alive`` with. Every row here is found by
#: one user id; ``scope_key`` / ``room_key`` are opaque strings a host's scope
#: provider computes, not workspace or meeting ids this module could match a
#: workspace erasure on, so claiming those types would mint a receipt for work
#: nobody could have done.
SUBJECT_TYPES = ("account",)


def erase_subject(subject_type: str, subject_key, workspace_id=None) -> dict | None:
    """Erase one subject's video trail. Returns the receipt's counts.

    ``None`` means "this key names nothing of mine" — the subject type is not
    one this module claims, so the caller owes no receipt (stapel-gdpr creates
    a part only for owners that claim the type).

    Idempotent: rooms and admissions are matched by user id, so a redelivery
    matches nothing; the meter's pseudonymization is idempotent by the same
    filter (a span already rewritten no longer carries the original id, and
    :func:`~stapel_video.presence.pseudonymize_user` never mints a second
    pseudonym for one subject). Both receipt their zeroes on a second run
    rather than pretending the work happened twice.

    ``workspace_id`` is accepted and ignored — an account request may carry it
    as a partition hint for owners that need one, and narrowing by it here
    would leave the subject's rooms in every other tenant.

    A key this module cannot parse raises ``ValueError`` / ``ValidationError``
    out of the ORM, which the protocol handler logs and never receipts: an
    unusable key names no row here, and a receipt would claim an erasure that
    did not happen.
    """
    if subject_type not in SUBJECT_TYPES:
        return None
    # Never stringified for the FK lookups: the user pk is a UUID in some
    # deployments and an integer in others, and both spellings must reach the
    # ORM as they came — the protocol hands over a string, the in-process
    # provider hands over whatever the host's pk is, and a str() around the
    # latter turns a valid integer key into an unparseable one. The meter's
    # own column is a CharField and does its own str().
    key = subject_key.strip() if isinstance(subject_key, str) else subject_key
    if key is None or key == "":
        return None

    from .models import Room, RoomParticipant
    from .presence import pseudonymize_user

    # Rooms the subject hosted, cascading to every admission row on them —
    # attendance at a call that no longer exists.
    rooms = _deleted(Room, created_by_id=key)
    # What is left: the subject's admission to OTHER people's rooms. Those
    # rooms stay — they are their hosts' record — minus this row.
    participations = _deleted(RoomParticipant, user_id=key)
    # The meter keeps its arithmetic and loses the person. Counted as rows
    # touched, which is what the receipt may honestly claim: these rows were
    # not removed, they were made unattributable.
    spans = pseudonymize_user(key)
    return {
        "rooms": rooms,
        "participations": participations,
        "presence_spans": spans,
    }


def _deleted(model, **lookup) -> int:
    """Rows of *model* this run actually removed — what a receipt may claim.

    Django's ``delete()`` returns the total across cascades; the per-label
    breakdown beside it is the honest number for this model, so a count never
    inflates itself with somebody else's rows.
    """
    _, per_model = model.objects.filter(**lookup).delete()
    return per_model.get(model._meta.label, 0)


__all__ = ["OWNER", "SUBJECT_TYPES", "erase_subject"]
