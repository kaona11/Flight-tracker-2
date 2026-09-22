
from qsuite.cli import _everything_open_as_changes
from qsuite.diffing import ChangeKind
from qsuite.models import Status


def test_baseline_changes_are_first_seen_not_newly_opened(scan_result, make):
    """With no prior scan we cannot know a date *just* opened. Say so."""
    diff = _everything_open_as_changes(scan_result)
    assert diff.changes
    assert {c.kind for c in diff.changes} == {ChangeKind.FIRST_SEEN}
    assert all(c.prev_status is None for c in diff.changes)


def test_baseline_only_includes_open_dates(scan_result):
    diff = _everything_open_as_changes(scan_result)
    open_dates = {c.date for c in scan_result.cells
                  if c.status in (Status.AVAILABLE, Status.WAITLIST)}
    assert {c.date for c in diff.changes} == open_dates


def test_baseline_carries_price_and_seats(scan_result):
    diff = _everything_open_as_changes(scan_result)
    assert all(c.miles == 80000 for c in diff.changes)
    assert all(c.seats == 2 for c in diff.changes)
