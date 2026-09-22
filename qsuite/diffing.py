"""What changed since the last scan.

Requirement 5 is "notify me only of new dates". That means the diff, not the
calendar, is the thing that gets pushed at you — and it means being careful
about what counts as new:

* ``NEWLY_OPENED``  — we saw this date, it was definitively empty, now it has space.
  This is the one you actually want at 3am.
* ``FIRST_SEEN``    — we had no usable prior reading (never scanned, or the previous
  run errored/was blocked on it) and it now has space. Real news, but weaker:
  it may have been open all along and we simply could not see it.
* ``CLOSED``        — space we reported is gone. Not alerted by default, but worth
  having in the report so you know the calendar moved under you.
* ``IMPROVED``      — still available, but cheaper or with more seats.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from .models import Cabin, Cell, Route, Status


class ChangeKind(str, Enum):
    NEWLY_OPENED = "newly_opened"
    FIRST_SEEN = "first_seen"
    CLOSED = "closed"
    IMPROVED = "improved"


#: Statuses that mean "we looked and there was definitively nothing".
DEFINITE_EMPTY = {Status.NONE}
#: Statuses that mean "we have space".
OPEN = {Status.AVAILABLE, Status.WAITLIST}


@dataclass
class Change:
    kind: ChangeKind
    date: dt.date
    provider: str
    status: Status
    prev_status: Optional[Status]
    miles: Optional[int] = None
    prev_miles: Optional[int] = None
    seats: Optional[int] = None
    qsuite: bool = False
    note: Optional[str] = None

    def describe(self) -> str:
        bits = [f"{self.date:%a %d %b %Y}", self.provider]
        if self.seats:
            bits.append(f"{self.seats} seat{'s' if self.seats != 1 else ''}")
        if self.miles:
            bits.append(f"{self.miles:,} pts")
        if self.status is Status.WAITLIST:
            bits.append("WAITLIST")
        bits.append("Qsuite" if self.qsuite else "Qsuite unconfirmed")
        return " · ".join(bits)


@dataclass
class Diff:
    route: Route
    cabin: Cabin
    changes: list[Change] = field(default_factory=list)

    def of(self, *kinds: ChangeKind) -> list[Change]:
        ks = set(kinds)
        return [c for c in self.changes if c.kind in ks]

    @property
    def alertable(self) -> list[Change]:
        """What is worth waking someone up for."""
        return self.of(ChangeKind.NEWLY_OPENED, ChangeKind.FIRST_SEEN)

    @property
    def is_empty(self) -> bool:
        return not self.alertable

    def summary(self) -> str:
        n = len(self.of(ChangeKind.NEWLY_OPENED))
        f = len(self.of(ChangeKind.FIRST_SEEN))
        c = len(self.of(ChangeKind.CLOSED))
        i = len(self.of(ChangeKind.IMPROVED))
        return (f"{n} newly opened, {f} first-seen, {i} improved, {c} closed")


def _index(cells: Iterable[Cell]) -> dict[tuple[dt.date, str], Cell]:
    return {(c.date, c.provider): c for c in cells}


def diff_scans(current: Iterable[Cell], previous: Iterable[Cell],
               route: Route, cabin: Cabin) -> Diff:
    """Compare two scans' cells, per (date, provider).

    Diffing per provider rather than per date is deliberate: "Alaska now shows
    this date" and "Qatar now shows this date" are different pieces of news, and
    collapsing them would hide a source coming online.
    """
    cur = _index(current)
    prev = _index(previous)
    out = Diff(route=route, cabin=cabin)

    for key, cell in sorted(cur.items()):
        date, provider = key
        before = prev.get(key)
        prev_status = before.status if before else None

        if cell.status in OPEN:
            if prev_status in DEFINITE_EMPTY:
                kind = ChangeKind.NEWLY_OPENED
            elif prev_status in OPEN:
                improved = _is_improvement(cell, before)
                if not improved:
                    continue
                kind = ChangeKind.IMPROVED
            else:
                # No prior reading, or the prior reading was unknown/error/blocked.
                kind = ChangeKind.FIRST_SEEN
            out.changes.append(Change(
                kind=kind, date=date, provider=provider, status=cell.status,
                prev_status=prev_status, miles=cell.cheapest_miles,
                prev_miles=before.cheapest_miles if before else None,
                seats=cell.max_seats, qsuite=cell.qsuite_confirmed, note=cell.note,
            ))
        elif cell.status in DEFINITE_EMPTY and prev_status in OPEN:
            out.changes.append(Change(
                kind=ChangeKind.CLOSED, date=date, provider=provider,
                status=cell.status, prev_status=prev_status,
                prev_miles=before.cheapest_miles if before else None,
            ))

    return out


def _is_improvement(cell: Cell, before: Optional[Cell]) -> bool:
    if before is None:
        return False
    m_now, m_before = cell.cheapest_miles, before.cheapest_miles
    if m_now is not None and m_before is not None and m_now < m_before:
        return True
    s_now, s_before = cell.max_seats, before.max_seats
    if s_now is not None and s_before is not None and s_now > s_before:
        return True
    # Waitlist becoming confirmed is an improvement worth hearing about.
    return before.status is Status.WAITLIST and cell.status is Status.AVAILABLE


def suppress_already_alerted(diff: Diff,
                             sent: set[tuple[dt.date, str]]) -> Diff:
    """Drop changes already pushed, so a daily cron does not repeat itself.

    Without this, a date that opens and stays open would be re-announced as
    FIRST_SEEN on every run where the previous scan happened to be blocked.
    """
    kept = [c for c in diff.changes
            if c.kind not in (ChangeKind.NEWLY_OPENED, ChangeKind.FIRST_SEEN)
            or (c.date, c.provider) not in sent]
    return Diff(route=diff.route, cabin=diff.cabin, changes=kept)
