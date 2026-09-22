import datetime as dt

from qsuite.alerts import build_channels, build_message, dispatch, filter_changes
from qsuite.alerts.base import AlertChannel
from qsuite.alerts.webhook import WebhookChannel
from qsuite.config import AlertConfig
from qsuite.diffing import Change, ChangeKind, Diff
from qsuite.models import Status

D = dt.date(2026, 11, 3)


def change(**kw):
    base = dict(kind=ChangeKind.NEWLY_OPENED, date=D, provider="ba",
                status=Status.AVAILABLE, prev_status=Status.NONE,
                miles=80000, seats=2, qsuite=True)
    base.update(kw)
    return Change(**base)


def diff_of(*changes, route, cabin):
    return Diff(route=route, cabin=cabin, changes=list(changes))


def test_qsuite_only_filters_unconfirmed(route, cabin):
    d = diff_of(change(qsuite=True), change(qsuite=False, date=D + dt.timedelta(1)),
                route=route, cabin=cabin)
    assert len(filter_changes(d, AlertConfig())) == 2
    assert len(filter_changes(d, AlertConfig(qsuite_only=True))) == 1


def test_min_seats_filter(route, cabin):
    d = diff_of(change(seats=1), change(seats=4, date=D + dt.timedelta(1)),
                route=route, cabin=cabin)
    assert len(filter_changes(d, AlertConfig(min_seats=2))) == 1


def test_unknown_seat_count_is_not_filtered_out(route, cabin):
    """Most engines never report seat counts; dropping those would be silent loss."""
    d = diff_of(change(seats=None), route=route, cabin=cabin)
    assert len(filter_changes(d, AlertConfig(min_seats=4))) == 1


def test_max_miles_filter(route, cabin):
    d = diff_of(change(miles=70000), change(miles=140000, date=D + dt.timedelta(1)),
                route=route, cabin=cabin)
    assert len(filter_changes(d, AlertConfig(max_miles=100000))) == 1


def test_closed_changes_are_never_alerted(route, cabin):
    d = diff_of(change(kind=ChangeKind.CLOSED, status=Status.NONE),
                route=route, cabin=cabin)
    assert filter_changes(d, AlertConfig()) == []


def test_message_names_the_route_and_counts(route, cabin):
    subject, body = build_message(route, cabin, [change()])
    assert "YUL-SIN" in subject and "business" in subject
    assert "1 newly opened" in subject
    assert "03 Nov 2026" in body
    assert "confirm on the booking engine" in body


def test_dispatch_survives_a_failing_channel(route, cabin):
    class Broken(AlertChannel):
        name = "broken"

        def send(self, *a):
            raise RuntimeError("down")

    class Fine(AlertChannel):
        name = "fine"

        def __init__(self):
            self.sent = 0

        def send(self, *a):
            self.sent += 1
            return True

    fine = Fine()
    results = dispatch([Broken(), fine], route, cabin, [change()])
    assert results == {"broken": False, "fine": True}
    assert fine.sent == 1


def test_dispatch_sends_nothing_when_there_is_nothing_new(route, cabin):
    class Counting(AlertChannel):
        name = "c"

        def __init__(self):
            self.sent = 0

        def send(self, *a):
            self.sent += 1
            return True

    c = Counting()
    assert dispatch([c], route, cabin, []) == {}
    assert c.sent == 0


def test_only_configured_channels_are_built():
    assert [c.name for c in build_channels(AlertConfig())] == ["console"]
    assert [c.name for c in build_channels(AlertConfig(console=False))] == []
    names = [c.name for c in build_channels(
        AlertConfig(webhook_url="https://example.invalid/hook"))]
    assert names == ["console", "webhook"]


def test_slack_payload_shape():
    p = WebhookChannel("https://x.invalid", "slack")._payload("subj", "body", [change()])
    assert p["text"] == "subj"
    assert p["blocks"][0]["type"] == "header"


def test_discord_payload_is_truncated():
    ch = WebhookChannel("https://x.invalid", "discord")
    p = ch._payload("subj", "x" * 5000, [change()])
    assert len(p["content"]) < 2000


def test_raw_payload_is_structured():
    p = WebhookChannel("https://x.invalid", "raw")._payload("subj", "body", [change()])
    assert p["changes"][0]["date"] == "2026-11-03"
