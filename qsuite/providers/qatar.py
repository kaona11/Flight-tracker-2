"""Qatar Airways Privilege Club — the authoritative source, and the hard one.

Qatar's own engine is the only one that definitively knows what Qatar is
selling, and it is also the most defended site in the set: Akamai Bot Manager
with device and TLS fingerprinting plus a sensor payload that plain HTTP cannot
reproduce. So this provider drives a real browser.

It also has no month grid. The flexible-date display is a short strip around
the searched date, so the range costs one request per strip (~48 for 330 days)
rather than one per month. That is still 7x better than day-by-day, and the
strips are run concurrently across the browser pool, which is requirement 3.

**Use it as a confirmation engine, not the primary sweep.** Sweep the range
with the month-view sites, then point this at the handful of dates they
flagged. That keeps your request count — and your account-flag risk — an order
of magnitude lower than scanning 330 days here.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any, Optional

from ..aircraft import verdict_for_itinerary
from ..browser import BrowserPool, BrowserUnavailable, human_pause
from ..models import RANK, Cabin, Cell, Offer, Route, Status, WindowKind
from ..ranges import Window, anchor
from ._calendar import parse_day_nodes
from .base import BlockedError, Provider, ProviderContext, ProviderError

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.qatarairways.com/en/homepage.html"

BOOKING_URL = (
    "https://booking.qatarairways.com/nsp/views/showBooking.action"
    "?widget=QR&searchType=F&addTaxToFare=Y&minPurTime=0&upsellCallId=0"
    "&bookingClass=B&tripType=O&fromStation={origin}&toStation={destination}"
    "&departingDate={date}&adults=1&children=0&infants=0&teenager=0"
    "&ofw=0&flexibleDate=Y&selLang=en&awardBooking=true"
)

CABIN_CODE = {
    Cabin.ECONOMY: "E",
    Cabin.PREMIUM: "P",
    Cabin.BUSINESS: "B",
    Cabin.FIRST: "F",
}

#: Strings Qatar's flow shows when it has decided you are a bot.
_CHALLENGE_MARKERS = (
    "access denied", "reference #", "unusual activity", "captcha",
    "verify you are human", "请稍候", "bot detection",
)


class QatarProvider(Provider):
    name = "qatar"
    label = "Qatar Airways Privilege Club award search"
    window_kind = WindowKind.STRIP
    strip_width = 7
    requires_auth = True
    needs_browser = True

    def __init__(self, ctx: ProviderContext, **kw: Any) -> None:
        super().__init__(ctx, **kw)
        self.strip_width = int(ctx.options.get("strip_width", 7))
        self.timeout_ms = int(ctx.options.get("timeout_ms", 45_000))
        self._pool: Optional[BrowserPool] = None

    async def preflight(self) -> None:
        if self.ctx.dry_run:
            return
        # The pool belongs to the scanner, which builds it before any provider
        # runs. Creating one here would mean a second Chromium per provider.
        self._pool = self.ctx.browser
        if self._pool is None:
            raise ProviderError(
                "qatar needs a browser pool but none was provided. Install the "
                "browser extra (`pip install 'qsuite-scanner[browser]'` then "
                "`playwright install chromium`), or scan the HTTP engines only "
                "with --provider alaska,ba,qantas.")
        try:
            await self._pool.start()
        except BrowserUnavailable as exc:
            raise ProviderError(str(exc)) from exc

    async def aclose(self) -> None:
        """Persist the session if asked, so a login survives to the next run."""
        state = self.ctx.options.get("storage_state")
        if state and self._pool is not None:
            try:
                await self._pool.save_state(state)
            except Exception as exc:  # noqa: BLE001 - losing a cookie jar is not fatal
                log.warning("qatar: could not save browser session to %s: %s",
                            state, exc)
        self._pool = None

    async def fetch_window(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        """Drive one flexible-date strip centred on the window."""
        if self.ctx.dry_run:
            return []
        if self._pool is None:
            # Preflight failed (usually no browser installed). Say so, so these
            # dates are recorded as an error with a real reason rather than as
            # a silent "engine did not mention this date".
            raise ProviderError(
                "no browser session available — preflight failed, see the log above")

        target = anchor(window)
        url = BOOKING_URL.format(origin=route.origin, destination=route.destination,
                                 date=target.strftime("%d-%m-%Y"))
        captured: list[Any] = []

        async with self._pool.page() as page:
            # Qatar renders the calendar client-side from XHR, so we read the
            # JSON off the wire rather than scraping a DOM that changes weekly.
            async def on_response(resp):
                try:
                    ct = (resp.headers or {}).get("content-type", "")
                    if "json" not in ct.lower():
                        return
                    if not re.search(r"(calendar|availab|lowfare|flexible|matrix)",
                                     resp.url, re.I):
                        return
                    captured.append(await resp.json())
                except Exception:  # noqa: BLE001 - a body we cannot read is not fatal
                    return

            page.on("response", on_response)
            try:
                await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"navigation failed: {exc}") from exc

            await human_pause(1.0, 2.5)
            body = ((await page.content()) or "").lower()
            for marker in _CHALLENGE_MARKERS:
                if marker in body:
                    raise BlockedError(f"Qatar served a challenge page (matched {marker!r})")

            # Give the XHRs a moment to land before reading what we captured.
            for _ in range(6):
                if captured:
                    break
                await human_pause(0.8, 1.4)

            if self.ctx.options.get("capture_dir") and captured:
                self._dump(captured, window, route)

            if not captured:
                dom_cells = parse_dom_strip(await page.content(), window, route, cabin)
                if dom_cells:
                    return dom_cells
                raise ProviderError(
                    "no availability XHR captured and no DOM strip parsed — Qatar has "
                    "likely changed its booking flow. Re-run with --capture and "
                    "--no-headless to watch what the page actually does.")

        return parse_strip(captured, window, route, cabin)

    def _dump(self, captured: list[Any], window: Window, route: Route) -> None:
        from pathlib import Path
        d = Path(self.ctx.options["capture_dir"])
        d.mkdir(parents=True, exist_ok=True)
        (d / f"qatar-{route}-{window.start:%Y-%m-%d}.json").write_text(
            json.dumps(captured, indent=2)[:2_000_000])


def parse_strip(payloads: list[Any], window: Window, route: Route,
                cabin: Cabin) -> list[Cell]:
    """Merge every captured XHR into one cell per date in the strip."""
    cells: dict[dt.date, Cell] = {}
    for payload in payloads:
        try:
            parsed = parse_day_nodes(payload, window, route, cabin, "qatar",
                                     fare_family="Privilege Club award",
                                     require_carrier="QR")
        except ProviderError:
            continue
        for c in parsed:
            prior = cells.get(c.date)
            if prior is None or RANK[c.status] > RANK[prior.status]:
                cells[c.date] = c
    return list(cells.values())


_DOM_DAY = re.compile(
    r'data-date="(?P<date>\d{4}-\d{2}-\d{2})"[^>]*'
    r'(?:data-avios="(?P<avios>\d+)")?[^>]*'
    r'class="[^"]*(?P<state>available|unavailable|sold-?out|waitlist)[^"]*"',
    re.I,
)


def parse_dom_strip(html: str, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
    """Last-resort DOM scrape of the flexible-date strip.

    Only used when no JSON was seen on the wire. Intentionally narrow: if the
    markup has moved, this returns nothing and the caller raises a clear error,
    which is better than silently reporting an empty week.
    """
    out: list[Cell] = []
    for m in _DOM_DAY.finditer(html or ""):
        try:
            d = dt.date.fromisoformat(m.group("date"))
        except ValueError:
            continue
        if not (window.start <= d <= window.end):
            continue
        state = (m.group("state") or "").lower()
        if state == "available":
            status = Status.AVAILABLE
        elif state == "waitlist":
            status = Status.WAITLIST
        else:
            status = Status.NONE
        offers: list[Offer] = []
        if status in (Status.AVAILABLE, Status.WAITLIST):
            qs, why = verdict_for_itinerary([])
            offers.append(Offer(
                miles=int(m.group("avios")) if m.group("avios") else None,
                fare_family="Privilege Club award",
                marketing_carriers=["QR"],
                qsuite=qs,
                qsuite_reason=why + " (DOM fallback: no equipment in the strip markup)",
            ))
        out.append(Cell(date=d, route=route, cabin=cabin, provider="qatar",
                        status=status, offers=offers))
    return out
