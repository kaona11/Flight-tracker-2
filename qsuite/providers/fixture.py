"""Offline provider: deterministic synthetic availability.

Exists so the whole pipeline -- planning, concurrency, storage, diffing,
alerting, the calendar render -- can be run and tested end to end without
sending a single request to an airline. ``--provider fixture`` is also the
right way to demo the tool or debug a report layout.

It can also replay a captured JSON file (``fixture_file``), which is how you
regression-test a parser against a real response you saved earlier.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from ..aircraft import verdict_for_itinerary
from ..models import Cabin, Cell, Offer, Route, Status, WindowKind
from ..ranges import Window
from .base import Provider, ProviderContext


def _hash_unit(seed: str, *parts: Any) -> float:
    """Stable pseudo-random float in [0, 1) from the seed and parts."""
    h = hashlib.sha256("|".join([seed, *map(str, parts)]).encode()).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


class FixtureProvider(Provider):
    name = "fixture"
    label = "Fixture (offline synthetic data)"
    window_kind = WindowKind.MONTH
    default_concurrency = 8
    default_delay_s = 0.0
    requires_auth = False

    def __init__(self, ctx: ProviderContext, **kw: Any) -> None:
        super().__init__(ctx, **kw)
        self.seed = str(ctx.options.get("seed", "qsuite"))
        self.hit_rate = float(ctx.options.get("hit_rate", 0.12))
        self.blocked_months = set(ctx.options.get("blocked_months", []))
        path = ctx.options.get("fixture_file")
        self._replay: dict[str, dict] | None = None
        if path:
            self._replay = self._load(Path(path))

    @staticmethod
    def _load(path: Path) -> dict[str, dict]:
        data = json.loads(path.read_text())
        rows = data["cells"] if isinstance(data, dict) and "cells" in data else data
        return {r["date"]: r for r in rows}

    async def fetch_window(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        if self._replay is not None:
            return self._from_replay(window, route, cabin)
        return self._synthesise(window, route, cabin)

    def _from_replay(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        out: list[Cell] = []
        for d in window.dates():
            row = (self._replay or {}).get(d.isoformat())
            if row is None:
                continue  # base class fills it as UNKNOWN
            offers = [Offer(**o) for o in row.get("offers", [])]
            out.append(Cell(date=d, route=route, cabin=cabin, provider=self.name,
                            status=Status(row.get("status", "unknown")),
                            offers=offers, note=row.get("note")))
        return out

    def _synthesise(self, window: Window, route: Route, cabin: Cabin) -> list[Cell]:
        month_key = f"{window.start.year}-{window.start.month:02d}"
        if month_key in self.blocked_months:
            return [self.blank(d, route, cabin, Status.BLOCKED,
                               "simulated bot-detection block") for d in window.dates()]

        out: list[Cell] = []
        for d in window.dates():
            roll = _hash_unit(self.seed, route, cabin.value, d.isoformat())
            # Award space clusters: shoulder season and midweek are looser, and
            # the far end of the schedule opens up before it gets bought out.
            weekday_bonus = 0.05 if d.weekday() in (1, 2, 3) else 0.0
            horizon_bonus = 0.06 if (d - dt.date.today()).days > 240 else 0.0
            threshold = self.hit_rate + weekday_bonus + horizon_bonus

            if roll >= threshold:
                out.append(self.blank(d, route, cabin, Status.NONE))
                continue

            seats = 1 + int(_hash_unit(self.seed, "seats", d) * 4)
            equip = ["77W", "35K"] if _hash_unit(self.seed, "eq", d) > 0.25 else ["77W", "788"]
            qs, why = verdict_for_itinerary(equip)
            offer = Offer(
                miles=70000 + int(_hash_unit(self.seed, "miles", d) * 6) * 5000,
                currency="CAD",
                taxes=round(180 + _hash_unit(self.seed, "tax", d) * 120, 2),
                seats=seats,
                marketing_carriers=["QR"],
                operating_carriers=["QR", "QR"],
                flight_numbers=["QR764", "QR944"],
                aircraft=equip,
                stops=1,
                fare_family="Classic Reward",
                qsuite=qs,
                qsuite_reason=why,
            )
            status = Status.WAITLIST if _hash_unit(self.seed, "wl", d) < 0.08 else Status.AVAILABLE
            out.append(Cell(date=d, route=route, cabin=cabin, provider=self.name,
                            status=status, offers=[offer]))
        return out
