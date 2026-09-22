"""Shared presentation logic for every renderer.

One place decides what a date's verdict *is*, so the terminal view, the HTML
heatmap and the JSON export can never disagree with each other.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from ..models import ScanResult, Status, best

#: Status palette (fixed, never themed) + the glyph that carries the same
#: meaning without color, so a cell is legible in greyscale, to a colorblind
#: reader, and in forced-colors mode.
STATUS_STYLE: dict[Status, dict[str, str]] = {
    Status.AVAILABLE: {"color": "#0ca30c", "glyph": "●", "label": "Saver space",
                       "role": "good"},
    Status.WAITLIST:  {"color": "#fab219", "glyph": "◐", "label": "Waitlist",
                       "role": "warning"},
    Status.NONE:      {"color": "",        "glyph": "·", "label": "Nothing found",
                       "role": "empty"},
    Status.BLOCKED:   {"color": "#d03b3b", "glyph": "✕", "label": "Blocked by site",
                       "role": "critical"},
    Status.ERROR:     {"color": "#ec835a", "glyph": "!", "label": "Request failed",
                       "role": "serious"},
    Status.UNKNOWN:   {"color": "",        "glyph": "?", "label": "Not seen",
                       "role": "unknown"},
}

ORDER = [Status.AVAILABLE, Status.WAITLIST, Status.NONE,
         Status.BLOCKED, Status.ERROR, Status.UNKNOWN]


@dataclass
class DayView:
    """The merged verdict for one date across every provider."""

    date: dt.date
    status: Status = Status.UNKNOWN
    providers_available: list[str] = field(default_factory=list)
    best_miles: Optional[int] = None
    best_seats: Optional[int] = None
    qsuite: bool = False
    per_provider: dict[str, Status] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def style(self) -> dict[str, str]:
        return STATUS_STYLE[self.status]

    def tooltip(self) -> str:
        bits = [f"{self.date:%a %d %b %Y}", STATUS_STYLE[self.status]["label"]]
        if self.providers_available:
            bits.append("on " + ", ".join(self.providers_available))
        if self.best_seats:
            bits.append(f"{self.best_seats} seat{'s' if self.best_seats != 1 else ''}")
        if self.best_miles:
            bits.append(f"from {self.best_miles:,} pts")
        bits.append("Qsuite confirmed" if self.qsuite else "Qsuite unconfirmed")
        per = ", ".join(f"{p}: {s.value}" for p, s in sorted(self.per_provider.items()))
        if per:
            bits.append(f"[{per}]")
        return " — ".join(bits)


def build_day_views(result: ScanResult) -> dict[dt.date, DayView]:
    """Collapse per-provider cells into one view per date."""
    views: dict[dt.date, DayView] = {}
    for cell in result.cells:
        v = views.setdefault(cell.date, DayView(date=cell.date))
        v.status = best(v.status, cell.status)
        v.per_provider[cell.provider] = cell.status
        if cell.status in (Status.AVAILABLE, Status.WAITLIST):
            v.providers_available.append(cell.provider)
            if cell.cheapest_miles is not None:
                v.best_miles = (cell.cheapest_miles if v.best_miles is None
                                else min(v.best_miles, cell.cheapest_miles))
            if cell.max_seats is not None:
                v.best_seats = (cell.max_seats if v.best_seats is None
                                else max(v.best_seats, cell.max_seats))
            v.qsuite = v.qsuite or cell.qsuite_confirmed
        if cell.note:
            v.notes.append(f"{cell.provider}: {cell.note}")
    for v in views.values():
        v.providers_available = sorted(set(v.providers_available))
    return views


def status_counts(views: dict[dt.date, DayView]) -> dict[Status, int]:
    counts = {s: 0 for s in ORDER}
    for v in views.values():
        counts[v.status] = counts.get(v.status, 0) + 1
    return counts


def month_blocks(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    """(year, month) pairs spanned by the range, in order."""
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out
