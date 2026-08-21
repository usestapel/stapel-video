"""Video provider contract (ABC) — the one seam a video vendor plugs into.

Generalized from a production LiveKit integration. ``mint_join_token`` and
``create_room`` are the mandatory core (every backend must issue join tokens
and name its media room). Everything else — the live-connection pair
(``rename_participant`` / ``remove_participant``), the room-metadata pair, the
health probe, the recording-egress trio and ``parse_webhook`` — has a default
``NotImplementedError`` body rather than being abstract, so a token-only
backend (or a test fake that only exercises admission) stays valid.

The optional half is not decoration. Every method here addresses a LIVE
connection or the running media room, and only the provider can: it invented
the identity convention in ``mint_join_token``. A capability missing from this
contract is a capability the product has to reach the vendor SDK for directly
— which is how a provider layer gets forked in the first place.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class VideoProviderError(Exception):
    """Provider-side failure (bad response, transport error, bad webhook)."""


class VideoProvider(ABC):
    """A pluggable video-conferencing backend for rooms."""

    @abstractmethod
    def create_room(self, join_code: str, *, scope_key: str = "") -> str:
        """Provision (or lazily name) the media room for ``join_code``.

        Returns the opaque ``provider_room_ref`` stored on the Room — the
        provider's own room name/id the join token is later scoped to. Raise
        :class:`VideoProviderError` on failure.
        """
        raise NotImplementedError

    @abstractmethod
    def mint_join_token(
        self,
        provider_room_ref: str,
        user_id,
        user_name: str,
        user_avatar: str = "",
        client_session_id: str | None = None,
    ) -> str:
        """Return a signed token letting ``user_id`` join ``provider_room_ref``.

        The name travels INSIDE the token, so it is frozen at mint time. A
        provider that can push a later correction into a room the person is
        already sitting in implements :meth:`rename_participant`; see its
        docstring for why that is not optional in practice.

        ``user_avatar`` is the picture the other clients render next to the
        name. It rides along the same one-way trip the name does, which is why
        :meth:`rename_participant` has to carry it back out untouched — a
        mechanism that guards a field nothing can set is a mechanism guarding
        nothing.

        ``client_session_id``, when the caller supplies one, makes the
        connection identity DETERMINISTIC for that browser instead of random.
        The identity is the only handle a provider has on a connection, so a
        fresh random one per connect means a page reload arrives as a
        *stranger*: the pre-reload connection is still seated, and the vendor
        can only reap it on its own disconnect timeout. Meanwhile the tile is
        real, silent and duplicated — for every adopter, on every reload. It
        heals itself a minute later, which is exactly why nobody ever catches
        it in the act. Reconnecting under the SAME identity makes the vendor
        evict the stale connection the instant the new one lands. Callers that
        pass nothing keep the random suffix, so two genuine devices under one
        user id still get two identities.
        """
        raise NotImplementedError

    def rename_participant(
        self, provider_room_ref: str, user_id, user_name: str
    ) -> int:
        """Push ``user_name`` onto ``user_id``'s LIVE connections in a room.

        The counterpart to :meth:`mint_join_token`, and the reason it exists:
        the display name is a claim inside a signed token, so every connection
        carries the name that was canonical at the instant it was minted. A
        rename that lands while the person is connected reaches the database
        and every REST reader immediately and reaches that person's video tile
        never — until they happen to reconnect. Reconnects are invisible to
        the people watching, which is what makes the defect read as "one
        person's tile is wrong": everyone who reconnected after the write
        looks correct, and whoever held one socket looks stale.

        The provider owns this because the provider owns the identity
        convention it invented in ``mint_join_token`` — a caller cannot
        address a connection it never named. Implementations map ``user_id``
        to their own live connections and return how many they updated; ``0``
        is the ordinary answer for someone who is not in the room, not a
        failure. Raise :class:`VideoProviderError` on transport failure.

        Default ``NotImplementedError``, like the egress trio: a token-only
        backend stays valid. ``actions.handle_profile_changed`` treats it as
        "this provider cannot push renames" and says so once, rather than
        pretending the rename arrived.
        """
        raise NotImplementedError

    def remove_participant(self, provider_room_ref: str, user_id) -> int:
        """Disconnect every live connection ``user_id`` holds in a room.

        The kick counterpart of :meth:`rename_participant`, and it is here for
        the same reason: the caller cannot address a connection it never
        named. One person is N live identities (laptop, phone), so a kick that
        removes one of them is not a kick. Returns how many connections were
        disconnected; ``0`` — nobody of that user was in the room — is an
        ordinary answer, not a failure. Raise :class:`VideoProviderError` on
        transport failure.

        Hosts reach this through their own kick/eviction paths (a host
        removing a guest, a membership revoked upstream). It lives on the
        contract rather than in a host helper because a host helper is a fork
        of the provider by another name: the moment it exists, the vendor SDK
        is imported in product code again and the next capability lands there
        too.
        """
        raise NotImplementedError

    # ── Room metadata ──────────────────────────────────────────────────

    def get_room_metadata(self, provider_room_ref: str) -> dict:
        """Read the room-level metadata blob every connected client sees.

        A parsed dict; ``{}`` both for "no metadata" and for a room the
        provider has not materialized yet (media rooms are typically lazy —
        an empty room does not exist). Raise :class:`VideoProviderError` only
        on a transport/protocol failure.
        """
        raise NotImplementedError

    def update_room_metadata(self, provider_room_ref: str, metadata: dict) -> bool:
        """Write the room-level metadata blob. Returns whether it landed."""
        raise NotImplementedError

    # ── Health ─────────────────────────────────────────────────────────

    def probe_reachable(self) -> bool:
        """Cheapest real call proving the backend answers us right now.

        For a health endpoint (``register_dependency_check``), not for request
        paths. It must exercise the same auth + network path the real calls
        use — a probe that only pings a port answers "reachable" for a
        deployment whose credentials are wrong — and it must not touch any
        room's state. Never raises: an unreachable backend is ``False``.
        """
        raise NotImplementedError

    # ── Recording egress (seam only in v0.1.0) ─────────────────────────

    def start_room_egress(self, provider_room_ref: str, storage_key: str) -> str:
        """Start a room-composite recording writing the media file to
        ``storage_key`` in the recordings object store. Returns the
        provider-side egress id. Raise :class:`VideoProviderError` on failure.
        """
        raise NotImplementedError

    def stop_room_egress(self, egress_id: str) -> None:
        """Stop an active egress. Stopping an already-finished egress must not
        raise (the goal — no active egress — is met either way)."""
        raise NotImplementedError

    def parse_webhook(self, body: bytes, auth_header: str) -> dict:
        """Verify + decode a provider webhook. Returns a normalized dict::

            {"event": str,
             "event_id": str | None,          # the provider's own event id
             "event_ts": datetime | None,     # the PROVIDER's clock, not ours
             "room": {"name": str, "sid": str} | None,
             "participant": {"identity": str, "user_id": str,
                             "connection_id": str, "name": str,
                             "joined_at": datetime | None} | None,
             "egress_id": str | None,
             "status": str | None,
             "storage_key": str | None}

        The four egress keys came first and are unchanged; everything above
        them was added in 0.6.0. Until then this method collapsed EVERY event
        into those four, so ``participant_joined`` / ``participant_left`` /
        ``room_finished`` arrived, verified, and were dropped on the floor —
        the media server is the only witness of a departure that survives a
        client crash, and the normalizer was throwing it away. A provider
        answers with what the event actually carried and ``None`` for the
        rest; a caller reads the keys its handler needs.

        ``event_ts`` and ``participant["joined_at"]`` are **the provider's
        server timestamps**, timezone-aware, never the moment we received the
        POST: webhooks are retried, queued and reordered, so receipt time is a
        measure of our own delivery path and not of anybody's presence.

        ``participant`` carries the raw ``identity`` AND its decomposition,
        because the provider is the one that invented the convention in
        :meth:`mint_join_token` (see :func:`split_identity`). A caller that
        re-parses an identity string is a caller that has forked the provider.

        Raise :class:`VideoProviderError` if the signature is invalid or the
        body is malformed — the ingress endpoint turns that into a 400.
        """
        raise NotImplementedError

    # ── Live roster ────────────────────────────────────────────────────

    def list_participants(self, provider_room_ref: str) -> list[dict] | None:
        """Who the media server says is connected to this room RIGHT NOW.

        Returns one dict per live connection in the same normalized shape
        ``parse_webhook`` puts under ``participant`` (``identity``,
        ``user_id``, ``connection_id``, ``name``, ``joined_at``), or ``None``
        when the room does not exist on the provider — media rooms are
        typically lazy, so "nobody is in there" and "no such room" are the
        same fact and ``None`` says it once. An empty list means the room
        exists and is empty.

        This is on the contract because a webhook stream is at-least-once and
        at-most-eventually: a dropped ``participant_left`` leaves a presence
        record open forever, and only a second, independent reading of the
        room can close it. The repair loop
        (``stapel_video.presence.sweep_open_spans``) is a fleet-level
        capability, so the reading it needs has to be a capability of the
        seam rather than a private method one vendor's class happens to have
        — a private one means the host reaches for the vendor SDK, which is
        how a provider layer gets forked (SWAP004).

        Default ``NotImplementedError``: a token-only backend stays valid and
        the sweeper reports that this provider cannot be reconciled, rather
        than pretending every open span is alive.
        """
        raise NotImplementedError


def split_identity(identity: str) -> tuple[str, str]:
    """Decompose a minted connection identity into ``(user_id, connection_id)``.

    The convention is ``{user_id}_{client_session_id}`` (see
    :meth:`VideoProvider.mint_join_token`): one person on a laptop and a phone
    is one ``user_id`` and two ``connection_id``s, which is exactly the
    granularity presence metering needs — time is unioned per user, but a
    connection is what joins and leaves.

    The separator is part of the split, and only the FIRST one counts: a
    ``client_session_id`` may itself contain underscores, a user id (a UUID)
    does not. An identity minted before the convention existed carries no
    separator at all; it is its own connection, which is what the bare
    equality arm of ``providers.livekit._mine`` already assumes.

    Lives on the base module rather than in the LiveKit class because the
    convention is declared by the ABC, and the ingest path decomposes
    identities that any provider minted.
    """
    identity = str(identity or "")
    user_id, sep, connection_id = identity.partition("_")
    if not sep:
        return identity, identity
    return user_id, connection_id
