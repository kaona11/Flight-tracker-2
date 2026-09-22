"""Parser tests, driven by payloads shaped like the real engines'.

These are the tests that matter when a site changes: run them against a file
captured with ``--capture`` and they tell you whether the parser still works.
"""

import datetime as dt

import pytest

from qsuite.models import Cabin, Route, Status, WindowKind
from qsuite.providers import aa, alaska, ba_avios, qantas
from qsuite.providers.base import ProviderError
from qsuite.ranges import Window

ROUTE = Route("YUL", "SIN")
WINDOW = Window(dt.date(2026, 11, 1), dt.date(2026, 11, 30), WindowKind.MONTH)


def cells_by_date(cells):
    return {c.date: c for c in cells}


BA_PAYLOAD = {
    "calendar": {"days": [
        {"date": "2026-11-03", "status": "available", "seats": 2, "avios": 85000,
         "segments": [{"aircraft": "77W", "operatingCarrier": "QR"},
                      {"aircraft": "35K", "operatingCarrier": "QR"}]},
        {"date": "2026-11-04", "status": "unavailable", "seats": 0},
        {"date": "2026-11-05", "availability": "waitlist", "seats": 1,
         "segments": [{"aircraft": "77W", "operatingCarrier": "QR"}]},
    ]}
}


def test_ba_parses_available_waitlist_and_empty():
    by = cells_by_date(ba_avios.parse_month(BA_PAYLOAD, WINDOW, ROUTE, Cabin.BUSINESS))
    assert by[dt.date(2026, 11, 3)].status is Status.AVAILABLE
    assert by[dt.date(2026, 11, 3)].cheapest_miles == 85000
    assert by[dt.date(2026, 11, 3)].max_seats == 2
    assert by[dt.date(2026, 11, 4)].status is Status.NONE
    assert by[dt.date(2026, 11, 5)].status is Status.WAITLIST


def test_ba_infers_qsuite_from_equipment():
    by = cells_by_date(ba_avios.parse_month(BA_PAYLOAD, WINDOW, ROUTE, Cabin.BUSINESS))
    assert by[dt.date(2026, 11, 3)].qsuite_confirmed is True


def test_ba_unverified_coverage_annotates_empty_days():
    by = cells_by_date(ba_avios.parse_month(BA_PAYLOAD, WINDOW, ROUTE, Cabin.BUSINESS))
    assert "not seen here" in by[dt.date(2026, 11, 4)].note


def test_ba_verified_coverage_leaves_empty_days_unannotated():
    by = cells_by_date(ba_avios.parse_month(
        BA_PAYLOAD, WINDOW, ROUTE, Cabin.BUSINESS, coverage_verified=True))
    assert by[dt.date(2026, 11, 4)].note is None


def test_dates_outside_the_window_are_ignored():
    payload = {"days": [{"date": "2026-12-25", "status": "available", "seats": 9}]}
    assert ba_avios.parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS) == []


def test_unrecognised_schema_raises_rather_than_returning_empty():
    """Silence is the dangerous failure. A changed schema must be loud."""
    with pytest.raises(ProviderError, match="schema has probably changed"):
        ba_avios.parse_month({"totally": "different"}, WINDOW, ROUTE, Cabin.BUSINESS)


def test_non_qatar_metal_is_not_counted_as_a_hit():
    payload = {"days": [
        {"date": "2026-11-06", "status": "available", "seats": 2,
         "segments": [{"aircraft": "359", "operatingCarrier": "CX"}]},
    ]}
    cell = cells_by_date(qantas.parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS))[
        dt.date(2026, 11, 6)]
    assert cell.status is Status.NONE
    assert "not QR" in cell.note


def test_missing_carrier_info_is_kept_but_flagged_unconfirmed():
    payload = {"days": [{"date": "2026-11-07", "status": "available", "seats": 1}]}
    cell = cells_by_date(alaska.parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS))[
        dt.date(2026, 11, 7)]
    assert cell.status is Status.AVAILABLE
    assert "unconfirmed" in cell.offers[0].qsuite_reason


def test_seat_count_alone_decides_availability():
    payload = {"days": [{"date": "2026-11-08", "seats": 3, "points": 90000},
                        {"date": "2026-11-09", "seats": 0, "points": 0}]}
    by = cells_by_date(qantas.parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS))
    assert by[dt.date(2026, 11, 8)].status is Status.AVAILABLE
    assert by[dt.date(2026, 11, 9)].status is Status.NONE


def test_epoch_millis_dates_are_understood():
    ts = int(dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    payload = {"days": [{"date": ts, "status": "available", "seats": 1}]}
    by = cells_by_date(alaska.parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS))
    assert dt.date(2026, 11, 10) in by


def test_aa_blank_month_is_unknown_not_empty():
    """AA's silent block looks exactly like an empty month, so never trust it."""
    payload = {"days": [{"date": f"2026-11-{d:02d}", "status": "unavailable", "seats": 0}
                        for d in range(1, 29)]}
    cells = aa.parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS)
    assert {c.status for c in cells} == {Status.UNKNOWN}
    assert all("silent bot-block" in (c.note or "") for c in cells)


def test_aa_month_with_real_signal_keeps_its_empties():
    payload = {"days": [
        {"date": "2026-11-01", "status": "available", "seats": 2, "miles": 80000,
         "segments": [{"operatingCarrier": "QR", "aircraft": "77W"}]},
        {"date": "2026-11-02", "status": "unavailable", "seats": 0},
    ]}
    by = cells_by_date(aa.parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS))
    assert by[dt.date(2026, 11, 1)].status is Status.AVAILABLE
    assert by[dt.date(2026, 11, 2)].status is Status.NONE
