"""Where the BROWSER connects — resolved per REQUEST, not per process.

``LIVEKIT_CLIENT_URL`` was one global string, and one global string is wrong
the moment a single image serves two brand hosts. A fleet that answers
``primary.example`` and ``secondary.example`` from the same containers handed
*every* browser ``wss://primary.example/rtc``: a page on the second brand opened
a socket across the boundary, where its cookies are not sent, its CSP does not
allow the origin and the TLS name does not match. The call minted a valid
token, named a real room, and never connected — the same silent shape
``stapel_video.W007`` was written for, one deployment topology further on.

So the value is a *policy*, read against the request that asked, in three
forms:

(a) **an absolute URL** — ``wss://media.example.com`` — one media address for
    every host this process answers. What every deployment had before, and
    still the right answer for a single-brand fleet.
(b) **a path** — ``/rtc`` — "the request's own host", scheme ``wss`` when the
    request arrived over TLS and ``ws`` when it did not. The answer for a
    fleet that proxies the media server under each brand's own domain: it is
    correct for hosts nobody has enumerated yet, including the one added
    tomorrow.
(c) **a mapping** — ``{"primary.example": "wss://primary.example/rtc",
    "secondary.example": "...", "default": "..."}`` — when the brands
    genuinely have different media addresses. ``default`` answers a host that is not listed; without one, an
    unlisted host is answered ``""`` rather than another brand's address,
    because handing a browser the wrong brand is the defect this module
    exists to close and silence is the lesser failure.

A mapping's values may themselves be paths, so ``{"default": "/rtc"}`` is
"whatever host asked" with room for one brand to be pinned elsewhere later.

**No request, no host.** A mint with no request in hand (a management
command, a background re-mint) can answer form (a) and a mapping's
``default``, and answers ``""`` for a path — there is no host to build one
from, and inventing one would produce exactly the cross-brand address this
resolver exists to prevent.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Key answering a host the mapping does not name.
DEFAULT_KEY = "default"

__all__ = ["DEFAULT_KEY", "request_host", "resolve_client_url"]


def request_host(request) -> str:
    """The host the request named, lowercased and without its port.

    ``""`` for a request that has no host to read — no request at all, a
    request object that does not implement ``get_host`` (a bare mock in a
    host's own tests), or a ``Host:`` header Django refuses under
    ``ALLOWED_HOSTS``. All three are the same fact here: *this resolver was
    not told which brand asked*, and the caller falls back rather than
    guessing.
    """
    if request is None:
        return ""
    try:
        host = request.get_host()
    except Exception:
        # DisallowedHost, a missing SERVER_NAME, a request-shaped object with
        # no such method. A host that cannot be validated is not a host we
        # will echo into a URL a browser is told to trust.
        return ""
    host = str(host or "").strip().lower()
    if not host:
        return ""
    # Strip the port: the map is keyed by brand, and :443 is not a brand.
    # IPv6 literals keep their brackets and their colons.
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.split(":", 1)[0]


def _is_absolute(value: str) -> bool:
    return "://" in value


def _scheme_for(request) -> str:
    """``wss`` for a secure request, ``ws`` for a plain one.

    Defaults to ``wss`` when the request cannot say: a fleet is served over
    TLS, and the failure of guessing wrong in that direction is a blocked
    mixed-content connection with a clear browser error, rather than a
    silently downgraded one.
    """
    try:
        return "wss" if request.is_secure() else "ws"
    except Exception:
        return "wss"


def resolve_client_url(configured, request=None) -> str:
    """The browser-facing media address for *this* request.

    Args:
        configured: the ``LIVEKIT_CLIENT_URL`` value — an absolute URL, a
            path, or a ``{host: url}`` mapping (see the module docstring).
        request: the HTTP request being answered, when there is one.

    Returns:
        The URL to hand this browser, or ``""`` when the configuration has
        nothing to say about this host. Never raises: a call with a good
        token and no URL is a client that can be told where to dial by other
        means, while an exception here would fail a call over a cosmetic.
    """
    if isinstance(configured, dict):
        return _resolve_mapping(configured, request)
    value = str(configured or "").strip()
    if not value:
        return ""
    if _is_absolute(value):
        return value
    if value.startswith("/"):
        host = request_host(request)
        if not host:
            return ""
        return f"{_scheme_for(request)}://{host}{value}"
    # Neither absolute nor a path — a bare "media.example.com", say. Passed
    # through untouched: guessing a scheme for it would be inventing the
    # half of the address that decides whether the connection is encrypted.
    return value


def _resolve_mapping(mapping: dict, request) -> str:
    host = request_host(request)
    value = mapping.get(host) if host else None
    if value is None:
        value = mapping.get(DEFAULT_KEY)
    if value is None:
        logger.warning(
            "video: LIVEKIT_CLIENT_URL names no entry for host %r and no %r, "
            "so this browser is told nothing rather than another brand's "
            "media address",
            host,
            DEFAULT_KEY,
        )
        return ""
    # A mapping's value may itself be a path, and resolving it recursively is
    # what makes {"default": "/rtc"} mean "each brand's own host" with room
    # for one brand to be pinned elsewhere.
    return resolve_client_url(value, request)
