"""scope_key provider — the scope/membership extension seam.

The library is scope-agnostic: ``Room.scope_key`` is an opaque string the host
owns. A ``ScopeProvider`` (dotted path in ``STAPEL_VIDEO["SCOPE_PROVIDER"]``)
resolves the scope_key from the current request, filters querysets by it, and
answers the one video-specific question the ``scope_trusted`` access level
needs: *is this user a trusted member of the room's scope?* The default is a
single global scope where every authenticated user is a member.
"""
from __future__ import annotations

from stapel_core.django.scope import MandateScopeMixin


class ScopeProvider:
    """Contract for scope resolution/filtering + membership. Subclass and
    point ``STAPEL_VIDEO["SCOPE_PROVIDER"]`` at it to scope video rooms."""

    def resolve(self, request) -> str:
        """Return the scope_key to stamp on rooms created via ``request``."""
        raise NotImplementedError

    def filter(self, queryset, request):
        """Restrict ``queryset`` to the scope visible to ``request``."""
        raise NotImplementedError

    def is_member(self, request, scope_key: str) -> bool:
        """True if ``request``'s user is a trusted member of ``scope_key``.

        Drives the ``scope_trusted`` auto-admit decision — which mints a live
        media token and skips the lobby, so a False here is a lobby wait and a
        True here is a seat in the call. A room with an empty ``scope_key``
        (no scope) has no trusted members by definition unless a provider says
        otherwise.

        Answer False for "not a member". Raise
        ``stapel_core.django.api.permissions.MandateUnavailable`` (503) for
        "could not find out": a token is not the thing to hand out while the
        question is still open.
        """
        raise NotImplementedError


class DefaultScopeProvider(MandateScopeMixin, ScopeProvider):
    """Single global scope: every room gets ``scope_key=""`` and no query is
    filtered (single-tenant hosts and tests).

    ``is_member`` used to return ``user.is_authenticated``, which said that
    "trusted member of this scope" and "has an account" are the same sentence.
    They are not, and the gap between them was a join code away from a live
    media token. It now answers with the third principal state
    (``stapel_core.django.scope``): an account holding no mandate anywhere is
    a member of nothing, so it waits in the lobby like any other stranger. In
    a genuinely standalone deployment, where nobody holds a mandate, the
    permissive behaviour stands and ``checks.py`` says so out loud.

    Swap for a workspace-aware provider in production: this closes the guest
    state, it does not tell one tenant's rooms from another's
    (``stapel_video.E009``).
    """

    def resolve(self, request) -> str:
        return ""

    def filter(self, queryset, request):
        return queryset

    def is_member(self, request, scope_key: str) -> bool:
        return self.mandate_admits(request)


def get_scope_provider() -> ScopeProvider:
    """Resolve the configured provider (already import_string'd by conf)."""
    from .conf import video_settings

    provider = video_settings.SCOPE_PROVIDER
    return provider() if isinstance(provider, type) else provider
