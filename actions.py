"""Action subscriptions of stapel-video.

Handlers must be idempotent: delivery is at-least-once (outbox retries, broker
redelivery). Consumes contracts live in ``schemas/consumes/``.
"""
import logging

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)


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
