import datetime as dt

from qsuite.diffing import ChangeKind, diff_scans, suppress_already_alerted
from qsuite.models import Status

D1 = dt.date(2026, 11, 3)
D2 = dt.date(2026, 11, 4)
D3 = dt.date(2026, 11, 5)


def test_definite_empty_to_available_is_newly_opened(make, route, cabin):
    diff = diff_scans([make(D1, Status.AVAILABLE, seats=2)],
                      [make(D1, Status.NONE)], route, cabin)
    assert [c.kind for c in diff.changes] == [ChangeKind.NEWLY_OPENED]


def test_unreadable_to_available_is_only_first_seen(make, route, cabin):
    """A blocked previous scan must not be reported as 'newly opened'.

    Otherwise every site outage turns into a flood of false 'new date!' alerts.
    """
    for prior in (Status.ERROR, Status.BLOCKED, Status.UNKNOWN):
        diff = diff_scans([make(D1, Status.AVAILABLE)],
                          [make(D1, prior)], route, cabin)
        assert [c.kind for c in diff.changes] == [ChangeKind.FIRST_SEEN], prior


def test_never_seen_before_is_first_seen(make, route, cabin):
    diff = diff_scans([make(D3, Status.AVAILABLE)], [], route, cabin)
    assert [c.kind for c in diff.changes] == [ChangeKind.FIRST_SEEN]


def test_still_available_and_unchanged_is_not_a_change(make, route, cabin):
    cell = make(D1, Status.AVAILABLE, miles=80000, seats=2)
    diff = diff_scans([cell], [make(D1, Status.AVAILABLE, miles=80000, seats=2)],
                      route, cabin)
    assert diff.changes == []


def test_cheaper_price_is_an_improvement(make, route, cabin):
    diff = diff_scans([make(D1, Status.AVAILABLE, miles=70000, seats=1)],
                      [make(D1, Status.AVAILABLE, miles=90000, seats=1)], route, cabin)
    assert [c.kind for c in diff.changes] == [ChangeKind.IMPROVED]


def test_more_seats_is_an_improvement(make, route, cabin):
    diff = diff_scans([make(D1, Status.AVAILABLE, seats=4)],
                      [make(D1, Status.AVAILABLE, seats=1)], route, cabin)
    assert [c.kind for c in diff.changes] == [ChangeKind.IMPROVED]


def test_waitlist_becoming_confirmed_is_an_improvement(make, route, cabin):
    diff = diff_scans([make(D1, Status.AVAILABLE)],
                      [make(D1, Status.WAITLIST)], route, cabin)
    assert [c.kind for c in diff.changes] == [ChangeKind.IMPROVED]


def test_space_disappearing_is_closed_and_not_alertable(make, route, cabin):
    diff = diff_scans([make(D1, Status.NONE)],
                      [make(D1, Status.AVAILABLE, seats=2)], route, cabin)
    assert [c.kind for c in diff.changes] == [ChangeKind.CLOSED]
    assert diff.alertable == []


def test_becoming_unreadable_is_not_reported_as_closed(make, route, cabin):
    """Losing visibility is not the same as losing the seat."""
    diff = diff_scans([make(D1, Status.BLOCKED)],
                      [make(D1, Status.AVAILABLE)], route, cabin)
    assert diff.changes == []


def test_providers_are_tracked_separately(make, route, cabin):
    diff = diff_scans(
        [make(D1, Status.AVAILABLE, "ba"), make(D1, Status.AVAILABLE, "alaska")],
        [make(D1, Status.NONE, "ba"), make(D1, Status.NONE, "alaska")],
        route, cabin)
    assert {c.provider for c in diff.changes} == {"ba", "alaska"}


def test_suppression_drops_already_alerted_pairs(make, route, cabin):
    diff = diff_scans([make(D1, Status.AVAILABLE), make(D2, Status.AVAILABLE)],
                      [make(D1, Status.NONE), make(D2, Status.NONE)], route, cabin)
    assert len(diff.alertable) == 2
    pruned = suppress_already_alerted(diff, {(D1, "ba")})
    assert [c.date for c in pruned.alertable] == [D2]


def test_suppression_keeps_non_alert_kinds(make, route, cabin):
    diff = diff_scans([make(D1, Status.NONE)],
                      [make(D1, Status.AVAILABLE)], route, cabin)
    pruned = suppress_already_alerted(diff, {(D1, "ba")})
    assert [c.kind for c in pruned.changes] == [ChangeKind.CLOSED]
