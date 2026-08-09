"""Video provider contract (ABC) — the one seam a video vendor plugs into.

Generalized from a production LiveKit integration. ``mint_join_token`` and
``create_room`` are the mandatory core (every backend must issue join tokens
and name its media room). The recording-egress trio + ``parse_webhook`` have
default ``NotImplementedError`` bodies rather than being abstract, so a
token-only backend (or a test fake that only exercises admission) stays valid
without implementing recording.
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
        self, provider_room_ref: str, user_id, user_name: str
    ) -> str:
        """Return a signed token letting ``user_id`` join ``provider_room_ref``.

        The name travels INSIDE the token, so it is frozen at mint time. A
        provider that can push a later correction into a room the person is
        already sitting in implements :meth:`rename_participant`; see its
        docstring for why that is not optional in practice.
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
