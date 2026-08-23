"""Presence metering — spans in, unioned time and co-presence out.

The one fact this module records is a :class:`~stapel_video.models.ParticipantSpan`:
one connection's stay in one room, ``[joined_at, left_at)``. Everything a
report wants is derived from it — a person's minutes in a period are the UNION
of their spans, and "who did they actually talk to" is the overlap of two
people's spans in the same room. Nothing here prices anything: this instance
meters and exports, and the thresholds ("more than 15 minutes counts") live in
whatever consumes the export, so a rate change is never a migration here.

Three writers, one table, in descending order of trust:

1. **The media server's webhooks** (``participant_joined`` /
   ``participant_left``, dispatched through :mod:`stapel_video.webhooks`).
   The SFU detects a vanished peer itself, which is the whole reason the meter
   is built on this and not on a browser's leave beacon: a closed laptop, a
   killed tab, a dead network and a crash all send nothing, and all of them
   are exactly when a naive meter bills forever.
2. **The sweeper** (:func:`sweep_open_spans`), reconciling open spans against
   the roster the provider reports. A webhook stream is at-least-once, which
   also means at-most-eventually: one dropped ``participant_left`` is an
   unbounded span, and only a second, independent reading closes it.
3. **The host**, explicitly (:func:`close_spans_explicitly`) — a leave button,
   a kick. A UX signal, not a source of truth; it only ever closes a span the
   media server has not closed yet.

Ordering and duplicates are the normal case, not the exception. Both ingest
paths key the span on ``(connection_id, joined_at)`` — the provider's own
join timestamp, never our receipt time — so a redelivery is a no-op and a
``participant_left`` that overtakes its ``participant_joined`` materializes
the whole closed span by itself. Closed spans are never mutated afterwards.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone

from django.db import transaction

from .models import ParticipantSpan, SpanCloseReason

logger = logging.getLogger(__name__)

#: Rows per export page when the caller does not ask, and the ceiling it is
#: clamped to. Same numbers as the rest of the shelf's snapshot exports.
EXPORT_DEFAULT_LIMIT = 500
EXPORT_MAX_LIMIT = 2000

#: Months a single ``usage_rollup_by_month`` call may cover, and the ceiling.
#: The read walks every span in every bucket, so an unbounded ``months`` is an
#: unbounded scan reachable from a URL query string.
ROLLUP_DEFAULT_MONTHS = 6
ROLLUP_MAX_MONTHS = 36


class InvalidExportCursor(Exception):
    """The opaque cursor handed to an export was not one we issued."""


class InvalidPeriod(Exception):
    """A period could not be read as a UTC calendar month or an explicit
    ``[start, end)`` pair."""


class InvalidTimezone(Exception):
    """A ``tz`` argument named no zone this deployment's tz database knows."""


# ── Webhook handlers (registered in webhooks.BUILTIN_WEBHOOK_HANDLERS) ─────


def handle_participant_joined(parsed: dict) -> None:
    """A connection arrived: open its span.

    The join timestamp is the provider's ``participant.joined_at`` and only
    falls back to the event stamp — never to ``now()``. Webhooks are queued
    and retried, so receipt time measures our delivery path, not anyone's
    presence, and a retry storm would otherwise re-date every arrival.
    """
    room_key, participant = _room_and_participant(parsed, "participant_joined")
    if participant is None:
        return
    joined_at = participant.get("joined_at") or parsed.get("event_ts")
    if joined_at is None:
        logger.warning(
            "participant_joined for %s carried no timestamp — cannot open a "
            "span keyed on a moment that was never reported",
            participant.get("identity"),
        )
        return
    open_span(
        room_key=room_key,
        user_id=participant["user_id"],
        connection_id=participant["connection_id"],
        joined_at=joined_at,
        scope_key=participant.get("scope_key"),
    )


def handle_participant_left(parsed: dict) -> None:
    """A connection ended: close its span, or record the whole stay at once.

    Two arms, and the second is not a fallback — it is the ordinary path
    whenever the pair arrives out of order or the ``participant_joined`` was
    lost. The left event carries ``participant.joined_at``, so the complete
    ``[joined_at, left_at)`` is reconstructable from it alone, and the
    ``(connection_id, joined_at)`` key makes the late-arriving twin a no-op
    instead of a second stay.
    """
    room_key, participant = _room_and_participant(parsed, "participant_left")
    if participant is None:
        return
    left_at = parsed.get("event_ts") or _now()
    span = (
        ParticipantSpan.objects.filter(
            room_key=room_key,
            connection_id=participant["connection_id"],
            left_at__isnull=True,
        )
        .order_by("-joined_at")
        .first()
    )
    if span is not None:
        close_span(span, at=left_at, reason=SpanCloseReason.WEBHOOK)
        return
    joined_at = participant.get("joined_at")
    if joined_at is None:
        # Nothing open and nothing to reconstruct from. Recording a stay of
        # unknown length would be inventing a number, so the honest outcome
        # is a log line and no row.
        logger.warning(
            "participant_left for %s: no open span and the event carried no "
            "joined_at — this stay is not recoverable",
            participant.get("identity"),
        )
        return
    open_span(
        room_key=room_key,
        user_id=participant["user_id"],
        connection_id=participant["connection_id"],
        joined_at=joined_at,
        closed_at=left_at,
        close_reason=SpanCloseReason.WEBHOOK,
        scope_key=participant.get("scope_key"),
    )


def _room_and_participant(parsed: dict, event: str):
    room = parsed.get("room") or {}
    participant = parsed.get("participant") or None
    if not participant or not participant.get("connection_id"):
        logger.warning("%s webhook carried no identifiable participant", event)
        return "", None
    room_key = str(room.get("name") or "")
    if not room_key:
        logger.warning(
            "%s webhook carried no room name — a span with no room cannot be "
            "attributed to a call",
            event,
        )
        return "", None
    return room_key, participant


# ── Writing spans ──────────────────────────────────────────────────────────


def normalize_scope_key(scope_key) -> str | None:
    """The value a span's ``scope_key`` column may actually hold.

    ``None`` for every falsy input, including ``""``. The column is nullable
    precisely so that "this host partitions nothing" and "this stay belongs to
    the tenant whose id is the empty string" stay different facts: the usage
    read groups by the column, and an empty-string scope would be a tenant the
    report invented. One funnel, so every writer — webhook, sweeper, backfill,
    host — agrees.
    """
    if scope_key is None:
        return None
    text = str(scope_key).strip()
    return text or None


def open_span(
    *,
    room_key: str,
    user_id: str,
    connection_id: str,
    joined_at: datetime,
    closed_at: datetime | None = None,
    close_reason: str = "",
    scope_key: str | None = None,
):
    """Record a connection's stay. Returns ``(span, created)``.

    Idempotent on ``(connection_id, joined_at)`` — the unique constraint IS
    the deduplication, so two deliveries of the same arrival race into one
    row and the loser reads it back. ``closed_at`` writes an already-finished
    span in one go (the out-of-order ``participant_left`` path).

    ``scope_key`` is the reporting partition, echoed off the join grant by the
    provider (:data:`stapel_video.providers.base.METADATA_SCOPE_KEY`). It is
    stamped only on a row this call CREATES: the span is append-only, and a
    redelivery carrying a different scope must not silently move a recorded
    stay from one tenant's invoice to another's. A host that changed the
    partition of a room fixes history with the backfill command, deliberately.

    Emits ``video.participant.joined`` only for a row this call created, in
    the same transaction as the write: the fact and the state it describes
    commit together or not at all.
    """
    from stapel_core.comm import emit

    joined_at = _aware(joined_at)
    if closed_at is not None:
        closed_at = max(_aware(closed_at), joined_at)
    with transaction.atomic():
        span, created = ParticipantSpan.objects.get_or_create(
            connection_id=str(connection_id),
            joined_at=joined_at,
            defaults={
                "room_key": str(room_key),
                "user_id": str(user_id),
                "scope_key": normalize_scope_key(scope_key),
                "left_at": closed_at,
                "close_reason": close_reason if closed_at else "",
                "last_seen_at": closed_at or joined_at,
            },
        )
        if not created:
            return span, False
        emit(
            "video.participant.joined",
            {
                "span_id": str(span.id),
                "room_key": span.room_key,
                "user_id": span.user_id,
                "connection_id": span.connection_id,
                "joined_at": span.joined_at.isoformat(),
            },
            key=str(span.id),
        )
        if closed_at is not None:
            _emit_left(span)
    return span, True


def close_span(span: ParticipantSpan, *, at: datetime, reason: str) -> bool:
    """Close an OPEN span at ``at``. Returns whether this call closed it.

    A conditional UPDATE (``left_at IS NULL``), not a save: append-only is a
    property the database enforces here rather than a rule every caller has
    to remember, and two closers racing — a late webhook and the sweeper —
    resolve to whichever landed first instead of the later one restating the
    duration.

    ``at`` is clamped to be no earlier than ``joined_at``: clocks and
    reorderings can present a departure before its arrival, and a negative
    stay would subtract from somebody's month.
    """
    at = max(_aware(at), span.joined_at)
    with transaction.atomic():
        updated = ParticipantSpan.objects.filter(
            pk=span.pk, left_at__isnull=True
        ).update(left_at=at, close_reason=reason, last_seen_at=at)
        if not updated:
            return False
        span.left_at = at
        span.close_reason = reason
        span.last_seen_at = at
        _emit_left(span)
    return True


def _emit_left(span: ParticipantSpan) -> None:
    """The departure fact. Ids and numbers only — no display name goes on
    the bus, and a subscriber that wants one asks the profile side."""
    from stapel_core.comm import emit

    emit(
        "video.participant.left",
        {
            "span_id": str(span.id),
            "room_key": span.room_key,
            "user_id": span.user_id,
            "connection_id": span.connection_id,
            "joined_at": span.joined_at.isoformat(),
            "left_at": span.left_at.isoformat(),
            "close_reason": span.close_reason,
            "duration_seconds": int(
                (span.left_at - span.joined_at).total_seconds()
            ),
        },
        key=str(span.id),
    )


def close_spans_explicitly(
    *,
    room_key: str,
    user_id: str,
    connection_id: str | None = None,
    at: datetime | None = None,
) -> int:
    """Close a person's open spans in a room because they said so.

    The host's leave button and kick path call this. It is deliberately weak:
    it closes what is open and never reopens, re-dates or deletes anything, so
    a product's grace-window policy ("coming back within 60s is the same
    session") stays a product policy — a return opens a NEW span and the
    period's total is the union of both, which is the same number either way.

    Returns how many spans it closed; zero is the ordinary answer for
    somebody the media server already reported gone.
    """
    at = _aware(at or _now())
    qs = ParticipantSpan.objects.filter(
        room_key=str(room_key), user_id=str(user_id), left_at__isnull=True
    )
    if connection_id:
        qs = qs.filter(connection_id=str(connection_id))
    closed = 0
    for span in list(qs):
        if close_span(span, at=at, reason=SpanCloseReason.EXPLICIT):
            closed += 1
    return closed


def backfill_scope_keys(
    resolver,
    *,
    batch_size: int = 1000,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Stamp ``scope_key`` on spans that have none, using the host's resolver.

    ``resolver`` is ``room_key -> scope_key | None``. Only the host can supply
    it: a span holds an opaque room key, and which partition that room belongs
    to is a fact this library has never been told. See the
    ``video_backfill_scope`` management command.

    Idempotent by construction rather than by bookkeeping — the population is
    defined as ``scope_key IS NULL``, so every row this call stamps leaves the
    population, a crash mid-run loses no progress, and a second full run does
    nothing. Rooms are the unit: one resolver call per distinct room, one
    bulk UPDATE per ``batch_size`` spans, because the reason this exists is a
    table holding every call the instance ever carried.

    A ``None`` from the resolver leaves the spans NULL and is counted as
    ``unresolved``. Some rooms really do belong to no partition, and forcing
    them into one would invent a tenant; but a resolver reading the wrong
    table answers ``None`` for everything too, which is why the two are
    counted separately and reported.

    Returns ``{"rooms", "resolved", "unresolved", "spans"}``.
    """
    unscoped = ParticipantSpan.objects.filter(scope_key__isnull=True)
    room_keys = sorted(_distinct_room_keys(unscoped))
    if limit is not None:
        room_keys = room_keys[: max(0, int(limit))]

    result = {"rooms": len(room_keys), "resolved": 0, "unresolved": 0, "spans": 0}
    for room_key in room_keys:
        scope_key = normalize_scope_key(resolver(room_key))
        if scope_key is None:
            result["unresolved"] += 1
            continue
        result["resolved"] += 1
        result["spans"] += _stamp_room(
            room_key, scope_key, batch_size=batch_size, dry_run=dry_run
        )
    logger.info(
        "video_backfill_scope: %(rooms)s room(s), %(resolved)s resolved, "
        "%(unresolved)s unresolved, %(spans)s span(s) stamped",
        result,
    )
    return result


def _stamp_room(room_key: str, scope_key: str, *, batch_size: int, dry_run: bool) -> int:
    """Set one room's unscoped spans, in batches. Returns how many."""
    base = ParticipantSpan.objects.filter(room_key=room_key, scope_key__isnull=True)
    if dry_run:
        return base.count()
    stamped = 0
    while True:
        # Paged by primary key rather than by a sliced UPDATE: not every
        # backend supports UPDATE ... LIMIT, and re-reading the same filter
        # is safe precisely because a stamped row leaves the population.
        ids = list(base.values_list("pk", flat=True)[: max(1, int(batch_size))])
        if not ids:
            return stamped
        stamped += ParticipantSpan.objects.filter(pk__in=ids).update(
            scope_key=scope_key
        )


# ── The sweeper ────────────────────────────────────────────────────────────


def sweep_open_spans(*, now: datetime | None = None) -> dict:
    """Reconcile every open span against the provider's live roster.

    For each room that still holds an open span, ask the provider who is
    actually connected:

    - a span whose connection is there is CONFIRMED — its ``last_seen_at``
      moves to now, which is the moment the next sweep will close it at;
    - a span whose connection is gone is a zombie: closed with
      ``close_reason=sweeper`` **at its last confirmed moment**, not at now.
      That is what bounds the damage of a lost ``participant_left`` to one
      sweep interval instead of to however long until somebody looked;
    - a connection the provider reports with no span of ours OPENS one, from
      the provider's own ``joined_at``. That repairs a lost
      ``participant_joined``, whose failure mode is otherwise silent — no
      row, no zombie, nothing to notice.

    The repair is bounded by what the seam can see: rooms are discovered from
    the open spans we already hold, because the contract answers "who is in
    THIS room", not "which rooms are live". A call whose every join webhook
    was lost is invisible to this loop, which is why the webhook is the
    primary contour and this is the second.

    Returns a counts dict and logs it — a reconciler nobody can observe is
    indistinguishable from one that stopped running.
    """
    from .providers import get_video_provider

    now = _aware(now or _now())
    provider = get_video_provider()
    room_keys = sorted(
        _distinct_room_keys(ParticipantSpan.objects.filter(left_at__isnull=True))
    )
    result = {
        "rooms": len(room_keys),
        "confirmed": 0,
        "closed": 0,
        "opened": 0,
        "unreachable": 0,
    }
    for room_key in room_keys:
        try:
            live = provider.list_participants(room_key)
        except NotImplementedError:
            # Nothing to reconcile against. Saying so once beats closing
            # every span on a guess, and beats leaving the operator to infer
            # it from a month of spans that never end.
            logger.warning(
                "%s does not implement list_participants; presence spans "
                "cannot be reconciled and a lost participant_left will stay "
                "open. %d room(s) skipped.",
                type(provider).__name__,
                len(room_keys),
            )
            result["unreachable"] = len(room_keys)
            return result
        except Exception:
            # One unreachable room must not strand the others: the next
            # sweep closes what this one could not.
            logger.exception("presence sweep could not read room %s", room_key)
            result["unreachable"] += 1
            continue

        alive = {
            p["connection_id"]: p
            for p in (live or [])
            if p.get("connection_id")
        }
        open_spans = list(
            ParticipantSpan.objects.filter(room_key=room_key, left_at__isnull=True)
        )
        for span in open_spans:
            if span.connection_id in alive:
                ParticipantSpan.objects.filter(
                    pk=span.pk, left_at__isnull=True
                ).update(last_seen_at=now)
                result["confirmed"] += 1
            elif close_span(span, at=span.last_seen_at, reason=SpanCloseReason.SWEEPER):
                result["closed"] += 1

        known = {span.connection_id for span in open_spans}
        for connection_id, participant in alive.items():
            if connection_id in known or not participant.get("joined_at"):
                continue
            _, created = open_span(
                room_key=room_key,
                user_id=participant.get("user_id") or connection_id,
                connection_id=connection_id,
                joined_at=participant["joined_at"],
                # The roster echoes the same grant metadata a webhook does, so
                # a span the sweeper REPAIRS lands in the right partition
                # instead of being the one unscoped row in a tenant's month.
                scope_key=participant.get("scope_key"),
            )
            if created:
                result["opened"] += 1

    logger.info(
        "presence sweep: %(rooms)s room(s), %(confirmed)s confirmed, "
        "%(closed)s zombie(s) closed, %(opened)s span(s) repaired, "
        "%(unreachable)s unreachable",
        result,
    )
    return result


# ── GDPR ───────────────────────────────────────────────────────────────────


def pseudonymize_user(user_id) -> int:
    """Replace a person's id in the meter with a stable pseudonym.

    Erasure without destroying the meter, the stapel-agent ledger rule in the
    shape this table needs it: a span holds no text to scrub, so what is
    personal about it is the ``user_id`` column itself. Deleting the rows
    instead would silently restate closed reporting periods — and the very
    question the export answers is "who was in a call during the period,
    counted from the spans and not from whether the account still exists".

    The pseudonym is a keyed digest, so it is stable (the same person's rows
    stay one person, distinct counts and pair overlaps survive untouched) and
    not reversible without the deployment's SECRET_KEY.

    Returns the number of rows rewritten.
    """
    from django.conf import settings

    import hashlib
    import hmac

    user_id = str(user_id)
    digest = hmac.new(
        str(settings.SECRET_KEY).encode(), user_id.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return ParticipantSpan.objects.filter(user_id=user_id).update(
        user_id=f"erased:{digest}"
    )


# ── Reading: periods, union, aggregates ────────────────────────────────────


def period_bounds(period: str) -> tuple[datetime, datetime]:
    """``"YYYY-MM"`` -> the half-open UTC calendar month ``[start, end)``.

    Half-open and UTC by the fleet's period convention (the eventstore's
    ``period_bounds``): a span that ends exactly at midnight on the 1st
    belongs to the month that just finished, once, and never to both.
    Workspace-local timezones are an additive switch the day a report needs
    one — the meter stores absolute instants either way.
    """
    try:
        year, month = str(period).split("-")
        start = datetime(int(year), int(month), 1, tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidPeriod(f"{period!r} is not a YYYY-MM month") from exc
    end = (
        datetime(start.year + 1, 1, 1, tzinfo=timezone.utc)
        if start.month == 12
        else datetime(start.year, start.month + 1, 1, tzinfo=timezone.utc)
    )
    return start, end


def _zone(tz: str | None):
    """A ``ZoneInfo`` for *tz*, or UTC when nothing was asked for."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = str(tz or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise InvalidTimezone(f"{name!r} is not an IANA time zone") from exc


def month_bounds(month: str, tz: str | None = None) -> tuple[datetime, datetime]:
    """``"YYYY-MM"`` + a zone -> the half-open month ``[start, end)`` in UTC.

    :func:`period_bounds` generalized to the calendar a human is looking at.
    The boundary is LOCAL midnight converted to an absolute instant, so
    "August" for a Berlin workspace starts at 22:00 UTC on July 31st — and,
    across a DST transition, one of the twelve months is 23 or 25 hours short
    of its naive length. That is the point: a report titled "March" must not
    quietly include the hour that belongs to April, nor drop the hour that
    belongs to March, because the two are what a spring-forward moves.

    The stored instants never change — the meter records absolute time — so a
    host may re-cut the same spans into a different zone's months at any point
    without a migration.
    """
    zone = _zone(tz)
    try:
        year, month_number = (int(part) for part in str(month).split("-"))
        start = datetime(year, month_number, 1, tzinfo=zone)
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidPeriod(f"{month!r} is not a YYYY-MM month") from exc
    end = (
        datetime(year + 1, 1, 1, tzinfo=zone)
        if month_number == 12
        else datetime(year, month_number + 1, 1, tzinfo=zone)
    )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def recent_months(count: int, tz: str | None = None, *, now: datetime | None = None) -> list[str]:
    """The last *count* calendar months in *tz*, NEWEST FIRST.

    Newest first because that is the order the answer is read in: a usage
    table opens on the month somebody is currently living in, not on the one
    that fell out of the retention window.
    """
    zone = _zone(tz)
    here = (now or _now()).astimezone(zone)
    year, month_number = here.year, here.month
    months = []
    for _ in range(max(1, int(count))):
        months.append(f"{year:04d}-{month_number:02d}")
        month_number -= 1
        if month_number == 0:
            year, month_number = year - 1, 12
    return months


def _resolve_period(payload: dict) -> tuple[datetime, datetime]:
    """The ``[start, end)`` a Function payload asks for.

    ``{"period": "2026-08"}`` for the ordinary calendar month, or an explicit
    ``{"period_start": iso, "period_end": iso}`` for a reporting window that
    is not one. Neither: everything ever recorded.
    """
    if payload.get("period"):
        return period_bounds(payload["period"])
    start = _parse_iso(payload.get("period_start")) or datetime(
        1970, 1, 1, tzinfo=timezone.utc
    )
    end = _parse_iso(payload.get("period_end")) or (_now() + timedelta(days=365 * 100))
    if end <= start:
        raise InvalidPeriod(f"period_end {end.isoformat()} is not after {start.isoformat()}")
    return start, end


def _spans_in_period(start: datetime, end: datetime, *, user_id="", room_key="", scope_key=None):
    """Every span overlapping ``[start, end)``, optionally one scope of it."""
    from django.db.models import Q

    qs = ParticipantSpan.objects.filter(joined_at__lt=end).filter(
        Q(left_at__isnull=True) | Q(left_at__gt=start)
    )
    if user_id:
        qs = qs.filter(user_id=str(user_id))
    if room_key:
        qs = qs.filter(room_key=str(room_key))
    if scope_key is not None:
        qs = qs.filter(scope_key=str(scope_key))
    return qs


def _distinct_room_keys(qs) -> list:
    """The rooms a queryset touches, each once.

    ``.order_by()`` first, and it is not decoration: the model has a Meta
    ordering, Django adds those columns to a ``values_list().distinct()``
    SELECT, and the DISTINCT then applies to ``(room_key, joined_at, id)`` —
    i.e. to nothing. Every caller here batches per room, so a room appearing
    twice is a room whose whole computation runs twice and whose rows are
    exported twice.
    """
    return list(
        qs.order_by().values_list("room_key", flat=True).distinct()
    )


def _clip(span, start: datetime, end: datetime, *, now: datetime | None = None):
    """One span as an ``(a, b)`` interval clipped to the window, or None.

    An open span is read as running up to now — presence recorded so far, not
    presence forever. A window that has already closed clips it to the window
    end, so a historical month never grows because somebody's span is still
    open today.
    """
    now = now or _now()
    a = max(span.joined_at, start)
    b = min(span.left_at or now, end)
    return (a, b) if b > a else None


def _merge_intervals(intervals: list) -> list:
    """Union of ``[a, b)`` intervals: sorted, non-overlapping, touching joined.

    THE semantic decision of the whole meter (design §1.4): a person on a
    laptop and a phone is one person present, not two. Summing connection
    durations would bill a second device as a second human, which is not a
    number anybody can defend to the customer holding the invoice.
    """
    ordered = sorted(intervals)
    merged: list = []
    for a, b in ordered:
        if merged and a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _interval_seconds(intervals: list) -> int:
    return int(sum((b - a).total_seconds() for a, b in intervals))


def _overlap_seconds(left: list, right: list) -> int:
    """Seconds two merged interval lists are both inside.

    Two pointers over two sorted, non-overlapping lists — linear in their
    combined length, which is what keeps the pair export affordable (see
    :func:`pairs_export`).
    """
    i = j = 0
    total = 0.0
    while i < len(left) and j < len(right):
        a = max(left[i][0], right[j][0])
        b = min(left[i][1], right[j][1])
        if b > a:
            total += (b - a).total_seconds()
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return int(total)


def presence_aggregate(
    *, user_id: str = "", room_key: str = "", period_start: datetime, period_end: datetime
) -> dict:
    """Unioned presence for one person, or for one room, over a window.

    For a ``user_id`` scope the answer is that person's minutes across every
    room and device — one merged timeline. For a ``room_key`` scope it is the
    sum of each attendee's own merged timeline inside that room, i.e.
    person-seconds spent in the call, not wall-clock room duration.
    """
    now = _now()
    spans = list(_spans_in_period(period_start, period_end, user_id=user_id, room_key=room_key))
    by_user: dict[str, list] = {}
    rooms = set()
    for span in spans:
        interval = _clip(span, period_start, period_end, now=now)
        if interval is None:
            continue
        by_user.setdefault(span.user_id, []).append(interval)
        rooms.add(span.room_key)
    seconds = sum(_interval_seconds(_merge_intervals(iv)) for iv in by_user.values())
    return {
        "user_id": str(user_id) if user_id else None,
        "room_key": str(room_key) if room_key else None,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "presence_seconds": seconds,
        "rooms_count": len(rooms),
        "users_count": len(by_user),
        "spans_count": len(spans),
    }


# ── Reading: the per-scope usage rollup ────────────────────────────────────


def usage_rollup(
    *,
    scope_key: str,
    period_start: datetime,
    period_end: datetime,
    group_by: str = "user",
) -> list[dict]:
    """One partition's period, one row per person:
    ``[{user_id, presence_seconds, rooms, connections, first_seen, last_seen}]``.

    The same arithmetic as :func:`presence_aggregate`, cut by ``scope_key``
    and reported per user instead of totalled — deliberately the same code
    path (``_clip`` / ``_merge_intervals``), because two implementations of
    "how long was this person present" is two answers, and the one on the
    workspace-admin screen would be the one nobody reconciles against the
    invoice. Seconds are UNIONED per person: a laptop and a phone were one
    human being present.

    ``rooms`` is the count of DISTINCT ``room_key``s, not of spans — a person
    who reconnected nine times to one call attended one call. ``connections``
    is the distinct connection count, which is where the reconnects show up,
    and it is reported separately rather than folded in so a support question
    ("why does this say four devices?") has an answer in the data.

    ``first_seen`` / ``last_seen`` are CLIPPED to the window, like the
    seconds: a stay that started last month starts, in this month's row, at
    the month's first instant. A row whose numbers and whose timestamps
    disagreed about the window would be unreadable.

    Rows come back longest-first, then by user id — a stable total order, so
    two calls with the same data page and render identically.

    ``group_by`` takes only ``"user"`` today and raises on anything else. It
    is an argument rather than an implied constant so the call site states
    which question it asked, and so a second grouping is additive instead of
    a signature change; silently ignoring an unknown value would answer a
    different question than the caller asked for.
    """
    if group_by != "user":
        raise ValueError(
            f"usage_rollup(group_by={group_by!r}) is not implemented — the "
            'only grouping is "user". Answering the default silently would '
            "hand back a per-user table labelled as something else."
        )
    scope_key = str(scope_key or "").strip()
    if not scope_key:
        raise ValueError(
            "usage_rollup needs a scope_key: an empty one is not 'every "
            "scope', it is a partition that never exists (spans store NULL)."
        )
    now = _now()
    per_user: dict[str, dict] = {}
    for span in _spans_in_period(period_start, period_end, scope_key=scope_key):
        interval = _clip(span, period_start, period_end, now=now)
        if interval is None:
            continue
        entry = per_user.setdefault(
            span.user_id, {"intervals": [], "rooms": set(), "connections": set()}
        )
        entry["intervals"].append(interval)
        entry["rooms"].add(span.room_key)
        entry["connections"].add(span.connection_id)

    rows = []
    for user_id, entry in per_user.items():
        merged = _merge_intervals(entry["intervals"])
        rows.append(
            {
                "user_id": user_id,
                "presence_seconds": _interval_seconds(merged),
                "rooms": len(entry["rooms"]),
                "connections": len(entry["connections"]),
                "first_seen": merged[0][0].isoformat(),
                "last_seen": merged[-1][1].isoformat(),
            }
        )
    rows.sort(key=lambda row: (-row["presence_seconds"], row["user_id"]))
    return rows


def usage_rollup_by_month(
    *,
    scope_key: str,
    months: int = ROLLUP_DEFAULT_MONTHS,
    tz: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """The month-by-month table:
    ``[{month, period_start, period_end, users: [...]}]``, newest month first.

    Buckets are calendar months in ``tz`` (default UTC), so the boundaries are
    local midnights and a DST transition shortens or lengthens exactly one of
    them (see :func:`month_bounds`). ``months`` is clamped to
    ``[1, ROLLUP_MAX_MONTHS]``: this walk is linear in the spans of every
    bucket and it is reachable from a query string.

    An empty month is present with ``users: []`` rather than omitted. The
    caller is drawing a table of months, and a gap that means "no calls" must
    not be indistinguishable from a gap that means "this row failed to load".
    """
    count = max(1, min(int(months or ROLLUP_DEFAULT_MONTHS), ROLLUP_MAX_MONTHS))
    result = []
    for month in recent_months(count, tz, now=now):
        start, end = month_bounds(month, tz)
        result.append(
            {
                "month": month,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "users": usage_rollup(
                    scope_key=scope_key, period_start=start, period_end=end
                ),
            }
        )
    return result


# ── Reading: exports ───────────────────────────────────────────────────────


def spans_export(
    *,
    cursor: str | None = None,
    limit: int = EXPORT_DEFAULT_LIMIT,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> dict:
    """Cursor-paged snapshot of raw spans: ``{"rows", "cursor", "total"}``.

    ``rows``, never ``items``: core's snapshot reader looks for that exact
    key and a differently-named list rebuilds a consumer's table to EMPTY
    while reporting success.

    Keyset over ``(joined_at, id)`` — a total order, so the walk visits every
    row exactly once even while the sweeper is closing spans underneath it.
    Durations are raw: no threshold is applied and none is applicable here,
    because which stays "count" is the consumer's policy and baking it into
    the export would make a pricing change a code release.
    """
    from django.db.models import Q

    limit = _limit(limit)
    qs = ParticipantSpan.objects.all()
    if period_start is not None and period_end is not None:
        qs = _spans_in_period(period_start, period_end)

    total = None
    if cursor is None:
        total = qs.count()
    else:
        last_joined, last_id = _decode_cursor(cursor, 2)
        last_joined = _parse_iso(last_joined)
        qs = qs.filter(
            Q(joined_at__gt=last_joined)
            | Q(joined_at=last_joined, id__gt=last_id)
        )

    page = list(qs.order_by("joined_at", "id")[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    rows = [
        {
            "span_id": str(span.id),
            "room_key": span.room_key,
            "scope_key": span.scope_key,
            "user_id": span.user_id,
            "connection_id": span.connection_id,
            "joined_at": span.joined_at.isoformat(),
            "left_at": span.left_at.isoformat() if span.left_at else None,
            "close_reason": span.close_reason,
            "duration_seconds": (
                int((span.left_at - span.joined_at).total_seconds())
                if span.left_at
                else None
            ),
            # Unix MILLISECONDS — an Event's clock, so a live
            # video.participant.left arriving mid-walk supersedes the
            # snapshot row instead of racing it.
            "seq": int((span.left_at or span.joined_at).timestamp() * 1000),
        }
        for span in page
    ]
    next_cursor = (
        _encode_cursor(page[-1].joined_at.isoformat(), str(page[-1].id))
        if has_more and page
        else None
    )
    return {"rows": rows, "cursor": next_cursor, "total": total}


def pairs_export(
    *,
    period_start: datetime,
    period_end: datetime,
    cursor: str | None = None,
    limit: int = EXPORT_DEFAULT_LIMIT,
) -> dict:
    """Co-presence per pair of people per room: ``{"rows", "cursor", "total"}``.

    One row is ``(user_a, user_b, room_key, co_presence_seconds)`` with
    ``user_a < user_b`` — a pair is one fact, not two, and the number is the
    RAW overlap of their merged timelines in that room. No minimum is applied:
    the owner's threshold lives in the external service and is revised per
    customer, so an export that had already dropped the short overlaps could
    not answer the revised question.

    Anonymous guests are ordinary people here. They hold a real account id
    like anyone else, and excluding them would price a product by how few
    accounts a customer bothers to create.

    **Cost.** Rooms are the batch boundary: one room's spans are loaded,
    merged per user, and every pair is evaluated. That is ``O(N²)`` pair
    evaluations for a room with ``N`` distinct attendees, each pair costing a
    two-pointer pass linear in their interval counts (usually one interval
    each). A 30-person meeting is 435 evaluations; a 500-person webinar is
    ~125k, still a fraction of a second, but the growth is quadratic and a
    room with thousands of attendees is where this stops being cheap. It is
    the shape of the question, not of the implementation — a co-presence
    matrix HAS N²/2 cells — so the mitigation, if a deployment ever needs
    one, is to stop asking it for broadcast-shaped rooms rather than to
    rewrite this loop.

    ``total`` is always ``None``: counting the pairs costs exactly as much as
    computing them, and the canon reads ``None`` as "the owner does not
    report it" rather than as zero.
    """
    limit = _limit(limit)
    now = _now()
    after = _decode_cursor(cursor, 3) if cursor else None

    room_keys = sorted(_distinct_room_keys(_spans_in_period(period_start, period_end)))
    if after is not None:
        room_keys = [key for key in room_keys if key >= after[0]]

    rows: list = []
    next_cursor = None
    for room_key in room_keys:
        pairs = _room_pairs(room_key, period_start, period_end, now=now)
        for user_a, user_b, seconds in pairs:
            if after is not None and (room_key, user_a, user_b) <= tuple(after):
                continue
            if len(rows) == limit:
                next_cursor = _encode_cursor(*_last_key(rows))
                return {"rows": rows, "cursor": next_cursor, "total": None}
            rows.append(
                {
                    "room_key": room_key,
                    "user_a": user_a,
                    "user_b": user_b,
                    "co_presence_seconds": seconds,
                }
            )
    return {"rows": rows, "cursor": next_cursor, "total": None}


def _last_key(rows: list) -> tuple:
    last = rows[-1]
    return last["room_key"], last["user_a"], last["user_b"]


def _room_pairs(room_key: str, start: datetime, end: datetime, *, now: datetime) -> list:
    """Every co-present pair in one room, sorted by ``(user_a, user_b)``."""
    by_user: dict[str, list] = {}
    for span in _spans_in_period(start, end, room_key=room_key):
        interval = _clip(span, start, end, now=now)
        if interval is not None:
            by_user.setdefault(span.user_id, []).append(interval)
    merged = {user: _merge_intervals(iv) for user, iv in by_user.items()}
    users = sorted(merged)
    pairs = []
    for index, user_a in enumerate(users):
        for user_b in users[index + 1 :]:
            seconds = _overlap_seconds(merged[user_a], merged[user_b])
            if seconds > 0:
                pairs.append((user_a, user_b, seconds))
    return pairs


# ── Small shared helpers ───────────────────────────────────────────────────


def _limit(limit) -> int:
    return max(1, min(int(limit or EXPORT_DEFAULT_LIMIT), EXPORT_MAX_LIMIT))


def _encode_cursor(*parts: str) -> str:
    raw = json.dumps(list(parts), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str, arity: int) -> list:
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor))
        if not isinstance(parts, list) or len(parts) != arity:
            raise ValueError(f"expected {arity} parts")
        return [str(part) for part in parts]
    except Exception as exc:
        raise InvalidExportCursor(cursor) from exc


def _now() -> datetime:
    from django.utils import timezone as dj_timezone

    return dj_timezone.now()


def _aware(moment: datetime) -> datetime:
    """A timestamp in UTC, whatever shape it arrived in.

    A naive datetime from a host that runs ``USE_TZ=False`` would compare
    against an aware one by raising, in the middle of a webhook.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError as exc:
        raise InvalidPeriod(f"{value!r} is not an ISO-8601 timestamp") from exc


__all__ = [
    "EXPORT_DEFAULT_LIMIT",
    "EXPORT_MAX_LIMIT",
    "ROLLUP_DEFAULT_MONTHS",
    "ROLLUP_MAX_MONTHS",
    "InvalidExportCursor",
    "InvalidPeriod",
    "InvalidTimezone",
    "backfill_scope_keys",
    "close_span",
    "close_spans_explicitly",
    "handle_participant_joined",
    "handle_participant_left",
    "month_bounds",
    "normalize_scope_key",
    "open_span",
    "pairs_export",
    "period_bounds",
    "presence_aggregate",
    "pseudonymize_user",
    "recent_months",
    "spans_export",
    "sweep_open_spans",
    "usage_rollup",
    "usage_rollup_by_month",
]
