"""Test scope provider: membership by username allow-list, for exercising the
scope_trusted auto-admit path without a real workspace backend."""
from stapel_video.scope import ScopeProvider

MEMBERS = {"alice", "carol"}


class UsernameScopeProvider(ScopeProvider):
    def resolve(self, request) -> str:
        return "scope-1"

    def filter(self, queryset, request):
        return queryset

    def is_member(self, request, scope_key: str) -> bool:
        user = getattr(request, "user", None)
        return bool(user) and getattr(user, "username", None) in MEMBERS
