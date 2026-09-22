import datetime as dt

import pytest

from qsuite.models import Cabin, Cell, Offer, Route, ScanResult, Status
from qsuite.store import new_scan_id


@pytest.fixture
def route() -> Route:
    return Route("YUL", "SIN")


@pytest.fixture
def cabin() -> Cabin:
    return Cabin.BUSINESS


def make_cell(d: dt.date, status: Status, provider: str = "ba", *,
              miles=None, seats=None, aircraft=None, route=None, cabin=None) -> Cell:
    offers = []
    if status in (Status.AVAILABLE, Status.WAITLIST):
        offers = [Offer(miles=miles, seats=seats, aircraft=aircraft or [],
                        qsuite=True if aircraft == ["77W"] else None)]
    return Cell(date=d, route=route or Route("YUL", "SIN"),
                cabin=cabin or Cabin.BUSINESS, provider=provider,
                status=status, offers=offers)


@pytest.fixture
def make():
    return make_cell


@pytest.fixture
def scan_result(route, cabin):
    start, end = dt.date(2026, 11, 1), dt.date(2026, 11, 30)
    cells = [make_cell(start + dt.timedelta(days=i),
                       Status.AVAILABLE if i % 7 == 0 else Status.NONE,
                       miles=80000, seats=2)
             for i in range((end - start).days + 1)]
    return ScanResult(scan_id=new_scan_id(), route=route, cabin=cabin,
                      start=start, end=end, cells=cells,
                      finished_at=dt.datetime.now(dt.timezone.utc))
