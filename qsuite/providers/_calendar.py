"""Generic month-calendar parsing shared by the month-view engines.

Every one of BA, Qantas, Alaska and AA returns the same *idea* -- a list of
days, each with an availability signal and usually a points figure -- wrapped
in a different, frequently-renamed envelope. Rather than hard-coding four
brittle JSON paths, this walks a payload looking for that shape.

The trade-off is deliberate: shape-matching occasionally picks up a node we did
not mean, so every field extraction is defensive and anything ambiguous lands
as ``UNKNOWN`` rather than being guessed into ``NONE``. Reporting "we could not
tell" is cheap; reporting "nothing available" when the parser simply missed it
means a missed seat.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Iterable, Optional

from ..aircraft import verdict_for_itinerary
from ..models import Cabin, Cell, Offer, Route, Status
from ..ranges import Window
from ._http import first, walk
from .base import ProviderError

DATE_KEYS = ("date", "departureDate", "flightDate", "day", "travelDate", "departureDateTime")
AVAIL_KEYS = ("availability", "status", "seats", "available", "cabins", "points",
              "avios", "miles", "lowestPoints", "classicRewardAvailability", "fares")
POINTS_KEYS = ("avios", "points", "miles", "pointsRequired", "lowestPoints",
               "amount", "milesAmount", "award")
SEAT_KEYS = ("seats", "availableSeats", "numberOfSeats", "quantity", "seatsRemaining")
STATUS_KEYS = ("status", "availability", "state", "availabilityStatus")

AVAILABLE_TOKENS = {"available", "a", "yes", "true", "green", "saver", "classic",
                    "classicreward", "available_saver", "sale", "open"}
WAITLIST_TOKENS = {"waitlist", "wl", "request", "amber", "oncall", "on_request"}
NONE_TOKENS = {"unavailable", "none", "no", "false", "red", "sold", "soldout",
               "sold_out", "closed", "notavailable", "not_available", "n"}


def is_day_node(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    return (any(k in node for k in DATE_KEYS)
            and any(k in node for k in AVAIL_KEYS))


def node_date(node: dict) -> Optional[dt.date]:
    raw = first(node, *DATE_KEYS)
    if raw is None:
        return None
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    if isinstance(raw, (int, float)):
        # Epoch millis show up in a couple of these payloads.
        try:
            secs = float(raw) / (1000.0 if float(raw) > 1e11 else 1.0)
            return dt.datetime.fromtimestamp(secs, dt.timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw)
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y%m%d", "%d %b %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        digits = "".join(ch for ch in v if ch.isdigit())
        if digits:
            return int(digits)
    return None


def node_status(node: dict, seats: Optional[int]) -> Status:
    for key in STATUS_KEYS:
        raw = node.get(key)
        if isinstance(raw, str):
            token = raw.strip().lower().replace(" ", "").replace("-", "")
            if token in AVAILABLE_TOKENS:
                return Status.AVAILABLE
            if token in WAITLIST_TOKENS:
                return Status.WAITLIST
            if token in NONE_TOKENS:
                return Status.NONE
        elif isinstance(raw, bool):
            return Status.AVAILABLE if raw else Status.NONE
    if isinstance(node.get("available"), bool):
        return Status.AVAILABLE if node["available"] else Status.NONE
    if seats is not None:
        return Status.AVAILABLE if seats > 0 else Status.NONE
    return Status.UNKNOWN


def extract_equipment(node: dict) -> list[str]:
    keys = ("aircraft", "aircraftType", "equipment", "equipmentCode", "aircraftCode")
    out: list[str] = []
    for n in walk(node, lambda x: isinstance(x, dict) and any(k in x for k in keys)):
        v = first(n, *keys)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            code = first(v, "code", "type", "name", "iata")
            if isinstance(code, str):
                out.append(code)
    return out


def extract_carriers(node: dict) -> tuple[list[str], list[str]]:
    def grab(keys: Iterable[str]) -> list[str]:
        out: list[str] = []
        ks = tuple(keys)
        for n in walk(node, lambda x: isinstance(x, dict) and any(k in x for k in ks)):
            v = first(n, *ks)
            if isinstance(v, str) and 2 <= len(v) <= 3:
                out.append(v.upper())
            elif isinstance(v, dict):
                c = first(v, "code", "iata", "airlineCode")
                if isinstance(c, str):
                    out.append(c.upper())
        return out

    marketing = grab(("marketingCarrier", "marketingAirline", "marketingCarrierCode"))
    operating = grab(("operatingCarrier", "operatingAirline", "operatingCarrierCode",
                      "carrier", "airline", "airlineCode"))
    return marketing, operating


def is_flight_number(node: Any) -> bool:
    return (isinstance(node, str) and 4 <= len(node) <= 7
            and node[:2].isalpha() and node[2:].isdigit())


def parse_day_nodes(
    payload: Any,
    window: Window,
    route: Route,
    cabin: Cabin,
    provider: str,
    *,
    fare_family: str = "Saver award",
    coverage_note: Optional[str] = None,
    node_filter: Optional[Callable[[dict], bool]] = None,
    require_carrier: Optional[str] = None,
) -> list[Cell]:
    """Parse a month payload into one cell per date it covers.

    ``require_carrier`` filters to itineraries actually flown by that carrier
    where the payload says so -- the point of this tool is Qatar metal, and an
    engine cheerfully offering a Cathay or Finnair routing is not a hit.
    When the payload carries no carrier information at all we keep the node
    rather than discarding it, and the ambiguity is recorded on the offer.
    """
    nodes = [n for n in walk(payload, is_day_node) if node_filter is None or node_filter(n)]
    if not nodes:
        raise ProviderError(
            f"{provider}: no day nodes found in the calendar payload — the schema has "
            "probably changed. Re-run with --capture and inspect the saved response.")

    cells: list[Cell] = []
    seen: set[dt.date] = set()
    for node in sorted(nodes, key=lambda n: str(node_date(n))):
        d = node_date(node)
        if d is None or not (window.start <= d <= window.end) or d in seen:
            continue
        seen.add(d)

        seats = _to_int(first(node, *SEAT_KEYS))
        status = node_status(node, seats)
        points = _to_int(first(node, *POINTS_KEYS))
        marketing, operating = extract_carriers(node)

        note = coverage_note if status is Status.NONE else None
        offers: list[Offer] = []

        if status in (Status.AVAILABLE, Status.WAITLIST):
            carriers = set(operating) | set(marketing)
            if require_carrier and carriers and require_carrier.upper() not in carriers:
                # Space exists, but not on the metal we care about.
                cells.append(Cell(date=d, route=route, cabin=cabin, provider=provider,
                                  status=Status.NONE,
                                  note=f"award space found, but on {'/'.join(sorted(carriers))}, "
                                       f"not {require_carrier.upper()}"))
                continue
            equip = extract_equipment(node)
            qs, why = verdict_for_itinerary(equip)
            if require_carrier and not carriers:
                why = (why + "; engine did not report operating carrier, so QR metal "
                              "is assumed but unconfirmed")
            offers.append(Offer(
                miles=points,
                seats=seats,
                marketing_carriers=sorted(set(marketing)),
                operating_carriers=sorted(set(operating)),
                flight_numbers=sorted({f for f in walk(node, is_flight_number)})[:6],
                aircraft=equip,
                fare_family=fare_family,
                qsuite=qs,
                qsuite_reason=why,
            ))

        cells.append(Cell(date=d, route=route, cabin=cabin, provider=provider,
                          status=status, offers=offers, note=note))
    return cells
