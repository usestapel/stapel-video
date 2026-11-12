"""ICE servers handed to the browser next to every media token.

The media server announces its own ICE configuration in its signalling
handshake; a deployment that runs no TURN there, or whose browsers sit behind
a NAT the media server's own STUN cannot see past, needs servers the client
adds itself. ``ice_servers`` on every grant (room join, call create/accept,
call re-mint) is that list, shaped exactly like ``RTCIceServer`` so a client
passes it to ``rtcConfig.iceServers`` unchanged.

Two sources, both empty by default (no third-party host is a default):

- ``RTC_STUN_SERVERS`` — STUN URLs, e.g. ``["stun:stun.example.com:3478"]``.
- ``RTC_TURN_SERVERS`` + ``RTC_TURN_SECRET`` — TURN URLs and the shared
  secret of a TURN server speaking the time-limited credential scheme
  (coturn ``use-auth-secret``). Each grant carries its own credential:
  ``username = "<expiry>:<user id>"``, ``credential =
  base64(HMAC-SHA1(secret, username))``, valid for
  ``RTC_TURN_CREDENTIAL_TTL_SECONDS``. The secret never leaves the server.

TURN URLs without a secret are not served: a TURN entry with no credential is
refused by every browser, and a list containing one is refused whole.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import List

from .conf import video_settings
from .dto import IceServer


def _urls(value) -> List[str]:
    """A list setting that may arrive from the environment as one string."""
    if not value:
        return []
    if isinstance(value, str):
        value = value.replace(",", " ").split()
    return [str(u).strip() for u in value if str(u).strip()]


def stun_urls() -> List[str]:
    return _urls(video_settings.RTC_STUN_SERVERS)


def turn_urls() -> List[str]:
    return _urls(video_settings.RTC_TURN_SERVERS)


def turn_credential(secret: str, user_id, *, ttl: int, now: float | None = None):
    """``(username, credential)`` for the time-limited TURN scheme."""
    expiry = int((time.time() if now is None else now) + ttl)
    username = f"{expiry}:{user_id}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


def ice_servers_for(user_id) -> List[IceServer]:
    """The ``RTCIceServer`` list for one grant to *user_id*; ``[]`` when none."""
    servers: List[IceServer] = []
    stun = stun_urls()
    if stun:
        servers.append(IceServer(urls=stun))
    turn = turn_urls()
    secret = video_settings.RTC_TURN_SECRET or ""
    if turn and secret:
        username, credential = turn_credential(
            secret, user_id, ttl=int(video_settings.RTC_TURN_CREDENTIAL_TTL_SECONDS)
        )
        servers.append(IceServer(urls=turn, username=username, credential=credential))
    return servers

