"""Action subscriptions of stapel-video.

Handlers must be idempotent: delivery is at-least-once (outbox retries, broker
redelivery). Consumes contracts live in ``schemas/consumes/``.
"""
import logging

from django.core.exceptions import ValidationError

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)


class MergeTargetNotReady(RuntimeError):
    """A ``user.merged`` arrived before the surviving account exists here.

    Transient, not a bug: the guest has rooms or admissions to carry over but
    there is no local user row to point their FKs at yet. Raising is the comm
    layer's retry signal — ``deliver()`` wraps a failing handler in
    ``ActionDeliveryError`` and the outbox redelivers — so the transfer
    completes once the survivor's user projection lands. An operator seeing
    this in a redelivery loop is looking at an ordering lag, not a defect.
    """


# The GDPR erasure protocol is NOT subscribed here. Since 0.8.0 this module
# registers as a stapel-gdpr data owner from ``apps.ready()`` and
# ``stapel_core.gdpr.register_gdpr_owner`` subscribes all three actions —
# ``gdpr.erasure.requested`` (erase + receipt), ``gdpr.owner.probe``
# (``gdpr.owner.alive`` answered from the SAME subscriber, which is what makes
# the answer evidence that the erasure path is consumed) and the deprecated
# ``user.deleted``, which used to be handled here. All three run
# :func:`stapel_video.erasure.erase_subject`; a second handler for
# ``user.deleted`` would be a second erasure to keep in step.


@on_action("profile.changed")
def handle_profile_changed(event):
    """Carry a renamed person's new name into the calls they are sitting in.

    The display name is a claim inside the join token
    (``VideoProvider.mint_join_token``), so it is frozen at the instant the
    connection was made. Everything else in a deployment reads the name back
    from stapel-profiles and is correct the moment the write commits; a video
    tile is the one surface that keeps rendering the name it was handed at
    join. Without this subscriber the gap closes only when the person happens
    to reconnect — which is why the symptom presents as ONE stale tile rather
    than a broken feature, and why it survives being looked for.

    stapel-profiles already publishes ``profile.changed`` on every write to
    the canonical name, including the roster-side correction an owner makes
    through stapel-workspaces. Consuming it is what makes the propagation a
    property of installing this module instead of an obligation the host has
    to know about: no product code, no ordering rule between two libraries,
    nothing to forget.

    "Which calls is this person in" goes through the ``LIVE_ROOMS_PROVIDER``
    seam rather than this module's models: a host can adopt the provider and
    keep its own rooms, and the subscriber has to work for it too — otherwise
    the capability is bought and the *calling* of it is left as a prose
    obligation, which is the defect class this whole path exists to close.

    Idempotent (at-least-once delivery): the provider skips connections that
    already carry the name, and a person in no live room is zero work.
    """
    user_id = event.payload.get("user_id")
    if not user_id:
        logger.error("profile.changed event without user_id: %s", event.event_id)
        return
    display_name = event.payload.get("display_name") or ""

    from .live_rooms import get_live_rooms_provider
    from .providers import get_video_provider

    room_refs = [ref for ref in get_live_rooms_provider().live_rooms_for_user(user_id) if ref]
    if not room_refs:
        return

    provider = get_video_provider()
    renamed = 0
    for room_ref in room_refs:
        try:
            renamed += provider.rename_participant(room_ref, user_id, display_name)
        except NotImplementedError:
            # A token-only provider cannot push a rename. Say it once, at
            # warning, rather than leaving the operator to discover it from a
            # stale tile: the name IS saved, it just cannot reach a live call.
            logger.warning(
                "%s cannot push renames into a live room; %s keeps the old "
                "name until they rejoin",
                type(provider).__name__,
                user_id,
            )
            return
        except Exception:
            # One unreachable room must not strand the others, and must not
            # fail the profile write that triggered us: the name is already
            # canonical, this is a projection catching up.
            logger.exception("failed to push the new name into room %s", room_ref)
    if renamed:
        logger.info(
            "pushed the new display name onto %d live connection(s) of user %s",
            renamed,
            user_id,
        )


@on_action("user.merged")
def handle_user_merged(event):
    """Carry a merged-away guest's rooms, admissions and meter to the survivor.

    stapel-auth absorbs an anonymous guest into an existing account and then
    DELETES the guest row. ``Room.created_by`` and ``RoomParticipant.user``
    are ``CASCADE``, so without this handler a visitor who opened a call
    before signing in loses the room and their admission to it the moment
    they sign in with an account that already exists. ``ParticipantSpan``
    holds no FK and so survives the deletion — but it keeps metering the
    guest as a SECOND billable person, which is the same defect wearing a
    different face.

    Three models are re-pointed here, in one transaction:

    * :class:`~stapel_video.models.Room` — ``created_by``. Plain rewrite; no
      constraint is scoped to the creator.
    * :class:`~stapel_video.models.RoomParticipant` — ``user``. Deduplicated
      against ``video_participant_uniq`` first: where both accounts have a
      row in one room the survivor's own admission stays and the guest's is
      dropped. An admission is state, and re-derivable — the person knocks
      again and the lobby answers. Nobody is ejected by the drop: a live
      connection lives on the provider and in the meter, and the meter moves.
    * :class:`~stapel_video.models.ParticipantSpan` — ``user_id``. See below.

    **Why the meter is re-keyed, when erasure refuses to touch it.** The
    ledger rule for this table is *scrub the person, keep the counters*, and
    erasure obeys it by pseudonymizing rather than deleting: a closed period
    must count the same seconds. A merge does not break that rule, it serves
    it. Every span survives with its room, its scope, its instants and its
    duration — total metered time does not move by a second, and no per-room
    or per-scope rollup changes. The only figures that change are the ones
    counted *per person*, and they change toward the truth: time is unioned
    per user precisely so that two devices are not two billable people, and
    a guest promoted into an account was never two people either. Leaving
    the spans behind would bill one human twice, and would double-count the
    wall-clock overlap of the very call they signed in during. Spans already
    pseudonymized by an erasure are not matched by the exact-id filter and
    stay erased. ``video_span_uniq`` is ``(connection_id, joined_at)``, not
    user-scoped, so no re-key can collide.

    Two different "unknown id" situations, and conflating them loses data:

    * the guest owns nothing here (never opened a call, or a previous
      delivery already moved it all) — a genuine no-op, returned quietly;
      this is also the at-least-once idempotency path;
    * the guest owns rooms or admissions but the survivor has no user row
      here yet — NOT a no-op. :class:`MergeTargetNotReady` is raised so the
      event is redelivered, because returning success would let the outbox
      mark it delivered and lose the rooms for good.

    A malformed id is neither: it names no row here and no redelivery can fix
    it, so it is logged and dropped rather than raised into a poison loop.
    Django's ``UUIDField`` raises ``ValidationError``, which is not a
    ``ValueError`` — both are caught.
    """
    from django.contrib.auth import get_user_model
    from django.db import transaction

    from .models import ParticipantSpan, Room, RoomParticipant

    payload = event.payload or {}
    from_user_id = payload.get("from_user_id")
    into_user_id = payload.get("into_user_id")
    if not from_user_id or not into_user_id:
        logger.error("user.merged without from/into user id: %s", event.event_id)
        return
    if str(from_user_id) == str(into_user_id):
        return

    with transaction.atomic():
        # Every read, and the decision they feed, happens inside the
        # transaction and before the first write, so the "not yet" path below
        # can never leave half the rows moved.
        try:
            guest_admissions = list(RoomParticipant.objects.filter(
                user_id=from_user_id
            ))
            owns_rooms = Room.objects.filter(created_by_id=from_user_id).exists()
            # The survivor probe is read under the same guard, because a
            # malformed *into* id must not escape as a poison pill either.
            survivor_exists = (
                get_user_model().objects.filter(pk=into_user_id).exists()
            )
            survivor_room_ids = set(
                RoomParticipant.objects.filter(user_id=into_user_id).values_list(
                    "room_id", flat=True
                )
            )
        except (ValidationError, ValueError, TypeError):
            logger.warning("user.merged with unusable user ids: %s", event.event_id)
            return

        # The meter is keyed by a CharField, not an FK: it needs no survivor
        # row to point at and it is checked separately, because a guest whose
        # only trace here is metered time still has to be merged.
        moved_spans = ParticipantSpan.objects.filter(
            user_id=str(from_user_id)
        ).update(user_id=str(into_user_id))

        if not (guest_admissions or owns_rooms):
            if moved_spans:
                logger.info(
                    "user.merged %s -> %s: %s presence spans re-keyed",
                    from_user_id, into_user_id, moved_spans,
                )
            return
        if not survivor_exists:
            raise MergeTargetNotReady(
                f"user.merged {from_user_id} -> {into_user_id}: the surviving "
                f"account has no user row in stapel-video yet; redeliver once "
                f"its projection has landed"
            )

        moved_admissions = 0
        dropped_admissions = 0
        for admission in guest_admissions:
            if admission.room_id in survivor_room_ids:
                # The survivor already has a row in this room: theirs stays,
                # with its status, role and joined_at. Reassigning would
                # violate video_participant_uniq.
                admission.delete()
                dropped_admissions += 1
                continue
            admission.user_id = into_user_id
            admission.save(update_fields=["user"])
            survivor_room_ids.add(admission.room_id)
            moved_admissions += 1

        moved_rooms = Room.objects.filter(created_by_id=from_user_id).update(
            created_by_id=into_user_id
        )

    logger.info(
        "user.merged %s -> %s: %s rooms re-hosted, %s admissions moved, %s "
        "dropped as duplicates, %s presence spans re-keyed",
        from_user_id,
        into_user_id,
        moved_rooms,
        moved_admissions,
        dropped_admissions,
        moved_spans,
    )
