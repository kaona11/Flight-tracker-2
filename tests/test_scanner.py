"""Scanner-level behaviour: isolation between providers, coverage honesty."""

import datetime as dt

from qsuite.config import Config
from qsuite.models import Status
from qsuite.scanner import Scanner, coverage_report

START, END = dt.date(2026, 11, 1), dt.date(2027, 1, 31)


def fixture_cfg(**opts) -> Config:
    cfg = Config()
    cfg.providers = ["fixture"]
    cfg.provider_options = {"fixture": {"seed": "test", **opts}}
    return cfg


async def test_scan_covers_every_date_once():
    async with Scanner(fixture_cfg()) as s:
        result = await s.scan(start=START, end=END)
    dates = [c.date for c in result.cells]
    assert len(dates) == len(set(dates)) == (END - START).days + 1


async def test_month_view_keeps_the_request_count_tiny():
    async with Scanner(fixture_cfg()) as s:
        result = await s.scan(start=START, end=END)
    assert result.total_requests == 3, "three months should cost three requests"


async def test_one_provider_failing_does_not_lose_the_others():
    cfg = fixture_cfg()
    cfg.providers = ["fixture", "does-not-exist"]
    async with Scanner(cfg) as s:
        result = await s.scan(start=START, end=END)
    providers = {r.provider for r in result.runs}
    assert providers == {"fixture", "does-not-exist"}
    assert any(c.provider == "fixture" for c in result.cells)
    bad = next(r for r in result.runs if r.provider == "does-not-exist")
    assert bad.errors


async def test_blocked_months_are_not_reported_as_empty():
    cfg = fixture_cfg(blocked_months=["2026-12"])
    async with Scanner(cfg) as s:
        result = await s.scan(start=START, end=END)
    december = [c for c in result.cells if c.date.month == 12]
    assert {c.status for c in december} == {Status.BLOCKED}
    assert Status.NONE not in {c.status for c in december}


async def test_partial_coverage_is_reported_as_untrustworthy():
    cfg = fixture_cfg(blocked_months=["2026-12"])
    async with Scanner(cfg) as s:
        result = await s.scan(start=START, end=END)
    cov = coverage_report(result)["fixture"]
    assert cov["trustworthy"] is False
    assert cov["blocked"] == 31


async def test_full_coverage_is_trustworthy():
    async with Scanner(fixture_cfg()) as s:
        result = await s.scan(start=START, end=END)
    assert coverage_report(result)["fixture"]["trustworthy"] is True


async def test_no_browser_pool_is_built_for_http_only_providers():
    async with Scanner(fixture_cfg()) as s:
        assert s._browser is None


async def test_browser_pool_is_built_when_a_provider_needs_one():
    cfg = fixture_cfg()
    cfg.providers = ["qatar"]
    async with Scanner(cfg) as s:
        # Built but not started: launching is deferred to preflight, so this
        # stays true whether or not chromium is installed in the test env.
        assert s._browser is not None
        assert s._browser.size >= 1
