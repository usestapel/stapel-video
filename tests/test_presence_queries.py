"""The Query-Functions: union semantics, pair overlap, export pagination.

Two arithmetic claims are load-bearing here and are asserted rather than
described. **Union**: a person on two devices was present once, so the second
device adds only the time the first was not connected. **Overlap**: "who did
they actually talk to" is the intersection of two merged timelines in one
room, raw, with no threshold — the 15-minute line lives in the external
service and is revised per customer, so the export that feeds it must carry
the full durations.
"""
from datetime import datetime, timedelta, timezone

import pytest
from stapel_core.comm import call

from stapel_video import presence
from stapel_video.models import ParticipantSpan

pytestmark = pytest.mark.django_db

ROOM = "abc-defg-hij"
OTHER_ROOM = "zzz-zzzz-zzz"
A = "user-a"
B = "user-b"
C = "user-c"
T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
PERIOD = "2026-08"


def span(user, *, room=ROOM, start=0, minutes=60, connection=None, closed=True):
    joined = T0 + timedelta(minutes=start)
    presence.open_span(
        room_key=room,
        user_id=user,
        connection_id=connection or f"{user}-{room}-{start}",
        joined_at=joined,
        closed_at=joined + timedelta(minutes=minutes) if closed else None,
        close_reason="webhook" if closed else "",
    )


# ── Union semantics (design §1.4) ──────────────────────────────────────────


def test_two_overlapping_connections_are_one_presence_not_two():
    # Laptop 12:00-13:00, phone 12:30-13:30. Present 12:00-13:30 = 90 min.
    span(A, start=0, minutes=60, connection="laptop")
    span(A, start=30, minutes=60, connection="phone")

    result = call("video.presence.aggregate", {"user_id": A, "period": PERIOD})
    assert result["presence_seconds"] == 90 * 60


def test_disjoint_connections_add_up():
    span(A, start=0, minutes=10, connection="laptop")
    span(A, start=30, minutes=10, connection="phone")
    result = call("video.presence.aggregate", {"user_id": A, "period": PERIOD})
    assert result["presence_seconds"] == 20 * 60


def test_two_rooms_at_once_still_count_once():
    """Parallel calls are a real thing the schema allows; a person cannot be
    present for 120 minutes in a 60-minute hour."""
    span(A, room=ROOM, start=0, minutes=60, connection="one")
    span(A, room=OTHER_ROOM, start=0, minutes=60, connection="two")

    result = call("video.presence.aggregate", {"user_id": A, "period": PERIOD})
    assert result["presence_seconds"] == 60 * 60
    assert result["rooms_count"] == 2


def test_room_scope_sums_each_attendee_not_the_wall_clock():
    span(A, start=0, minutes=60)
    span(B, start=30, minutes=60)
    result = call("video.presence.aggregate", {"room_key": ROOM, "period": PERIOD})
    assert result["presence_seconds"] == 120 * 60  # person-seconds
    assert result["users_count"] == 2
    assert result["rooms_count"] == 1


def test_a_span_is_clipped_to_the_period_it_is_reported_in():
    # 23:30 on the last day of July into 00:30 on the 1st of August.
    presence.open_span(
        room_key=ROOM,
        user_id=A,
        connection_id="midnight",
        joined_at=datetime(2026, 7, 31, 23, 30, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc),
        close_reason="webhook",
    )
    july = call("video.presence.aggregate", {"user_id": A, "period": "2026-07"})
    august = call("video.presence.aggregate", {"user_id": A, "period": "2026-08"})
    assert july["presence_seconds"] == 30 * 60
    assert august["presence_seconds"] == 30 * 60


def test_an_open_span_counts_up_to_now_not_forever():
    from django.utils import timezone as dj_timezone

    now = dj_timezone.now()
    presence.open_span(
        room_key=ROOM,
        user_id=A,
        connection_id="still-here",
        joined_at=now - timedelta(minutes=10),
    )
    result = presence.presence_aggregate(
        user_id=A, period_start=now - timedelta(days=1), period_end=now + timedelta(days=1)
    )
    assert 9 * 60 <= result["presence_seconds"] <= 11 * 60


def test_aggregate_refuses_two_scopes_at_once():
    from stapel_core.comm.exceptions import FunctionCallError

    with pytest.raises(FunctionCallError):
        call("video.presence.aggregate", {"user_id": A, "room_key": ROOM})
    with pytest.raises(FunctionCallError):
        call("video.presence.aggregate", {"period": PERIOD})


# ── Pair overlap ───────────────────────────────────────────────────────────


def test_a_three_way_room_yields_three_pairs_with_their_own_overlaps():
    # A 12:00-13:00, B 12:30-13:30, C 12:45-12:55.
    span(A, start=0, minutes=60)
    span(B, start=30, minutes=60)
    span(C, start=45, minutes=10)

    result = call("video.presence.pairs_export", {"period": PERIOD})
    pairs = {(r["user_a"], r["user_b"]): r["co_presence_seconds"] for r in result["rows"]}
    assert pairs == {
        (A, B): 30 * 60,  # 12:30-13:00
        (A, C): 10 * 60,  # 12:45-12:55
        (B, C): 10 * 60,  # 12:45-12:55
    }
    assert all(row["room_key"] == ROOM for row in result["rows"])
    # A pair is ONE fact, not two: no (B, A) row anywhere.
    assert all(row["user_a"] < row["user_b"] for row in result["rows"])


def test_the_same_two_people_in_two_rooms_are_two_rows():
    span(A, room=ROOM, start=0, minutes=60)
    span(B, room=ROOM, start=0, minutes=60)
    span(A, room=OTHER_ROOM, start=0, minutes=10, connection="a2")
    span(B, room=OTHER_ROOM, start=0, minutes=10, connection="b2")

    rows = call("video.presence.pairs_export", {"period": PERIOD})["rows"]
    assert {(r["room_key"], r["co_presence_seconds"]) for r in rows} == {
        (ROOM, 3600),
        (OTHER_ROOM, 600),
    }


def test_people_in_the_same_room_at_different_times_are_not_a_pair():
    span(A, start=0, minutes=10)
    span(B, start=30, minutes=10)
    assert call("video.presence.pairs_export", {"period": PERIOD})["rows"] == []


def test_a_five_second_look_in_is_exported_raw_not_dropped():
    """The threshold lives outside. An export that had already filtered could
    not answer the revised question."""
    span(A, start=0, minutes=60)
    presence.open_span(
        room_key=ROOM,
        user_id=B,
        connection_id="drive-by",
        joined_at=T0 + timedelta(minutes=5),
        closed_at=T0 + timedelta(minutes=5, seconds=5),
        close_reason="webhook",
    )
    rows = call("video.presence.pairs_export", {"period": PERIOD})["rows"]
    assert [r["co_presence_seconds"] for r in rows] == [5]


def test_one_persons_two_devices_do_not_double_their_pair_time():
    span(A, start=0, minutes=60, connection="laptop")
    span(A, start=0, minutes=60, connection="phone")
    span(B, start=0, minutes=60)

    rows = call("video.presence.pairs_export", {"period": PERIOD})["rows"]
    assert [r["co_presence_seconds"] for r in rows] == [3600]


def test_pairs_export_reports_no_total_and_says_so_as_null():
    span(A, start=0, minutes=60)
    span(B, start=0, minutes=60)
    result = call("video.presence.pairs_export", {"period": PERIOD})
    assert result["total"] is None


# ── Export shape and pagination ────────────────────────────────────────────


def test_spans_export_is_rows_cursor_total_never_items():
    span(A, start=0, minutes=10)
    result = call("video.presence.spans_export", {})
    assert set(result) == {"rows", "cursor", "total"}
    assert "items" not in result
    assert result["total"] == 1
    row = result["rows"][0]
    assert row["user_id"] == A
    assert row["duration_seconds"] == 600
    assert row["close_reason"] == "webhook"
    assert isinstance(row["seq"], int)


def test_spans_export_pagination_walks_every_row_exactly_once():
    for index in range(25):
        span(A, start=index, minutes=1, connection=f"c{index}")

    seen = []
    cursor = None
    pages = 0
    while True:
        page = call("video.presence.spans_export", {"cursor": cursor, "limit": 7})
        pages += 1
        seen.extend(row["span_id"] for row in page["rows"])
        cursor = page["cursor"]
        if not cursor:
            break
        assert pages < 20, "cursor never terminated"

    assert len(seen) == 25
    assert len(set(seen)) == 25
    assert set(seen) == {str(pk) for pk in ParticipantSpan.objects.values_list("id", flat=True)}


def test_spans_export_reports_total_on_the_first_page_only():
    for index in range(5):
        span(A, start=index, minutes=1, connection=f"c{index}")
    first = call("video.presence.spans_export", {"limit": 2})
    assert first["total"] == 5
    second = call("video.presence.spans_export", {"cursor": first["cursor"], "limit": 2})
    assert second["total"] is None


def test_pairs_export_pagination_walks_every_pair_exactly_once():
    # Four rooms, four people each, everybody overlapping: 4 * 6 = 24 pairs.
    for room in range(4):
        for user in range(4):
            span(
                f"u{user}",
                room=f"room-{room}",
                start=0,
                minutes=60,
                connection=f"r{room}-u{user}",
            )

    seen = []
    cursor = None
    pages = 0
    while True:
        page = call(
            "video.presence.pairs_export",
            {"period": PERIOD, "cursor": cursor, "limit": 5},
        )
        pages += 1
        seen.extend((r["room_key"], r["user_a"], r["user_b"]) for r in page["rows"])
        cursor = page["cursor"]
        if not cursor:
            break
        assert pages < 20, "cursor never terminated"

    assert len(seen) == 24
    assert len(set(seen)) == 24


def test_a_forged_cursor_is_rejected_rather_than_silently_restarting():
    span(A, start=0, minutes=10)
    from stapel_core.comm.exceptions import FunctionCallError

    with pytest.raises(FunctionCallError):
        call("video.presence.spans_export", {"cursor": "not-a-cursor"})


def test_spans_export_can_be_narrowed_to_a_period():
    presence.open_span(
        room_key=ROOM,
        user_id=A,
        connection_id="july",
        joined_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
        closed_at=datetime(2026, 7, 5, 1, tzinfo=timezone.utc),
        close_reason="webhook",
    )
    span(B, start=0, minutes=10)
    august = call("video.presence.spans_export", {"period": "2026-08"})
    assert [row["user_id"] for row in august["rows"]] == [B]


def test_a_period_that_is_not_a_month_is_refused():
    from stapel_core.comm.exceptions import FunctionCallError

    with pytest.raises(FunctionCallError):
        call("video.presence.aggregate", {"user_id": A, "period_start": "yesterday"})


# ── The merge primitive itself ─────────────────────────────────────────────


def test_merge_intervals_joins_touching_and_nested_ranges():
    m = timedelta(minutes=1)
    merged = presence._merge_intervals(
        [
            (T0, T0 + 10 * m),
            (T0 + 10 * m, T0 + 20 * m),  # touching
            (T0 + 12 * m, T0 + 15 * m),  # nested
            (T0 + 30 * m, T0 + 40 * m),  # disjoint
        ]
    )
    assert merged == [(T0, T0 + 20 * m), (T0 + 30 * m, T0 + 40 * m)]
    assert presence._interval_seconds(merged) == 30 * 60
