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

            {"event": str, "egress_id": str | None,
             "status": str | None, "storage_key": str | None}

        Raise :class:`VideoProviderError` if the signature is invalid or the
        body is malformed — the ingress endpoint turns that into a 400.
        """
        raise NotImplementedError
