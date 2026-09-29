"""Alaska Mileage Plan — the fast, low-risk wide net.

Alaska is the pleasant one. Its flexible-date award calendar is a first-class
part of the UI, it returns a month per request, and — the part that actually
matters — it does not need a logged-in account to show award calendars. No
login means no account-flag risk, which is the only risk in this whole tool
that can cost you a miles balance.

Use it as the first sweep across the full range, then confirm any hit on
Qatar's own engine before committing. Alaska's partner display can lag Qatar's
live inventory and it prices some partner space on its own chart.

**Unverified: cabin filtering.** The captured calendar request carries no cabin
parameter at all -- it asks for ``FareType=Lowest+price+available``, which
reads like "cheapest seat in any cabin". If that is what it means, a hit here
says *something* is available on that date, not necessarily business. The
parser only counts a date when the payload itself reports business-cabin
availability, so the failure mode is a missed date rather than a false one, but
until this is confirmed against a real response, treat Alaska as a coarse first
filter and confirm the cabin elsewhere.
"""

from __future__ import annotations

from typing import Any

from ..models import Cabin, Cell, Route, WindowKind
from ..ranges import Window
from ._calendar import parse_day_nodes
from ._endpoint import resolve
from ._http import get_json
from .base import Provider, ProviderContext

#: Captured from a real browser session on 2026-09-29 (see docs/SOURCES.md).
#: Alaska is a SvelteKit app: the calendar route loads its data from a sibling
#: ``__data.json``. The parameters that matter:
#:
#:   RequestType=Calendar        the month grid rather than a single day
#:   ShoppingMethod=onlineaward  award inventory rather than cash fares
#:   CM=YYYY-MM                  the month -- this is what makes one request
#:                               cover ~30 days instead of one
#:
#: ``int`` is an analytics tag and ``x-sveltekit-invalidated`` is a SvelteKit
#: internal; both are kept exactly as the browser sent them, since a request
#: that differs from a real one is a request worth being suspicious of.
DEFAULT_ENDPOINT = (
    "https://www.alaskaair.com/search/calendar/__data.json"
    "?O={origin}&D={destination}&OD={date}&A=1&RT=false"
    "&RequestType=Calendar&ShoppingMethod=onlineaward"
    "&int=flightresultsmicrosite%3Aviewby-calendar&locale=en-us"
    "&CM={year}-{month02}&FareType=Lowest+price+available"
    "&x-sveltekit-invalidated=01"
)

CABIN_CODE = {
    Cabin.ECONOMY: "coach",
    Cabin.PREMIUM: "premium",
    Cabin.BUSINESS: "business",
    Cabin.FIRST: "first",
}


class AlaskaProvider(Provider):
    name = "alaska"
    label = "Alaska Airlines Mileage Plan award calendar"
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
                "O": route.origin,
                "D": route.destination,
                "OD": window.start.isoformat(),
                "A": 1,
                "C": 0,
                "L": 0,
                "RT": "false",
                "ShoppingMethod": "onlineaward",
                "CabinClass": CABIN_CODE[cabin],
            },
        )
        payload = await get_json(
            self.ctx, url, params=params,
            headers={"Referer": "https://www.alaskaair.com/planbook",
                     **self.ctx.options.get("headers", {})},
            capture_tag=f"alaska-{route}-{window.start:%Y-%m}",
        )
        return parse_month(payload, window, route, cabin)


def parse_month(payload: Any, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
    return parse_day_nodes(payload, window, route, cabin, "alaska",
                           fare_family="Partner award", require_carrier="QR")
