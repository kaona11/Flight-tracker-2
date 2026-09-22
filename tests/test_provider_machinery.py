"""The shared scanning loop: gap filling, backoff, blocking, concurrency."""

import asyncio
import datetime as dt


from qsuite.models import Cabin, Route, Status, WindowKind
from qsuite.providers.base import BlockedError, Provider, ProviderContext, ProviderError
from qsuite.ranges import Window

ROUTE = Route("YUL", "SIN")
START, END = dt.date(2026, 11, 1), dt.date(2027, 1, 31)


class Recording(Provider):
    name = "fixture"          # borrow the fixture risk profile: no delays in tests
    window_kind = WindowKind.MONTH

    def __init__(self, ctx, behaviour=None, **kw):
        super().__init__(ctx, **kw)
        self.behaviour = behaviour or (lambda w: "ok")
        self.calls: list[Window] = []
        self.in_flight = 0
        self.peak = 0

    async def fetch_window(self, window, route, cabin):
        self.calls.append(window)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            what = self.behaviour(window)
            if what == "blocked":
                raise BlockedError("simulated challenge")
            if what == "error":
                raise ProviderError("simulated failure")
            if what == "partial":
                d = window.start
                return [self.blank(d, route, cabin, Status.AVAILABLE)]
            return [self.blank(d, route, cabin, Status.NONE) for d in window.dates()]
        finally:
            self.in_flight -= 1


def ctx():
    return ProviderContext(options={})


async def test_every_date_gets_exactly_one_cell():
    p = Recording(ctx())
    cells, run = await p.scan(START, END, ROUTE, Cabin.BUSINESS)
    dates = [c.date for c in cells]
    assert len(dates) == len(set(dates)) == (END - START).days + 1
    assert run.requests_made == 3


async def test_dates_missing_from_a_response_become_unknown_not_empty():
    """The distinction the whole tool rests on: unsaid is not the same as no."""
    p = Recording(ctx(), behaviour=lambda w: "partial")
    cells, _ = await p.scan(START, END, ROUTE, Cabin.BUSINESS)
    by = {c.date: c for c in cells}
    assert by[START].status is Status.AVAILABLE
    assert by[START + dt.timedelta(days=1)].status is Status.UNKNOWN
    assert "not present" in by[START + dt.timedelta(days=1)].note


async def test_blocked_window_retries_then_marks_dates_blocked():
    p = Recording(ctx(), behaviour=lambda w: "blocked")
    p.backoff.base = 0.001
    p.backoff.max_attempts = 2
    cells, run = await p.scan(START, END, ROUTE, Cabin.BUSINESS)
    assert run.blocked is True
    assert {c.status for c in cells} == {Status.BLOCKED}
    # 3 windows, each tried max_attempts + 1 times.
    assert run.requests_made == 9


async def test_one_bad_window_does_not_lose_the_good_ones():
    bad = dt.date(2026, 12, 1)
    p = Recording(ctx(), behaviour=lambda w: "error" if w.start == bad else "ok")
    p.backoff.base = 0.001
    p.backoff.max_attempts = 1
    cells, run = await p.scan(START, END, ROUTE, Cabin.BUSINESS)
    by = {c.date: c for c in cells}
    assert by[bad].status is Status.ERROR
    assert by[START].status is Status.NONE
    assert len(run.errors) == 1


async def test_concurrency_is_bounded_by_the_provider_limit():
    p = Recording(ctx(), concurrency=2)
    await p.scan(START, dt.date(2027, 6, 30), ROUTE, Cabin.BUSINESS)
    assert p.peak <= 2


async def test_windows_run_in_parallel_not_sequentially():
    p = Recording(ctx(), concurrency=4)
    await p.scan(START, dt.date(2027, 6, 30), ROUTE, Cabin.BUSINESS)
    assert p.peak > 1, "windows should overlap; sequential scanning is the bug"


async def test_preflight_block_short_circuits_the_whole_range():
    class Refusing(Recording):
        async def preflight(self):
            raise BlockedError("login challenged")

    p = Refusing(ctx())
    cells, run = await p.scan(START, END, ROUTE, Cabin.BUSINESS)
    assert run.blocked is True
    assert run.requests_made == 0
    assert {c.status for c in cells} == {Status.BLOCKED}


async def test_preflight_soft_failure_still_scans():
    class Flaky(Recording):
        async def preflight(self):
            raise RuntimeError("cookie warm failed")

    p = Flaky(ctx())
    cells, run = await p.scan(START, END, ROUTE, Cabin.BUSINESS)
    assert run.requests_made == 3
    assert any("preflight failed" in e for e in run.errors)
    assert {c.status for c in cells} == {Status.NONE}
