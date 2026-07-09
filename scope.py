"""scope_key provider — the scope/membership extension seam.

The library is scope-agnostic: ``Room.scope_key`` is an opaque string the host
owns. A ``ScopeProvider`` (dotted path in ``STAPEL_VIDEO["SCOPE_PROVIDER"]``)
resolves the scope_key from the current request, filters querysets by it, and
answers the one video-specific question the ``scope_trusted`` access level
needs: *is this user a trusted member of the room's scope?* The default is a
single global scope where every authenticated user is a member.
"""
from __future__ import annotations


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

        Drives the ``scope_trusted`` auto-admit decision. A room with an empty
        ``scope_key`` (no scope) has no trusted members by definition unless a
        provider says otherwise.
        """
        raise NotImplementedError


class DefaultScopeProvider(ScopeProvider):
    """Single global scope: every room gets ``scope_key=""``, no query is
    filtered, and every authenticated user counts as a member (single-tenant
    hosts and tests). Swap for a workspace-aware provider in production."""

    def resolve(self, request) -> str:
        return ""

    def filter(self, queryset, request):
        return queryset

    def is_member(self, request, scope_key: str) -> bool:
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False))


def get_scope_provider() -> ScopeProvider:
    """Resolve the configured provider (already import_string'd by conf)."""
    from .conf import video_settings

    provider = video_settings.SCOPE_PROVIDER
    return provider() if isinstance(provider, type) else provider
