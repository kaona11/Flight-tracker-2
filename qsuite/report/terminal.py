"""Terminal calendar: the whole range as month grids you can read at a glance.

ANSI colour where the terminal supports it, and a glyph in every cell so the
view still works piped to a file, in CI logs, or for a colourblind reader.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import shutil
import sys
from typing import Optional

from ..diffing import Diff
from ..models import ScanResult, Status
from ..scanner import coverage_report
from .common import ORDER, STATUS_STYLE, DayView, build_day_views, month_blocks, status_counts

ANSI = {
    Status.AVAILABLE: "\033[1;32m",
    Status.WAITLIST: "\033[1;33m",
    Status.NONE: "\033[2;37m",
    Status.BLOCKED: "\033[1;31m",
    Status.ERROR: "\033[0;33m",
    Status.UNKNOWN: "\033[2;37m",
}
RESET = "\033[0m"
BOLD = "\033[1m"


def _enable_windows_ansi() -> bool:
    """Ask the Windows console to interpret ANSI, and report whether it will.

    Windows Terminal handles ANSI natively, but ``powershell.exe`` in the old
    console host does not unless a process turns on virtual-terminal
    processing. Without it our colour codes print as literal ``<-[2;37m``
    text -- and since each one occupies six visible columns, they also wreck
    the calendar's alignment by pushing every line past the window width.

    So this is not merely cosmetic: if we cannot turn VT on, colour must be
    off, because garbled output is far worse than plain output.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32           # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)         # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False                            # redirected, or no console
        enable_vt = 0x0004                          # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:  # noqa: BLE001 - any failure here just means "no colour"
        return False


def _use_color(force: Optional[bool]) -> bool:
    if force is False:
        return False
    if force is None:
        if os.getenv("NO_COLOR"):
            return False
        if not sys.stdout.isatty():
            return False
    # Either auto-detection said yes or colour was asked for explicitly. Only
    # actually emit it if the terminal will render it.
    return _enable_windows_ansi()


def _months_that_fit(width: Optional[int] = None) -> int:
    """How many month grids fit side by side in the terminal.

    Each grid is GRID_W wide with a 3-space gutter between them. Guessing too
    many is the difference between a readable calendar and one that soft-wraps
    into noise, so measure rather than assume 80 columns.
    """
    if width is None:
        width = shutil.get_terminal_size(fallback=(100, 24)).columns
    fits = (width + 3) // (GRID_W + 3)
    return max(1, min(4, fits))


def render_terminal(result: ScanResult, diff: Optional[Diff] = None, *,
                    color: Optional[bool] = None,
                    months_per_row: Optional[int] = None) -> str:
    use_color = _use_color(color)
    if months_per_row is None:
        months_per_row = _months_that_fit()
    views = build_day_views(result)
    lines: list[str] = []

    def paint(text: str, status: Status) -> str:
        return f"{ANSI[status]}{text}{RESET}" if use_color else text

    header = (f"Qatar Qsuite award scan — {result.route} "
              f"{result.cabin.value} — {result.start:%d %b %Y} to {result.end:%d %b %Y}")
    lines.append(f"{BOLD if use_color else ''}{header}{RESET if use_color else ''}")
    lines.append("=" * len(header))

    counts = status_counts(views)
    total_days = (result.end - result.start).days + 1
    lines.append(
        f"{counts[Status.AVAILABLE]} date(s) with saver business space  ·  "
        f"{counts[Status.WAITLIST]} waitlist  ·  {counts[Status.NONE]} empty  ·  "
        f"{counts[Status.BLOCKED] + counts[Status.ERROR] + counts[Status.UNKNOWN]} not read"
    )
    lines.append(f"{total_days} days covered in {result.total_requests} upstream request(s) "
                 f"across {len(result.runs)} engine(s)")
    lines.append("")

    blocks = month_blocks(result.start, result.end)
    for i in range(0, len(blocks), months_per_row):
        chunk = blocks[i:i + months_per_row]
        grids = [_month_grid(y, m, views, result, paint) for y, m in chunk]
        height = max(len(g) for g in grids)
        for g in grids:
            g.extend([" " * GRID_W] * (height - len(g)))
        for row in zip(*grids):
            lines.append("   ".join(row))
        lines.append("")

    lines.append(_legend(use_color))
    lines.append("")
    lines.extend(_coverage_lines(result))

    if diff is not None:
        lines.append("")
        lines.extend(_diff_lines(diff, use_color))

    return "\n".join(lines)


#: Cells are 3 characters ("<glyph><2-digit day>") so a marked date still shows
#: its full day number. Everything -- header, padding, title -- derives from this.
CELL_W = 3
DAY_HEADS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GRID_W = len(DAY_HEADS) * CELL_W + (len(DAY_HEADS) - 1)


def _month_grid(year: int, month: int, views: dict[dt.date, DayView],
                result: ScanResult, paint) -> list[str]:
    title = f"{calendar.month_name[month]} {year}"
    out = [title.center(GRID_W), " ".join(DAY_HEADS)]
    blank = " " * CELL_W
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells: list[str] = []
        for d in week:
            v = views.get(d) if d.month == month else None
            if v is None or not (result.start <= d <= result.end):
                cells.append(blank)
                continue
            # Day number alone where there is nothing, glyph + day where there is
            # something: the eye lands on the marks, not on 330 numbers.
            if v.status in (Status.NONE, Status.UNKNOWN):
                token = f" {d.day:2d}"
            else:
                token = f"{STATUS_STYLE[v.status]['glyph']}{d.day:2d}"
            cells.append(paint(token, v.status))
        out.append(" ".join(cells))
    return out


def _legend(use_color: bool) -> str:
    parts = []
    for s in ORDER:
        st = STATUS_STYLE[s]
        chunk = f"{st['glyph']} {st['label']}"
        parts.append(f"{ANSI[s]}{chunk}{RESET}" if use_color else chunk)
    return "Legend:  " + "   ".join(parts)


def _coverage_lines(result: ScanResult) -> list[str]:
    cov = coverage_report(result)
    out = ["Source coverage (how much of the range each engine could actually read):"]
    for name, c in sorted(cov.items()):
        flag = "ok " if c["trustworthy"] else "!! "
        out.append(f"  {flag}{name:<8} {c['coverage_pct']:>5.1f}% read  "
                   f"{c['requests']:>3} req  {c['duration_s']:>6.1f}s  "
                   f"blocked={c['blocked']} err={c['errored']} unknown={c['unknown']}")
    if any(not c["trustworthy"] for c in cov.values()):
        out.append("  !! = this engine did not read the whole range, so an empty cell "
                   "from it means 'not seen', not 'not available'.")
    return out


def _diff_lines(diff: Diff, use_color: bool) -> list[str]:
    out = [f"Changes since the previous scan: {diff.summary()}"]
    alertable = diff.alertable
    if not alertable:
        out.append("  (nothing new)")
        return out
    for c in alertable:
        prefix = "NEW " if c.kind.value == "newly_opened" else "1st "
        line = f"  {prefix}{c.describe()}"
        out.append(f"\033[1;32m{line}{RESET}" if use_color else line)
    return out
