import datetime as dt

from qsuite.models import Cabin, Route, ScanResult, Status
from qsuite.store import Store, new_scan_id

ROUTE, CABIN = Route("YUL", "SIN"), Cabin.BUSINESS


def result_with(cells, start, end):
    return ScanResult(scan_id=new_scan_id(), route=ROUTE, cabin=CABIN,
                      start=start, end=end, cells=cells,
                      finished_at=dt.datetime.now(dt.timezone.utc))


def test_roundtrip_preserves_status_price_and_offers(tmp_path, make):
    store = Store(tmp_path / "t.sqlite3")
    d = dt.date(2026, 11, 3)
    r = result_with([make(d, Status.AVAILABLE, miles=85000, seats=3, aircraft=["77W"])],
                    d, d)
    store.save(r)
    back = store.cells_for_scan(r.scan_id)
    assert len(back) == 1
    assert back[0].status is Status.AVAILABLE
    assert back[0].cheapest_miles == 85000
    assert back[0].max_seats == 3
    assert back[0].qsuite_confirmed is True
    assert back[0].route == ROUTE
    store.close()


def test_previous_scan_id_skips_the_current_one(tmp_path, make):
    store = Store(tmp_path / "t.sqlite3")
    d = dt.date(2026, 11, 3)
    first = result_with([make(d, Status.NONE)], d, d)
    store.save(first)
    second = result_with([make(d, Status.AVAILABLE)], d, d)
    store.save(second)
    assert store.previous_scan_id(ROUTE, CABIN, before=second.scan_id) == first.scan_id
    store.close()


def test_no_previous_scan_returns_none(tmp_path, make):
    store = Store(tmp_path / "t.sqlite3")
    d = dt.date(2026, 11, 3)
    only = result_with([make(d, Status.NONE)], d, d)
    store.save(only)
    assert store.previous_scan_id(ROUTE, CABIN, before=only.scan_id) is None
    store.close()


def test_alert_ledger_roundtrip(tmp_path):
    store = Store(tmp_path / "t.sqlite3")
    d = dt.date(2026, 11, 3)
    assert store.already_alerted(ROUTE, CABIN) == set()
    store.mark_alerted(ROUTE, CABIN, [(d, "ba")])
    assert store.already_alerted(ROUTE, CABIN) == {(d, "ba")}
    store.mark_alerted(ROUTE, CABIN, [(d, "ba")])   # idempotent
    assert len(store.already_alerted(ROUTE, CABIN)) == 1
    store.close()


def test_prune_keeps_the_newest_scans(tmp_path, make):
    store = Store(tmp_path / "t.sqlite3")
    d = dt.date(2026, 11, 3)
    ids = []
    for _ in range(5):
        r = result_with([make(d, Status.NONE)], d, d)
        r.started_at += dt.timedelta(seconds=len(ids))
        store.save(r)
        ids.append(r.scan_id)
    assert store.prune(keep=2) == 3
    remaining = store.scan_ids(ROUTE, CABIN, limit=10)
    assert len(remaining) == 2
    assert set(remaining) == set(ids[-2:])
    store.close()


def test_saving_twice_replaces_rather_than_duplicates(tmp_path, make):
    store = Store(tmp_path / "t.sqlite3")
    d = dt.date(2026, 11, 3)
    r = result_with([make(d, Status.NONE)], d, d)
    store.save(r)
    store.save(r)
    assert len(store.cells_for_scan(r.scan_id)) == 1
    store.close()
