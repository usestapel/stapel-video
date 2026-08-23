"""v1 URL set for stapel-video (api-versioning.md §2, §6).

No global prefix here — the root ``urls.py`` mounts this module under
``api/v1/`` and the host mounts that under ``video/``:

    path("video/", include("stapel_video.urls"))   # -> /video/api/v1/...
"""
from typing import NamedTuple

from django.urls import path

from .views import (
    LobbyAdmitView,
    LobbyDenyView,
    RoomDetailView,
    RoomJoinView,
    RoomListCreateView,
    RoomParticipantsView,
    ScopeUsageView,
    WebhookIngressView,
)

urlpatterns = [
    path("rooms", RoomListCreateView.as_view(), name="video-rooms"),
    path(
        "rooms/<str:join_code>",
        RoomDetailView.as_view(),
        name="video-room-detail",
    ),
    path(
        "rooms/<str:join_code>/join",
        RoomJoinView.as_view(),
        name="video-room-join",
    ),
    path(
        "rooms/<str:join_code>/participants",
        RoomParticipantsView.as_view(),
        name="video-room-participants",
    ),
    path(
        "rooms/<str:join_code>/lobby/admit",
        LobbyAdmitView.as_view(),
        name="video-lobby-admit",
    ),
    path(
        "rooms/<str:join_code>/lobby/deny",
        LobbyDenyView.as_view(),
        name="video-lobby-deny",
    ),
    # The per-partition usage read. Trailing slash on purpose: the segment
    # after it is a report NAME, and the next report this scope grows
    # (`.../usage/export`) has to be able to sit next to it without the
    # router reading it as part of an opaque key.
    path(
        "scopes/<str:scope_key>/usage/",
        ScopeUsageView.as_view(),
        name="video-scope-usage",
    ),
    path("webhook", WebhookIngressView.as_view(), name="video-webhook"),
]


class GateEntry(NamedTuple):
    """One gated URL block: which flags gate which url patterns (capability-config.md §2 p.2).

    ``flags`` compose with OR — the block is mounted while ANY flag is on, and
    disappears only when ALL of them are off. Empty flags = always on.
    """

    name: str
    flags: tuple
    patterns: tuple


#: Gate registry (capability-config.md §2 p.2): stapel-video's axes are
#: behavioral (which provider carries calls, the default access level / lobby
#: switch) — none unmounts an endpoint — so the whole URL surface is a single
#: always-on block. Declared as a registry entry (rather than left implicit) so
#: the capabilities.json emitter has a uniform mechanism across every module.
GATE_REGISTRY: dict = {
    "video.api": GateEntry("video.api", (), tuple(urlpatterns)),
}
