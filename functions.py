"""comm surface of stapel-video's presence meter.

Three read Functions over :class:`~stapel_video.models.ParticipantSpan`. They
are the instance's whole reporting surface: this deployment meters and hands
the numbers over; whoever bills reads them. Nothing here applies a threshold,
a plan or a price — the "counts as a real conversation" line is revised per
customer in the external service, and an export that had already dropped the
short overlaps could not answer the revised question.

- ``video.presence.aggregate`` — unioned presence seconds for one person or
  one room over a period.
- ``video.presence.spans_export`` — the raw spans, cursor-paged.
- ``video.presence.pairs_export`` — the co-presence matrix as rows, cursor-paged.

Both exports answer ``{"rows", "cursor", "total"}`` and never ``{"items"}``:
core's snapshot reader (``stapel_core.comm.projections._iter_snapshot``) looks
for ``rows`` by name, so an items-shaped answer rebuilds a consumer's
projection to EMPTY and reports success doing it.

A comm call carries no session. These Functions are as authoritative as the
transport that reaches them — a host exposing them over HTTP puts them behind
its own service credential, exactly as it would any other reporting endpoint.
"""
import json
from pathlib import Path

from stapel_core.comm import function

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas" / "functions"


def _schema(name: str) -> dict:
    return json.loads((_SCHEMAS_DIR / f"{name}.json").read_text(encoding="utf-8"))


AGGREGATE = "video.presence.aggregate"
SPANS_EXPORT = "video.presence.spans_export"
PAIRS_EXPORT = "video.presence.pairs_export"


@function(AGGREGATE, schema=_schema(AGGREGATE))
def presence_aggregate(payload: dict) -> dict:
    """Unioned presence for one scope over a period.

    Input: ``{"user_id"|"room_key", "period": "YYYY-MM"}`` (or an explicit
    ``period_start``/``period_end`` ISO pair).
    Output: ``{"user_id", "room_key", "period_start", "period_end",
    "presence_seconds", "rooms_count", "users_count", "spans_count"}``.

    Seconds are UNIONED, not summed: one person on a laptop and a phone was
    present once. For a room scope the total is person-seconds inside that
    room (each attendee's own merged timeline), not the room's wall clock.
    """
    from . import presence

    user_id = str(payload.get("user_id") or "")
    room_key = str(payload.get("room_key") or "")
    if bool(user_id) == bool(room_key):
        raise ValueError(
            f"{AGGREGATE} takes exactly one scope: user_id OR room_key "
            "(both would silently answer a third question)"
        )
    start, end = presence._resolve_period(payload)
    return presence.presence_aggregate(
        user_id=user_id, room_key=room_key, period_start=start, period_end=end
    )


@function(SPANS_EXPORT, schema=_schema(SPANS_EXPORT))
def spans_export(payload: dict) -> dict:
    """Cursor-paged snapshot of raw presence spans.

    Input: ``{"cursor": str|null, "limit": int?, "period"|"period_start"/
    "period_end"?}``.
    Output: ``{"rows": [{"span_id", "room_key", "user_id", "connection_id",
    "joined_at", "left_at", "close_reason", "duration_seconds", "seq"}],
    "cursor": str|null, "total": int|null}``.

    Keyset paging over ``(joined_at, id)``, so a full walk visits every row
    exactly once even while the sweeper closes spans underneath it. ``total``
    is reported on the first page only.
    """
    from . import presence

    start = end = None
    if payload.get("period") or payload.get("period_start") or payload.get("period_end"):
        start, end = presence._resolve_period(payload)
    return presence.spans_export(
        cursor=payload.get("cursor"),
        limit=payload.get("limit") or presence.EXPORT_DEFAULT_LIMIT,
        period_start=start,
        period_end=end,
    )


@function(PAIRS_EXPORT, schema=_schema(PAIRS_EXPORT))
def pairs_export(payload: dict) -> dict:
    """Cursor-paged co-presence matrix for a period.

    Input: ``{"period": "YYYY-MM" (or period_start/period_end), "cursor":
    str|null, "limit": int?}``.
    Output: ``{"rows": [{"room_key", "user_a", "user_b",
    "co_presence_seconds"}], "cursor": str|null, "total": null}``.

    One row per unordered pair per room, with ``user_a < user_b``, carrying
    the RAW overlap in seconds. ``total`` is always null — counting the pairs
    costs the same as computing them, and the canon reads null as "the owner
    does not report it".
    """
    from . import presence

    start, end = presence._resolve_period(payload)
    return presence.pairs_export(
        period_start=start,
        period_end=end,
        cursor=payload.get("cursor"),
        limit=payload.get("limit") or presence.EXPORT_DEFAULT_LIMIT,
    )


__all__ = ["AGGREGATE", "PAIRS_EXPORT", "SPANS_EXPORT"]
