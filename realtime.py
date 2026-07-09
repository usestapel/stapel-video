"""Realtime lobby fan-out — a thin, optional wrapper over Channels.

Lobby transitions (a guest starts waiting; a host admits/denies) are pushed to
the room's Channels group so the ``LobbyConsumer`` can relay them to connected
browsers. Channels is an OPTIONAL extra: with no channel layer configured
(HTTP-only hosts, most tests) these helpers are silent no-ops — the REST
operations still work, clients just poll instead of getting a live push.
"""
from __future__ import annotations


def lobby_group(join_code: str) -> str:
    """Channels group name for a room's lobby."""
    return f"video_lobby_{join_code}"


def notify_lobby(join_code: str, message: dict) -> None:
    """Fan a lobby event out to the room group. No-op without Channels."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
    except ImportError:
        return
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(lobby_group(join_code), message)
