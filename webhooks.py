"""Provider-webhook dispatch — a merge registry keyed by event type.

Until 0.6.0 the ingress did one thing: ``if _is_egress_ended(parsed): emit``.
Every other event the media server sends — a participant joining, a
participant leaving, a room finishing — arrived at the URL, passed the
signature check, and was dropped. Adding a second reaction meant editing this
library or terminating the webhook in product code and re-implementing
verification there, which is the same fork by two routes.

So the dispatch is a registry, in the fleet's standard merge shape
(library-standard §3.3; the form of ``stapel_docs.doc_types`` /
``stapel_search.registry``), three layers of increasing precedence:

1. :data:`BUILTIN_WEBHOOK_HANDLERS` — what this package handles out of the box;
2. ``STAPEL_VIDEO["WEBHOOK_HANDLERS"]`` — a host overlay,
   ``{event: dotted-path | None}``, merged OVER the builtins, so adding
   ``room_finished`` never means restating the four that ship, and ``None``
   tombstones one that does;
3. :func:`register_webhook_handler` — runtime registration, for an app-layer
   package wiring itself from its own ``AppConfig.ready()``.

A handler is called with the normalized event dict
(``VideoProvider.parse_webhook``) and returns nothing. It runs inside the
ingress request, so it owns its own idempotency: delivery is at-least-once
and events arrive out of order.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Event type -> dotted path. The two egress entries are the 0.5.x behaviour,
#: unchanged: LiveKit emits a dedicated ``egress_ended``, while some
#: deployments only send ``egress_updated`` carrying a terminal status, and
#: the handler filters on that.
BUILTIN_WEBHOOK_HANDLERS: dict[str, str] = {
    "egress_ended": "stapel_video.webhooks.handle_egress_ended",
    "egress_updated": "stapel_video.webhooks.handle_egress_ended",
    "participant_joined": "stapel_video.presence.handle_participant_joined",
    "participant_left": "stapel_video.presence.handle_participant_left",
}

# event -> dotted path | callable | None (None masks the event).
_runtime_handlers: dict[str, object] = {}


def register_webhook_handler(event: str, handler) -> None:
    """Register (or mask) a handler for one provider event type at runtime.

    ``handler`` is a callable taking the normalized event dict, or a dotted
    path to one; ``None`` masks the event so nothing runs for it. Highest
    precedence — meant for an app-layer ``AppConfig.ready()``.
    """
    if handler is None or handler == "":
        _runtime_handlers[event] = None
        return
    if isinstance(handler, str) or callable(handler):
        _runtime_handlers[event] = handler
        return
    raise TypeError(
        f"register_webhook_handler({event!r}) expects a callable or a dotted "
        f"path string, got {handler!r}"
    )


def unregister_webhook_handler(event: str) -> None:
    """Drop a runtime registration (tests)."""
    _runtime_handlers.pop(event, None)


def get_webhook_handlers() -> dict:
    """The effective ``event -> callable`` map: builtins <- settings <- runtime.

    Resolved per entry, not through ``import_strings``: one unimportable
    overlay path must not take the other handlers down with it, and a
    ``None`` has to survive to the merge to tombstone a builtin. A broken
    entry is logged and skipped here and reported at boot by
    ``stapel_video.E010`` — the check is where an operator finds out, the
    log is what keeps a live webhook from 500-ing over a typo.
    """
    from .conf import video_settings

    merged: dict[str, object] = dict(BUILTIN_WEBHOOK_HANDLERS)
    for layer in (video_settings.WEBHOOK_HANDLERS or {}, _runtime_handlers):
        for event, target in layer.items():
            if target is None or target == "":
                merged.pop(event, None)
            else:
                merged[event] = target

    resolved = {}
    for event, target in merged.items():
        try:
            resolved[event] = _resolve(event, target)
        except Exception as exc:
            logger.error(
                "STAPEL_VIDEO['WEBHOOK_HANDLERS'][%r] -> %r is not usable: %s",
                event,
                target,
                exc,
            )
    return resolved


def get_webhook_handler(event: str):
    """The handler for one event type, or None when nothing handles it.

    "Nothing handles it" is the ordinary answer for most of what a media
    server sends (track_published, room_started, …) and is not an error: the
    ingress still answers 200, because a provider that gets a 4xx retries the
    event it already delivered correctly.
    """
    return get_webhook_handlers().get(event or "")


def _resolve(event: str, target):
    if callable(target):
        return target
    from django.utils.module_loading import import_string

    handler = import_string(target)
    if not callable(handler):
        raise TypeError(f"{target!r} is not callable")
    return handler


# ── Built-in handlers ──────────────────────────────────────────────────────

#: Terminal egress statuses some deployments report through
#: ``egress_updated`` instead of a dedicated ``egress_ended``.
TERMINAL_EGRESS_STATUSES = ("EGRESS_COMPLETE", "EGRESS_ABORTED", "EGRESS_FAILED")


def handle_egress_ended(parsed: dict) -> None:
    """A recording finished: emit ``video.egress_ended`` for the recordings
    side to finalize the upload the egress wrote.

    Byte-for-byte the 0.5.x behaviour, moved out of ``services.handle_webhook``
    into the registry — including the ``egress_updated`` + terminal-status
    arm, which is how the event arrives on deployments that never send the
    dedicated one.
    """
    from django.db import transaction
    from stapel_core.comm import emit

    if not _is_egress_ended(parsed):
        return
    with transaction.atomic():
        emit(
            "video.egress_ended",
            {
                "egress_id": parsed.get("egress_id"),
                "status": parsed.get("status"),
                "storage_key": parsed.get("storage_key"),
            },
            key=str(parsed.get("egress_id") or ""),
        )


def _is_egress_ended(parsed: dict) -> bool:
    event = (parsed.get("event") or "").lower()
    status = (parsed.get("status") or "").upper()
    if event == "egress_ended":
        return True
    return event == "egress_updated" and status in TERMINAL_EGRESS_STATUSES


__all__ = [
    "BUILTIN_WEBHOOK_HANDLERS",
    "TERMINAL_EGRESS_STATUSES",
    "get_webhook_handler",
    "get_webhook_handlers",
    "handle_egress_ended",
    "register_webhook_handler",
    "unregister_webhook_handler",
]
