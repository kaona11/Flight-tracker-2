"""British Airways Reward Flight Finder — the primary month-view source.

Why this one first: the Reward Flight Finder is built *around* a month grid.
One request returns a whole month of per-day award availability, so a 330-day
scan costs about twelve requests instead of three hundred and thirty. Nothing
else in the set is that cheap.

Two caveats that matter more than the code:

* **Coverage.** BA has restricted which partners RFF exposes at various times.
  An engine that cannot see Qatar-operated space at all returns empty months
  that look exactly like genuine unavailability. So an unverified BA "nothing"
  is recorded as "not seen here", and the report says so.
* **Endpoint drift.** The RFF request shape changes between BA releases. The
  URL template lives in config (``providers.ba.endpoint``) rather than being
  hard-coded, and ``--capture`` saves raw responses so a broken parser can be
  re-fitted against a real payload instead of reverse-engineered from scratch.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import Cabin, Cell, Route, WindowKind
from ..ranges import Window
from ._calendar import parse_day_nodes
from ._http import get_json
from .base import BlockedError, Provider, ProviderContext

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = (
    "https://www.britishairways.com/api/rewardflightfinder/rest/v1/"
    "availability/{origin}/{destination}/{year}-{month:02d}"
)
LANDING = "https://www.britishairways.com/travel/redeem/execclub/_gf/en_gb"

CABIN_CODE = {
    Cabin.ECONOMY: "M",
    Cabin.PREMIUM: "W",
    Cabin.BUSINESS: "C",
    Cabin.FIRST: "F",
}

COVERAGE_NOTE = ("BA partner coverage for Qatar metal is unverified on this run; read "
                 "this as 'not seen here', not 'not available'")


class BritishAirwaysProvider(Provider):
    name = "ba"
    label = "British Airways Reward Flight Finder (Avios)"
    window_kind = WindowKind.MONTH
    requires_auth = True

    def __init__(self, ctx: ProviderContext, **kw: Any) -> None:
        super().__init__(ctx, **kw)
        self.endpoint = ctx.options.get("endpoint", DEFAULT_ENDPOINT)
        self.coverage_verified = bool(ctx.options.get("coverage_verified", False))

    async def preflight(self) -> None:
        """Warm cookies so the month calls are not challenged.

        RFF is session-bound: the month endpoint expects the cookie jar a real
        visit to the redemption search leaves behind. We fetch the landing page
        once and reuse the jar for every month — which is also why concurrency
        stays at 2 here. A dozen parallel calls on one freshly-minted session is
        exactly the pattern Akamai scores as automated.
        """
        if self.ctx.dry_run or self.ctx.http is None:
            return
        try:
            resp = await self.ctx.http.get(
                LANDING, headers={"Accept": "text/html,application/xhtml+xml"})
        except Exception as exc:  # noqa: BLE001 - an unwarmed session may still work
            log.warning("ba: could not warm session (%s); continuing unwarmed", exc)
            return
        if resp.status_code in (403, 429, 503):
            raise BlockedError(f"RFF landing page returned HTTP {resp.status_code}")

    async def fetch_window(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        if self.ctx.dry_run:
            return []
        url = self.endpoint.format(
            origin=route.origin, destination=route.destination,
            year=window.start.year, month=window.start.month,
        )
        payload = await get_json(
            self.ctx, url,
            params={"cabin": CABIN_CODE[cabin], "adults": 1},
            headers={"Referer": LANDING},
            capture_tag=f"ba-{route}-{window.start:%Y-%m}",
        )
        return parse_month(payload, window, route, cabin,
                           coverage_verified=self.coverage_verified)


def parse_month(payload: Any, window: Window, route: Route, cabin: Cabin, *,
                coverage_verified: bool = False) -> list[Cell]:
    """Parse an RFF month payload. Free function so tests need no HTTP."""
    return parse_day_nodes(
        payload, window, route, cabin, "ba",
        fare_family="Reward Saver",
        coverage_note=None if coverage_verified else COVERAGE_NOTE,
        require_carrier="QR",
    )
