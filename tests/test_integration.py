"""End-to-end over real HTTP against a local stub.

This is the test that would have caught the Alaska 404 as a *wiring* problem
rather than an endpoint problem: it proves a calibrated endpoint template
reaches the wire with the right URL, and that the response comes back as a
rendered calendar. Only the real airline URL is unknowable offline; everything
between config and calendar is exercised here.
"""

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from qsuite.config import Config
from qsuite.models import Status
from qsuite.report import render_html, render_terminal
from qsuite.scanner import Scanner, coverage_report

RECEIVED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        RECEIVED.append({"path": parsed.path,
                         "query": parse_qs(parsed.query),
                         "headers": dict(self.headers)})
        if parsed.path == "/blocked":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>Pardon Our Interruption</html>")
            return
        if parsed.path != "/award":
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>not found</html>")
            return
        month = parsed.query and parse_qs(parsed.query).get("month", ["2026-10"])[0]
        body = {"days": [
            {"date": f"{month}-03", "status": "available", "seats": 2, "miles": 85000,
             "segments": [{"operatingCarrier": "QR", "aircraft": "77W"},
                          {"operatingCarrier": "QR", "aircraft": "35K"}]},
            {"date": f"{month}-04", "status": "unavailable", "seats": 0},
        ]}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):  # keep pytest output clean
        return


@pytest.fixture
def stub():
    RECEIVED.clear()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def cfg_for(stub_url: str, path: str = "/award") -> Config:
    cfg = Config()
    cfg.providers = ["alaska"]
    cfg.provider_options = {"alaska": {
        "endpoint": (stub_url + path +
                     "?origin={origin}&destination={destination}&month={year}-{month02}"),
        "headers": {"X-Test": "calibrated"},
        # Pacing is configurable precisely so it can be turned off against a
        # local stub; against a real airline it only ever goes slower.
        "delay_s": 0,
        "retry_base_s": 0.001,
        "max_retries": 1,
    }}
    return cfg


async def test_calibrated_endpoint_reaches_the_wire_correctly(stub):
    async with Scanner(cfg_for(stub)) as s:
        await s.scan(start=dt.date(2026, 10, 1), end=dt.date(2026, 11, 30))

    assert len(RECEIVED) == 2, "one request per month, not per day"
    months = sorted(r["query"]["month"][0] for r in RECEIVED)
    assert months == ["2026-10", "2026-11"]
    assert RECEIVED[0]["query"]["origin"] == ["YUL"]
    assert RECEIVED[0]["query"]["destination"] == ["SIN"]


async def test_calibrated_headers_are_sent(stub):
    async with Scanner(cfg_for(stub)) as s:
        await s.scan(start=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31))
    assert RECEIVED[0]["headers"].get("X-Test") == "calibrated"


async def test_response_becomes_a_rendered_calendar(stub):
    async with Scanner(cfg_for(stub)) as s:
        result = await s.scan(start=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31))

    by = {c.date: c for c in result.cells}
    assert by[dt.date(2026, 10, 3)].status is Status.AVAILABLE
    assert by[dt.date(2026, 10, 3)].cheapest_miles == 85000
    assert by[dt.date(2026, 10, 3)].qsuite_confirmed is True
    assert by[dt.date(2026, 10, 4)].status is Status.NONE
    # Days the engine did not mention are unknown, never "nothing available".
    assert by[dt.date(2026, 10, 10)].status is Status.UNKNOWN

    assert "●" in render_terminal(result, color=False)
    assert "<!DOCTYPE html>" in render_html(result)


async def test_a_404_is_reported_as_error_not_as_no_availability(stub):
    """The exact failure a wrong endpoint produces. It must never read as empty."""
    async with Scanner(cfg_for(stub, "/wrong-path")) as s:
        result = await s.scan(start=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31))

    assert {c.status for c in result.cells} == {Status.ERROR}
    assert Status.NONE not in {c.status for c in result.cells}
    cov = coverage_report(result)["alaska"]
    assert cov["coverage_pct"] == 0.0
    assert cov["trustworthy"] is False


async def test_a_challenge_page_is_reported_as_blocked(stub):
    async with Scanner(cfg_for(stub, "/blocked")) as s:
        result = await s.scan(start=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31))
    assert {c.status for c in result.cells} == {Status.BLOCKED}
