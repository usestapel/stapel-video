"""Who may read one partition's usage.

The question this module answers is narrow and the reason it is not answered
with ``IsStaff`` is the whole design. The audience for
``GET /video/api/v1/scopes/{scope_key}/usage/`` is a *workspace's own
owner or admin*, looking at their own people's minutes in their own
administration screen. A staff gate would mean the only accounts able to see a
customer's numbers belong to the vendor — which is not an authorization model,
it is a support ticket.

Two questions, asked in order, and they are genuinely different:

1. **Is the caller a principal at all?** That is
   ``stapel_core.django.api.permissions.HasWorkspaceMandateIfScoped``, on the
   view — the same gate stapel-calendar 0.5.0 put on its by-id reads, for the
   same reason (an unfiltered by-id read is reachable by anyone the gate
   admits). It refuses anonymous everywhere, refuses the guest state where
   that state exists, admits everyone in a genuinely standalone deployment,
   and turns "could not ask" into 503 rather than into a verdict.
2. **May this principal read THIS partition?** That is :func:`may_read_scope`
   below, and nothing in core can answer it: it is per-workspace authority,
   so it goes to the workspaces access registry
   (``workspaces.check_capability`` via
   ``stapel_core.django.workspaces.require_capability``) with the capability
   named by ``STAPEL_VIDEO["USAGE_MANDATE"]``.

Step 1 without step 2 is the hole this module exists to close: a mandated
member of workspace A holds a mandate *somewhere*, so the coarse gate admits
them, and a URL is a string they can type. The registry check is what makes
``scope_key`` have to be a workspace they actually hold the capability in.

**The refusal is 404, not 403**, and uniformly so — the same answer for a
partition that does not exist, a partition with no calls in it, and a
partition that belongs to somebody else. 403 would confirm the key: an
attacker enumerating workspace ids would read "forbidden" as "exists" and walk
away with a customer list. Existence is itself the secret here, because the
key is the host's own tenant id.

A deployment that cannot ask about mandates at all has no registry to consult,
so :data:`USAGE_AUTHORIZER` answers instead — staff-only by default, because
"nothing here knows who owns this partition" degrades to the operator, not to
everyone.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def staff_only_authorizer(request, scope_key: str) -> bool:
    """Default ``USAGE_AUTHORIZER``: staff and superusers, nobody else.

    Reached only in a standalone deployment (no workspaces installed, no
    ``workspaces.check_mandate`` routed). There is no membership to consult
    there, so the choice is between the operator and everyone; a library that
    picked "everyone" would be handing every authenticated account the whole
    instance's per-person call minutes on the strength of a missing setting.

    A single-tenant host that wants its own admins in points
    ``STAPEL_VIDEO["USAGE_AUTHORIZER"]`` at its own callable.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def may_read_scope(request, scope_key: str) -> bool:
    """May ``request``'s caller read the usage of ``scope_key``?

    In a workspace-bearing deployment: does the caller hold
    ``STAPEL_VIDEO["USAGE_MANDATE"]`` **in the workspace whose id is
    ``scope_key``**? The scope key IS the workspace id for such a host — that
    is what makes "a member of A cannot read B" a check rather than a hope.
    Nothing here interprets the key beyond passing it to the registry, so a
    host whose partitions are not workspaces at all simply never grants the
    capability and falls back to its own authorizer.

    Failure is closed. ``require_capability`` answers ``None`` for a denial,
    for a workspace that does not exist, and for a remote call that failed —
    it does not distinguish them, and neither does the caller, because all
    three render as the same 404.
    """
    from stapel_core.django.scope import deployment_is_standalone

    from .conf import video_settings

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    scope_key = str(scope_key or "").strip()
    if not scope_key:
        return False

    if deployment_is_standalone():
        # No registry exists to hold a capability, so the host's own callable
        # is the only authority. Resolved through conf (import_strings), so a
        # broken dotted path is stapel_video.E011 at boot, not a 500 here.
        return bool(video_settings.USAGE_AUTHORIZER(request, scope_key))

    from stapel_core.django.workspaces import require_capability

    membership = require_capability(
        scope_key, getattr(user, "pk", None), video_settings.USAGE_MANDATE
    )
    return membership is not None


__all__ = ["may_read_scope", "staff_only_authorizer"]
