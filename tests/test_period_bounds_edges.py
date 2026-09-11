"""The last month the calendar has — security audit 2026-09-11, L-8.

``?period=9999-12`` reached ``datetime(10000, 1, 1)`` and raised a bare
``ValueError`` from OUTSIDE the try that exists to turn a bad period into
``InvalidPeriod``, so a malformed query parameter on a staff/service surface
answered 500 instead of 400. The guard covered parsing the month and not
computing the month after it.
"""
import pytest

from stapel_video.presence import InvalidPeriod, month_bounds, period_bounds


@pytest.mark.parametrize("period", ["9999-12", "9999-13", "0000-01", "abc", "2026"])
def test_a_period_the_calendar_cannot_answer_is_invalid_not_a_crash(period):
    with pytest.raises(InvalidPeriod):
        period_bounds(period)


@pytest.mark.parametrize("month", ["9999-12", "9999-13", "0000-01", "abc", "2026"])
def test_month_bounds_answers_the_same_way(month):
    with pytest.raises(InvalidPeriod):
        month_bounds(month)


def test_the_last_month_that_does_fit_is_still_answered():
    start, end = period_bounds("9999-11")
    assert (start.year, start.month) == (9999, 11)
    assert (end.year, end.month) == (9999, 12)
