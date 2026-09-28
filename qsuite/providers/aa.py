"""American AAdvantage — a month view you should not trust on its own.

AA has a calendar endpoint that returns a month of award pricing, which is
cheap. The problem is its failure mode: when AA decides you are automated it
frequently returns a well-formed, entirely empty result rather than an error.
A blocked scan and a genuinely empty month are byte-for-byte indistinguishable.

So this provider applies a sanity check: if an entire month comes back with no
availability *and* no pricing of any kind, it reports ``UNKNOWN`` rather than
``NONE``. A silently-degraded engine must not be allowed to vote "nothing here"
against sources that can actually see.
"""

from __future__ import annotations

from typing import Any

from ..models import Cabin, Cell, Route, Status, WindowKind
from ..ranges import Window
from ._calendar import parse_day_nodes
from ._endpoint import resolve
from ._http import get_json
from .base import Provider, ProviderContext

DEFAULT_ENDPOINT = "https://www.aa.com/booking/api/search/calendar"

CABIN_CODE = {
    Cabin.ECONOMY: "COACH",
    Cabin.PREMIUM: "PREMIUM_ECONOMY",
    Cabin.BUSINESS: "BUSINESS",
    Cabin.FIRST: "FIRST",
}

DEGRADED_NOTE = ("AA returned a completely empty month with no pricing at all, which is "
                 "also what its silent bot-block looks like — recorded as unknown, not "
                 "as 'no availability'")


class AmericanProvider(Provider):
    name = "aa"
    label = "American Airlines AAdvantage award calendar"
    window_kind = WindowKind.MONTH
    requires_auth = False

    def __init__(self, ctx: ProviderContext, **kw: Any) -> None:
        super().__init__(ctx, **kw)
        self.endpoint = ctx.options.get("endpoint", DEFAULT_ENDPOINT)

    async def fetch_window(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        if self.ctx.dry_run:
            return []
        url, params = resolve(
            self.endpoint, route, window, CABIN_CODE[cabin],
            default_params={
                "origin": route.origin,
                "destination": route.destination,
                "month": f"{window.start.year}-{window.start.month:02d}",
                "cabin": CABIN_CODE[cabin],
                "passengerCount": 1,
                "tripType": "OneWay",
                "searchType": "Award",
            },
        )
        payload = await get_json(
            self.ctx, url, params=params,
            headers={"Referer": "https://www.aa.com/booking/find-flights",
                     **self.ctx.options.get("headers", {})},
            capture_tag=f"aa-{route}-{window.start:%Y-%m}",
        )
        return parse_month(payload, window, route, cabin)


def parse_month(payload: Any, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
    cells = parse_day_nodes(payload, window, route, cabin, "aa",
                            fare_family="AAdvantage award", require_carrier="QR")
    return _guard_silent_block(cells)


def _guard_silent_block(cells: list[Cell]) -> list[Cell]:
    """Downgrade a suspiciously blank month from NONE to UNKNOWN."""
    if not cells:
        return cells
    any_signal = any(c.status in (Status.AVAILABLE, Status.WAITLIST) or c.offers
                     or c.cheapest_miles is not None for c in cells)
    if any_signal:
        return cells
    for c in cells:
        if c.status is Status.NONE:
            c.status = Status.UNKNOWN
            c.note = DEGRADED_NOTE
    return cells
