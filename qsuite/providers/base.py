"""Provider contract and the shared window-scanning loop."""

from __future__ import annotations

import abc
import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import Cabin, Cell, ProviderRun, Route, Status, WindowKind
from ..ranges import Window, plan
from ..ratelimit import Backoff, RateLimiter, gather_bounded
from .. import risk

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A window failed for an ordinary reason (network, parse)."""


class BlockedError(ProviderError):
    """A window failed because the site pushed back: CAPTCHA, 403, 429."""


@dataclass
class ProviderContext:
    """Everything a provider needs from the outside world.

    Kept as a plain object so providers stay testable: a fake context with a
    stub http client is enough to exercise parsing.
    """

    http: Any = None                     # httpx.AsyncClient, or a stub
    browser: Any = None                  # qsuite.browser.BrowserPool, lazily created
    credentials: dict[str, str] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    def credential(self, key: str) -> Optional[str]:
        v = self.credentials.get(key)
        return v or None


class Provider(abc.ABC):
    """One booking engine.

    Subclasses implement :meth:`fetch_window` for whatever the site's densest
    view is -- a month grid where one exists, a flexible-date strip otherwise,
    a single date as the last resort. Everything above that (planning, pacing,
    retries, gap filling) is handled here so every provider behaves the same.
    """

    name: str = "base"
    label: str = "Base provider"
    window_kind: WindowKind = WindowKind.DAY
    strip_width: int = 7
    default_concurrency: int = 2
    default_delay_s: float = 2.0
    requires_auth: bool = False
    #: Whether this engine can see Qatar-operated space at all. Providers that
    #: cannot are excluded from "no availability" conclusions.
    sees_qatar_metal: bool = True
    #: Set by providers that must drive a real browser. The scanner creates one
    #: pool up front and shares it, so two browser providers in the same run
    #: cannot each launch their own Chromium.
    needs_browser: bool = False

    def __init__(self, ctx: ProviderContext, *, concurrency: int | None = None,
                 delay_s: float | None = None) -> None:
        self.ctx = ctx
        profile = risk.get(self.name)
        self.concurrency = concurrency or (profile.recommended_concurrency if profile
                                           else self.default_concurrency)
        delay = delay_s if delay_s is not None else (
            profile.recommended_delay_s if profile else self.default_delay_s)
        self.limiter = RateLimiter(delay)
        self.backoff = Backoff()

    # ---- subclass surface -------------------------------------------------

    @abc.abstractmethod
    async def fetch_window(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        """Return the cells this window's single upstream request yields.

        Implementations should return one cell per date the response actually
        covered. Dates the response did not mention are filled in by
        :meth:`scan` as ``UNKNOWN``, which keeps "we did not look" distinct from
        "we looked and there was nothing".
        """

    async def preflight(self) -> None:
        """Optional: log in, warm cookies, resolve a session token."""
        return None

    async def aclose(self) -> None:
        return None

    # ---- shared machinery -------------------------------------------------

    def plan_windows(self, start: dt.date, end: dt.date) -> list[Window]:
        return plan(start, end, self.window_kind, self.strip_width)

    def blank(self, date: dt.date, route: Route, cabin: Cabin,
              status: Status = Status.UNKNOWN, note: str | None = None) -> Cell:
        return Cell(date=date, route=route, cabin=cabin, provider=self.name,
                    status=status, note=note)

    async def scan(self, start: dt.date, end: dt.date, route: Route,
                   cabin: Cabin) -> tuple[list[Cell], ProviderRun]:
        """Cover ``start..end`` in as few requests as this engine allows."""
        windows = self.plan_windows(start, end)
        run = ProviderRun(provider=self.name, window_kind=self.window_kind,
                          windows_planned=len(windows),
                          started_at=dt.datetime.now(dt.timezone.utc))
        log.info("%s: %d window(s) of kind %s for %s..%s, concurrency=%d",
                 self.name, len(windows), self.window_kind.value, start, end,
                 self.concurrency)

        try:
            await self.preflight()
        except BlockedError as exc:
            run.blocked = True
            run.errors.append(f"preflight blocked: {exc}")
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            cells = [self.blank(d, route, cabin, Status.BLOCKED, str(exc))
                     for w in windows for d in w.dates()]
            run.dates_covered = len(cells)
            return cells, run
        except Exception as exc:  # noqa: BLE001 - a failed login is not fatal to the scan
            run.errors.append(f"preflight failed: {exc}")
            log.warning("%s: preflight failed: %s", self.name, exc)

        results = await gather_bounded(
            [self._run_window(w, route, cabin, run) for w in windows],
            self.concurrency,
        )

        cells: list[Cell] = []
        for window, res in zip(windows, results):
            if isinstance(res, BaseException):
                run.errors.append(f"{window}: {res!r}")
                cells.extend(self.blank(d, route, cabin, Status.ERROR, repr(res))
                             for d in window.dates())
                continue
            cells.extend(res)

        cells = self._fill_gaps(cells, windows, route, cabin)
        run.dates_covered = len({c.date for c in cells})
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        log.info("%s: %d cell(s) from %d request(s) in %.1fs (%d error(s))",
                 self.name, len(cells), run.requests_made, run.duration_s, len(run.errors))
        return cells, run

    async def _run_window(self, window: Window, route: Route, cabin: Cabin,
                          run: ProviderRun) -> list[Cell]:
        attempt = 0
        while True:
            attempt += 1
            await self.limiter.acquire()
            try:
                run.requests_made += 1
                return await self.fetch_window(window, route, cabin)
            except BlockedError as exc:
                if attempt > self.backoff.max_attempts:
                    run.blocked = True
                    run.errors.append(f"{window}: blocked after {attempt} attempts: {exc}")
                    log.warning("%s %s: blocked, giving up", self.name, window)
                    return [self.blank(d, route, cabin, Status.BLOCKED, str(exc))
                            for d in window.dates()]
                waited = await self.backoff.sleep(attempt)
                log.warning("%s %s: blocked (%s), backing off %.1fs (attempt %d)",
                            self.name, window, exc, waited, attempt)
            except (ProviderError, asyncio.TimeoutError, OSError) as exc:
                if attempt > self.backoff.max_attempts:
                    run.errors.append(f"{window}: {exc}")
                    return [self.blank(d, route, cabin, Status.ERROR, str(exc))
                            for d in window.dates()]
                await self.backoff.sleep(attempt)

    def _fill_gaps(self, cells: list[Cell], windows: list[Window], route: Route,
                   cabin: Cabin) -> list[Cell]:
        """Make sure every planned date has exactly one cell.

        A month response that simply omits a day is telling us nothing about it,
        not that the day is empty -- so omitted days become UNKNOWN. Providers
        that mean "searched, nothing there" must say so explicitly with NONE.
        """
        seen = {c.date for c in cells}
        for w in windows:
            for d in w.dates():
                if d not in seen:
                    cells.append(self.blank(d, route, cabin, Status.UNKNOWN,
                                            "date not present in engine response"))
                    seen.add(d)
        cells.sort(key=lambda c: c.date)
        return cells
