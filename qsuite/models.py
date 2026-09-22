"""Core value types shared by providers, storage, diffing and reporting."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    """Availability verdict for one (date, route, cabin, provider) cell.

    The ordering in ``RANK`` matters: when several providers report on the same
    date we surface the best verdict, and when one provider returns several
    itineraries for a date we keep the strongest one.
    """

    AVAILABLE = "available"      # saver/classic award space confirmed bookable
    WAITLIST = "waitlist"        # some engines expose a waitlist/request state
    NONE = "none"                # searched successfully, nothing in this cabin
    UNKNOWN = "unknown"          # not searched yet, or response was unreadable
    ERROR = "error"              # request failed (network, block, parse)
    BLOCKED = "blocked"          # bot-detection: CAPTCHA, 403, rate limit


# Higher wins when merging.
RANK: dict[Status, int] = {
    Status.AVAILABLE: 5,
    Status.WAITLIST: 4,
    Status.NONE: 3,
    Status.BLOCKED: 2,
    Status.ERROR: 1,
    Status.UNKNOWN: 0,
}


def best(a: Status, b: Status) -> Status:
    return a if RANK[a] >= RANK[b] else b


class Cabin(str, Enum):
    ECONOMY = "economy"
    PREMIUM = "premium"
    BUSINESS = "business"
    FIRST = "first"


class WindowKind(str, Enum):
    """How many dates one upstream request covers."""

    MONTH = "month"    # a true month-at-a-glance calendar: ~1 request / 30 days
    STRIP = "strip"    # a flexible-date strip: ~1 request / 3-7 days
    DAY = "day"        # no calendar at all: 1 request / date


@dataclass(frozen=True)
class Route:
    origin: str
    destination: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.origin}-{self.destination}"

    @classmethod
    def parse(cls, text: str) -> "Route":
        sep = "-" if "-" in text else ("–" if "–" in text else None)
        if sep is None:
            raise ValueError(f"route must look like YUL-SIN, got {text!r}")
        o, d = text.split(sep, 1)
        o, d = o.strip().upper(), d.strip().upper()
        if len(o) != 3 or len(d) != 3:
            raise ValueError(f"route must use 3-letter airport codes, got {text!r}")
        return cls(o, d)


@dataclass
class Offer:
    """One bookable award itinerary found on a date."""

    miles: Optional[int] = None
    currency: Optional[str] = None
    taxes: Optional[float] = None
    seats: Optional[int] = None
    marketing_carriers: list[str] = field(default_factory=list)
    operating_carriers: list[str] = field(default_factory=list)
    flight_numbers: list[str] = field(default_factory=list)
    aircraft: list[str] = field(default_factory=list)
    stops: Optional[int] = None
    fare_family: Optional[str] = None       # e.g. "Classic Reward", "Reward Saver"
    qsuite: Optional[bool] = None           # None = could not be determined
    qsuite_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Cell:
    """The result for one date, from one provider."""

    date: dt.date
    route: Route
    cabin: Cabin
    provider: str
    status: Status = Status.UNKNOWN
    offers: list[Offer] = field(default_factory=list)
    note: Optional[str] = None
    observed_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def cheapest_miles(self) -> Optional[int]:
        vals = [o.miles for o in self.offers if o.miles is not None]
        return min(vals) if vals else None

    @property
    def max_seats(self) -> Optional[int]:
        vals = [o.seats for o in self.offers if o.seats is not None]
        return max(vals) if vals else None

    @property
    def qsuite_confirmed(self) -> bool:
        return any(o.qsuite is True for o in self.offers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "route": str(self.route),
            "cabin": self.cabin.value,
            "provider": self.provider,
            "status": self.status.value,
            "offers": [o.to_dict() for o in self.offers],
            "note": self.note,
            "observed_at": self.observed_at.isoformat(),
        }


@dataclass
class ProviderRun:
    """Bookkeeping for one provider's pass over the whole date range."""

    provider: str
    window_kind: WindowKind
    requests_made: int = 0
    windows_planned: int = 0
    dates_covered: int = 0
    errors: list[str] = field(default_factory=list)
    blocked: bool = False
    started_at: Optional[dt.datetime] = None
    finished_at: Optional[dt.datetime] = None

    @property
    def duration_s(self) -> float:
        if not self.started_at or not self.finished_at:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "window_kind": self.window_kind.value,
            "requests_made": self.requests_made,
            "windows_planned": self.windows_planned,
            "dates_covered": self.dates_covered,
            "errors": self.errors[:20],
            "error_count": len(self.errors),
            "blocked": self.blocked,
            "duration_s": round(self.duration_s, 2),
        }


@dataclass
class ScanResult:
    """Everything one full scan produced."""

    scan_id: str
    route: Route
    cabin: Cabin
    start: dt.date
    end: dt.date
    cells: list[Cell] = field(default_factory=list)
    runs: list[ProviderRun] = field(default_factory=list)
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    finished_at: Optional[dt.datetime] = None

    def by_date(self) -> dict[dt.date, list[Cell]]:
        out: dict[dt.date, list[Cell]] = {}
        for c in self.cells:
            out.setdefault(c.date, []).append(c)
        return out

    def available_dates(self) -> set[dt.date]:
        return {c.date for c in self.cells if c.status is Status.AVAILABLE}

    @property
    def total_requests(self) -> int:
        return sum(r.requests_made for r in self.runs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "route": str(self.route),
            "cabin": self.cabin.value,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_requests": self.total_requests,
            "runs": [r.to_dict() for r in self.runs],
            "cells": [c.to_dict() for c in self.cells],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
