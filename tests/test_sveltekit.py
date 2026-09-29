"""SvelteKit __data.json decoding.

Alaska (and others) now serve availability through SvelteKit data endpoints,
which return a devalue-flattened graph rather than ordinary JSON. Undecoded,
every date is an integer and the shape-driven parser finds nothing — so this
is the difference between a working provider and a silent empty calendar.
"""

import datetime as dt

from qsuite.models import Cabin, Route, Status, WindowKind
from qsuite.providers._http import normalise_payload
from qsuite.providers._sveltekit import decode, looks_like_sveltekit, unflatten
from qsuite.providers.alaska import parse_month
from qsuite.ranges import Window

ROUTE = Route("YUL", "SIN")
WINDOW = Window(dt.date(2026, 10, 1), dt.date(2026, 10, 31), WindowKind.MONTH)


def envelope(flat):
    return {"type": "data", "nodes": [None, {"type": "data", "data": flat}]}


def test_detects_the_envelope():
    assert looks_like_sveltekit(envelope([{"a": 1}, "x"])) is True
    assert looks_like_sveltekit({"days": []}) is False
    assert looks_like_sveltekit([1, 2, 3]) is False


def test_pointers_resolve_to_values():
    assert unflatten([{"a": 1}, "hello"]) == {"a": "hello"}


def test_integers_inside_structures_are_pointers_not_literals():
    """The subtle rule: an integer inside a container addresses a slot.

    So `{"seats": 2}` means "whatever is in slot 2", not the number two. A slot
    holding an integer holds it as a literal — pointers only appear inside
    containers, which is what stops the resolution recursing forever.
    """
    assert unflatten([{"seats": 2}, 99, 7]) == {"seats": 7}
    assert unflatten([{"seats": 1}, 7, 99]) == {"seats": 7}


def test_shared_values_are_reused():
    out = unflatten([[1, 1], "same"])
    assert out == ["same", "same"]


def test_cycles_do_not_hang():
    out = unflatten([{"self": 0}])
    assert out["self"] is out


def test_sentinels_become_none():
    assert unflatten([{"a": -1, "b": -2}]) == {"a": None, "b": None}


def test_date_tag_keeps_its_iso_string():
    assert unflatten([{"when": 1}, ["Date", 2], "2026-10-03"]) == {"when": "2026-10-03"}


def test_out_of_range_pointer_is_none_not_a_crash():
    assert unflatten([{"a": 99}]) == {"a": None}


def test_empty_payload_is_handled():
    assert unflatten([]) is None


def test_normalise_passes_ordinary_json_through_untouched():
    plain = {"days": [{"date": "2026-10-03", "status": "available"}]}
    assert normalise_payload(plain) is plain


def test_decoded_payload_parses_into_a_calendar():
    """The whole point: a SvelteKit response must reach the parser readable."""
    flat = [
        {"days": 1},
        [2, 6],
        {"date": 3, "seats": 4, "miles": 5, "status": 10},
        "2026-10-03", 2, 85000,
        {"date": 7, "seats": 8, "miles": 9, "status": 11},
        "2026-10-04", 0, 0,
        "available", "unavailable",
    ]
    payload = normalise_payload(envelope(flat))
    cells = {c.date: c for c in parse_month(payload, WINDOW, ROUTE, Cabin.BUSINESS)}
    assert cells[dt.date(2026, 10, 3)].status is Status.AVAILABLE
    assert cells[dt.date(2026, 10, 3)].cheapest_miles == 85000
    assert cells[dt.date(2026, 10, 3)].max_seats == 2
    assert cells[dt.date(2026, 10, 4)].status is Status.NONE


def test_nodes_without_data_are_skipped():
    payload = {"type": "data", "nodes": [None, {"type": "skip"},
                                         {"type": "data", "data": [{"a": 1}, "v"]}]}
    out = decode(payload)
    assert {"a": "v"} in out["nodes"]
