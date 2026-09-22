import datetime as dt

import pytest

from qsuite.models import WindowKind
from qsuite.ranges import (anchor, day_windows, month_windows, plan, resolve_range,
                           strip_windows)

TODAY = dt.date(2026, 9, 22)


def test_month_windows_cover_range_exactly_once():
    start, end = dt.date(2026, 10, 15), dt.date(2027, 2, 3)
    wins = month_windows(start, end)
    covered = [d for w in wins for d in w.dates()]
    assert covered == [start + dt.timedelta(days=i)
                       for i in range((end - start).days + 1)]
    assert len(covered) == len(set(covered)), "no date may be scanned twice"


def test_month_windows_clip_to_range_edges():
    wins = month_windows(dt.date(2026, 10, 15), dt.date(2026, 12, 3))
    assert wins[0].start == dt.date(2026, 10, 15)
    assert wins[0].end == dt.date(2026, 10, 31)
    assert wins[-1].end == dt.date(2026, 12, 3)


def test_month_view_is_far_cheaper_than_day_by_day():
    start, end = TODAY, TODAY + dt.timedelta(days=329)
    assert len(month_windows(start, end)) <= 12
    assert len(day_windows(start, end)) == 330
    assert len(strip_windows(start, end, 7)) == 48


def test_strip_anchor_sits_mid_window():
    w = strip_windows(dt.date(2026, 10, 1), dt.date(2026, 10, 7), 7)[0]
    assert anchor(w) == dt.date(2026, 10, 4)


def test_strip_width_must_be_positive():
    with pytest.raises(ValueError):
        strip_windows(dt.date(2026, 1, 1), dt.date(2026, 1, 5), 0)


def test_empty_when_end_before_start():
    assert month_windows(dt.date(2026, 5, 1), dt.date(2026, 4, 1)) == []
    assert strip_windows(dt.date(2026, 5, 1), dt.date(2026, 4, 1), 7) == []


def test_resolve_days_starts_tomorrow():
    start, end = resolve_range(None, None, 90, today=TODAY)
    assert start == TODAY + dt.timedelta(days=1)
    assert (end - start).days == 89


def test_resolve_clamps_beyond_schedule_horizon():
    _, end = resolve_range("2026-09-23", None, 900, today=TODAY)
    assert end <= TODAY + dt.timedelta(days=365)


def test_resolve_rejects_backwards_range():
    with pytest.raises(ValueError):
        resolve_range("2026-12-01", "2026-11-01", None, today=TODAY)


def test_resolve_requires_something():
    with pytest.raises(ValueError):
        resolve_range(None, None, None, today=TODAY)


def test_plan_dispatches_on_kind():
    s, e = dt.date(2026, 10, 1), dt.date(2026, 10, 14)
    assert len(plan(s, e, WindowKind.MONTH)) == 1
    assert len(plan(s, e, WindowKind.STRIP, 7)) == 2
    assert len(plan(s, e, WindowKind.DAY)) == 14
