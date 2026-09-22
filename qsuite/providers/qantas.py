"""Qantas Classic Rewards — the strongest non-BA month view.

Qantas exposes Classic Reward seats on oneworld partners including Qatar, and
its redemption search has a month display that returns per-day lowest-points
cells. That makes it a month-per-request engine like BA, with meaningfully
lower bot-detection risk and no Akamai sensor payload to reproduce.

The catch is authentication: Classic Reward inventory only renders for a
logged-in Frequent Flyer account with a points balance. Without credentials the
provider still runs, but it sees the cash-fare calendar rather than award
space, which would be worse than useless — so it refuses instead, loudly.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import Cabin, Cell, Route, WindowKind
from ..ranges import Window
from ._calendar import parse_day_nodes
from ._http import get_json
from .base import Provider, ProviderContext, ProviderError

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.qantas.com/flight/rest/v1/classic-rewards/calendar"

CABIN_CODE = {
    Cabin.ECONOMY: "ECONOMY",
    Cabin.PREMIUM: "PREMIUM_ECONOMY",
    Cabin.BUSINESS: "BUSINESS",
    Cabin.FIRST: "FIRST",
}


class QantasProvider(Provider):
    name = "qantas"
    label = "Qantas Classic Rewards"
    window_kind = WindowKind.MONTH
    requires_auth = True

    def __init__(self, ctx: ProviderContext, **kw: Any) -> None:
        super().__init__(ctx, **kw)
        self.endpoint = ctx.options.get("endpoint", DEFAULT_ENDPOINT)
        self.token = ctx.credential("qantas_token")
        self.allow_anonymous = bool(ctx.options.get("allow_anonymous", False))

    async def preflight(self) -> None:
        if self.ctx.dry_run:
            return
        if not self.token and not self.allow_anonymous:
            raise ProviderError(
                "qantas: no Frequent Flyer session token configured. Classic Reward "
                "space is invisible when logged out, so scanning anonymously would "
                "report false negatives for the whole range. Set credentials.qantas_token "
                "in config, or pass providers.qantas.allow_anonymous: true if you have "
                "deliberately accepted that.")

    async def fetch_window(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        if self.ctx.dry_run:
            return []
        headers = {"Referer": "https://www.qantas.com/"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = await get_json(
            self.ctx, self.endpoint,
            params={
                "origin": route.origin,
                "destination": route.destination,
                "month": f"{window.start.year}-{window.start.month:02d}",
                "cabinClass": CABIN_CODE[cabin],
                "adults": 1,
                "fareType": "classic-reward",
            },
            headers=headers,
            capture_tag=f"qantas-{route}-{window.start:%Y-%m}",
        )
        return parse_month(payload, window, route, cabin)


def parse_month(payload: Any, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
    return parse_day_nodes(payload, window, route, cabin, "qantas",
                           fare_family="Classic Reward", require_carrier="QR")
