"""URL patterns — no global prefix here, the host project mounts them:

    path("video/", include("stapel_video.urls"))
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
    WebhookIngressView,
)

urlpatterns = [
    path("api/rooms", RoomListCreateView.as_view(), name="video-rooms"),
    path(
        "api/rooms/<str:join_code>",
        RoomDetailView.as_view(),
        name="video-room-detail",
    ),
    path(
        "api/rooms/<str:join_code>/join",
        RoomJoinView.as_view(),
        name="video-room-join",
    ),
    path(
        "api/rooms/<str:join_code>/participants",
        RoomParticipantsView.as_view(),
        name="video-room-participants",
    ),
    path(
        "api/rooms/<str:join_code>/lobby/admit",
        LobbyAdmitView.as_view(),
        name="video-lobby-admit",
    ),
    path(
        "api/rooms/<str:join_code>/lobby/deny",
        LobbyDenyView.as_view(),
        name="video-lobby-deny",
    ),
    path("api/webhook", WebhookIngressView.as_view(), name="video-webhook"),
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
