"""comm surface of stapel-video's presence meter.

Five read Functions over :class:`~stapel_video.models.ParticipantSpan`. They
are the instance's whole reporting surface: this deployment meters and hands
the numbers over; whoever bills reads them. Nothing here applies a threshold,
a plan or a price — the "counts as a real conversation" line is revised per
customer in the external service, and an export that had already dropped the
short overlaps could not answer the revised question.

- ``video.presence.aggregate`` — unioned presence seconds for one person or
  one room over a period.
- ``video.presence.spans_export`` — the raw spans, cursor-paged.
- ``video.presence.pairs_export`` — the co-presence matrix as rows, cursor-paged.
- ``video.presence.usage_rollup`` — one PARTITION's window, one row per person.
- ``video.presence.usage_rollup_by_month`` — the same, cut into calendar
  months in a caller-named time zone: the table a workspace-administration
  screen draws.

The last two are why ``scope_key`` exists on the span. The first three answer
about a person, a room, or the whole instance, and there was no way to ask
about a *tenant* — which is the question a workspace admin has, and the one a
host would otherwise answer by joining the meter to its own room table and
re-implementing the union arithmetic beside it.

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
USAGE_ROLLUP = "video.presence.usage_rollup"
USAGE_ROLLUP_BY_MONTH = "video.presence.usage_rollup_by_month"


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
    Output: ``{"rows": [{"span_id", "room_key", "scope_key", "user_id",
    "connection_id", "joined_at", "left_at", "close_reason",
    "duration_seconds", "seq"}], "cursor": str|null, "total": int|null}``.

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


@function(USAGE_ROLLUP, schema=_schema(USAGE_ROLLUP))
def usage_rollup(payload: dict) -> dict:
    """One partition's window, one row per person.

    Input: ``{"scope_key", "period": "YYYY-MM"?, "tz"?, "period_start"?,
    "period_end"?, "group_by"?}``.
    Output: ``{"scope_key", "period_start", "period_end", "rows":
    [{"user_id", "presence_seconds", "rooms", "connections", "first_seen",
    "last_seen"}]}``.

    The same union arithmetic as :func:`presence_aggregate`, cut by scope and
    reported per person instead of totalled — one code path, because two
    implementations of "how long was this person present" is two numbers, and
    the one on the admin screen would be the one nobody reconciles against the
    invoice. ``rooms`` counts distinct rooms, not spans.

    ``rows``, never ``items``: core's snapshot reader looks for that exact key.
    """
    from . import presence

    scope_key = str(payload.get("scope_key") or "")
    if payload.get("period"):
        start, end = presence.month_bounds(payload["period"], payload.get("tz"))
    else:
        start, end = presence._resolve_period(payload)
    return {
        "scope_key": scope_key,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "rows": presence.usage_rollup(
            scope_key=scope_key,
            period_start=start,
            period_end=end,
            group_by=payload.get("group_by") or "user",
        ),
    }


@function(USAGE_ROLLUP_BY_MONTH, schema=_schema(USAGE_ROLLUP_BY_MONTH))
def usage_rollup_by_month(payload: dict) -> dict:
    """The month-by-month table, newest month first.

    Input: ``{"scope_key", "months": int?, "tz": str?}``.
    Output: ``{"scope_key", "tz", "months": [{"month", "period_start",
    "period_end", "users": [...]}]}``.

    Buckets are calendar months in ``tz`` — local midnights, so a DST
    transition makes exactly one of them 23 or 25 hours short of its naive
    length. A month with no calls is present with ``users: []``: "no calls"
    and "this row failed to load" must not look the same.
    """
    from . import presence

    tz = payload.get("tz") or "UTC"
    return {
        "scope_key": str(payload.get("scope_key") or ""),
        "tz": tz,
        "months": presence.usage_rollup_by_month(
            scope_key=str(payload.get("scope_key") or ""),
            months=payload.get("months") or presence.ROLLUP_DEFAULT_MONTHS,
            tz=tz,
        ),
    }


__all__ = [
    "AGGREGATE",
    "PAIRS_EXPORT",
    "SPANS_EXPORT",
    "USAGE_ROLLUP",
    "USAGE_ROLLUP_BY_MONTH",
]
