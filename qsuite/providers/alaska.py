"""Alaska Mileage Plan — the fast, low-risk wide net.

Alaska is the pleasant one. Its flexible-date award calendar is a first-class
part of the UI, it returns a month per request, and — the part that actually
matters — it does not need a logged-in account to show award calendars. No
login means no account-flag risk, which is the only risk in this whole tool
that can cost you a miles balance.

Use it as the first sweep across the full range, then confirm any hit on
Qatar's own engine before committing. Alaska's partner display can lag Qatar's
live inventory and it prices some partner space on its own chart.
"""

from __future__ import annotations

from typing import Any

from ..models import Cabin, Cell, Route, WindowKind
from ..ranges import Window
from ._calendar import parse_day_nodes
from ._http import get_json
from .base import Provider, ProviderContext

DEFAULT_ENDPOINT = "https://www.alaskaair.com/search/api/flexibleawardcalendar"

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
        payload = await get_json(
            self.ctx, self.endpoint,
            params={
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
            headers={"Referer": "https://www.alaskaair.com/planbook"},
            capture_tag=f"alaska-{route}-{window.start:%Y-%m}",
        )
        return parse_month(payload, window, route, cabin)


def parse_month(payload: Any, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
    return parse_day_nodes(payload, window, route, cabin, "alaska",
                           fare_family="Partner award", require_carrier="QR")
