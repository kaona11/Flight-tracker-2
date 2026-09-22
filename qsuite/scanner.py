"""The orchestrator: run every provider over the range, concurrently.

Two layers of parallelism, because they solve different problems:

* **Across providers** — Alaska, BA and Qantas have nothing to do with each
  other, so they run at the same time. Three engines is three times the
  coverage for the wall-clock cost of the slowest one.
* **Within a provider** — each provider runs its own windows concurrently under
  its own semaphore and its own rate limiter, tuned to how much that site
  tolerates. Alaska gets 3 in flight; Qatar gets 1 and an 8-second gap.

The result is that a 330-day scan across the three month-view engines is ~36
requests total and finishes in under a minute, versus ~990 sequential requests
and several hours for the naive day-by-day approach.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional

from .config import Config
from .models import Cabin, Cell, ProviderRun, Route, ScanResult, Status
from .providers import ProviderContext, build
from .ranges import resolve_range
from .store import new_scan_id

log = logging.getLogger(__name__)


class Scanner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._http = None
        self._browser = None

    async def __aenter__(self) -> "Scanner":
        if not self.cfg.dry_run:
            self._http = _make_http_client(self.cfg)
            self._browser = self._make_browser_pool()
        return self

    def _make_browser_pool(self):
        """One pool for the whole run, built only if a provider actually needs it.

        Owning it here rather than in a provider means two browser-backed
        providers share one Chromium instead of launching one each, and the
        teardown has a single obvious home.
        """
        from .providers import REGISTRY
        needy = [n for n in self.cfg.providers
                 if getattr(REGISTRY.get(n), "needs_browser", False)]
        if not needy:
            return None
        from .browser import BrowserPool
        # Size to the most permissive needy provider, but never above the
        # global cap -- each context is a real browser tab's worth of memory.
        size = max((riskmod_concurrency(n) for n in needy), default=1)
        log.info("browser pool needed by %s: %d context(s)", ", ".join(needy), size)
        opts = self.cfg.options_for(needy[0])
        return BrowserPool(
            size=min(size, self.cfg.global_concurrency),
            headless=self.cfg.headless,
            storage_state=opts.get("storage_state"),
            proxy=self.cfg.proxy,
            slow_mo_ms=int(opts.get("slow_mo_ms", 120)),
        )

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._browser is not None:
            await self._browser.aclose()
            self._browser = None

    async def scan(self, route: Optional[Route] = None, cabin: Optional[Cabin] = None,
                   start: Optional[dt.date] = None,
                   end: Optional[dt.date] = None) -> ScanResult:
        route = route or self.cfg.route
        cabin = cabin or self.cfg.cabin
        if start is None or end is None:
            start, end = resolve_range(self.cfg.start, self.cfg.end, self.cfg.days)

        result = ScanResult(scan_id=new_scan_id(), route=route, cabin=cabin,
                            start=start, end=end)

        gate = asyncio.Semaphore(max(1, self.cfg.global_concurrency))
        tasks = [self._run_provider(name, route, cabin, start, end, gate)
                 for name in self.cfg.providers]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for name, outcome in zip(self.cfg.providers, outcomes):
            if isinstance(outcome, BaseException):
                # One provider blowing up must never lose the others' results.
                log.error("provider %s failed entirely: %r", name, outcome)
                run = ProviderRun(provider=name, window_kind=_kind_for(name))
                run.errors.append(repr(outcome))
                result.runs.append(run)
                continue
            cells, run = outcome
            result.cells.extend(cells)
            result.runs.append(run)

        result.finished_at = dt.datetime.now(dt.timezone.utc)
        log.info("scan %s: %d cells, %d requests, %.1fs", result.scan_id,
                 len(result.cells), result.total_requests,
                 (result.finished_at - result.started_at).total_seconds())
        return result

    async def _run_provider(self, name: str, route: Route, cabin: Cabin,
                            start: dt.date, end: dt.date,
                            gate: asyncio.Semaphore) -> tuple[list[Cell], ProviderRun]:
        async with gate:
            ctx = ProviderContext(
                http=self._http,
                browser=self._browser,
                credentials=self.cfg.credentials,
                options=self.cfg.options_for(name),
                dry_run=self.cfg.dry_run,
            )
            provider = build(name, ctx)
            try:
                return await provider.scan(start, end, route, cabin)
            finally:
                # The scanner owns the browser pool, so a provider's aclose only
                # releases its own resources -- it never tears the pool down.
                await provider.aclose()


def riskmod_concurrency(name: str) -> int:
    from . import risk
    profile = risk.get(name)
    return profile.recommended_concurrency if profile else 1


def _kind_for(name: str):
    from .models import WindowKind
    from .providers import REGISTRY
    cls = REGISTRY.get(name)
    return cls.window_kind if cls else WindowKind.DAY


def _make_http_client(cfg: Config):
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "httpx is required for the HTTP providers. `pip install httpx`, or run "
            "with --provider fixture / --dry-run.") from exc

    limits = httpx.Limits(max_connections=cfg.global_concurrency * 2,
                          max_keepalive_connections=cfg.global_concurrency)
    kwargs = {
        "timeout": httpx.Timeout(30.0, connect=15.0),
        "follow_redirects": True,
        "limits": limits,
        # A shared cookie jar is what makes the session-warming in BA's
        # preflight pay off across all twelve month calls.
        "cookies": httpx.Cookies(),
    }
    if cfg.proxy:
        kwargs["proxy"] = cfg.proxy.get("server")
    return httpx.AsyncClient(**kwargs)


def coverage_report(result: ScanResult) -> dict[str, dict]:
    """Per-provider honesty check: how much of the range did we actually see?

    A provider that was blocked for half the range should not have its silence
    read as "no availability", and this is what the report renders that from.
    """
    total_days = (result.end - result.start).days + 1
    out: dict[str, dict] = {}
    for run in result.runs:
        cells = [c for c in result.cells if c.provider == run.provider]
        readable = sum(1 for c in cells if c.status in
                       (Status.AVAILABLE, Status.WAITLIST, Status.NONE))
        blocked = sum(1 for c in cells if c.status is Status.BLOCKED)
        errored = sum(1 for c in cells if c.status is Status.ERROR)
        unknown = sum(1 for c in cells if c.status is Status.UNKNOWN)
        out[run.provider] = {
            "days_in_range": total_days,
            "days_read": readable,
            "coverage_pct": round(100.0 * readable / total_days, 1) if total_days else 0.0,
            "blocked": blocked,
            "errored": errored,
            "unknown": unknown,
            "requests": run.requests_made,
            "duration_s": round(run.duration_s, 1),
            # "Trustworthy" is strict on purpose: a provider that lost even a
            # handful of days to a block must not have its silence counted as
            # evidence of no availability.
            "trustworthy": (readable >= total_days * 0.95 and blocked == 0
                            and not run.blocked),
        }
    return out
