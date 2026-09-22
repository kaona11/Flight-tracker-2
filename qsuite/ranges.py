"""Turning a date range into the fewest upstream requests a provider allows."""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Iterator

from .models import WindowKind


@dataclass(frozen=True)
class Window:
    """One upstream request's worth of dates."""

    start: dt.date
    end: dt.date          # inclusive
    kind: WindowKind

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def dates(self) -> list[dt.date]:
        return [self.start + dt.timedelta(days=i) for i in range(self.days)]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.start.isoformat()}..{self.end.isoformat()} ({self.kind.value})"


def daterange(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)


def month_windows(start: dt.date, end: dt.date) -> list[Window]:
    """Split into calendar months, clipped to the range.

    Month-view engines key off a month, so this is the natural unit: a 330-day
    scan becomes ~11 requests instead of 330.
    """
    if end < start:
        return []
    out: list[Window] = []
    cur = start
    while cur <= end:
        last_dom = calendar.monthrange(cur.year, cur.month)[1]
        month_end = cur.replace(day=last_dom)
        out.append(Window(cur, min(month_end, end), WindowKind.MONTH))
        cur = month_end + dt.timedelta(days=1)
    return out


def strip_windows(start: dt.date, end: dt.date, width: int) -> list[Window]:
    """Fixed-width windows for flexible-date strips (+/- N days around an anchor)."""
    if width < 1:
        raise ValueError("strip width must be >= 1")
    if end < start:
        return []
    out: list[Window] = []
    cur = start
    while cur <= end:
        w_end = min(cur + dt.timedelta(days=width - 1), end)
        out.append(Window(cur, w_end, WindowKind.STRIP))
        cur = w_end + dt.timedelta(days=1)
    return out


def day_windows(start: dt.date, end: dt.date) -> list[Window]:
    return [Window(d, d, WindowKind.DAY) for d in daterange(start, end)]


def plan(start: dt.date, end: dt.date, kind: WindowKind, strip_width: int = 7) -> list[Window]:
    """Plan the request windows a provider of this kind needs for the range."""
    if kind is WindowKind.MONTH:
        return month_windows(start, end)
    if kind is WindowKind.STRIP:
        return strip_windows(start, end, strip_width)
    return day_windows(start, end)


def anchor(window: Window) -> dt.date:
    """The date a strip-style engine should be queried on to cover the window.

    Flexible-date strips centre on the searched date, so we aim at the middle.
    """
    return window.start + dt.timedelta(days=(window.days - 1) // 2)


def resolve_range(
    start: str | dt.date | None,
    end: str | dt.date | None,
    days: int | None,
    today: dt.date | None = None,
) -> tuple[dt.date, dt.date]:
    """Resolve CLI-style range inputs into a concrete inclusive range.

    Accepts an explicit ``--start``/``--end``, or ``--days N`` meaning "the next
    N days starting tomorrow". Award inventory for today is not bookable in any
    useful sense, so a bare ``--days`` starts at tomorrow.
    """
    today = today or dt.date.today()
    s = _coerce(start) if start else None
    e = _coerce(end) if end else None
    if s and e:
        pass
    elif s and days:
        e = s + dt.timedelta(days=days - 1)
    elif e and days:
        s = e - dt.timedelta(days=days - 1)
    elif days:
        s = today + dt.timedelta(days=1)
        e = s + dt.timedelta(days=days - 1)
    elif s:
        e = s + dt.timedelta(days=329)
    else:
        raise ValueError("need --start/--end, or --days")
    if e < s:
        raise ValueError(f"end {e} is before start {s}")
    horizon = today + dt.timedelta(days=365)
    if e > horizon:
        # Airline schedules load ~330-361 days out; asking past that just burns
        # requests on empty months.
        e = horizon
    return s, e


def _coerce(v: str | dt.date) -> dt.date:
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(v)
