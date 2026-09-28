"""Endpoint templating: a calibrated URL must reach the wire intact."""

import datetime as dt

import pytest

from qsuite.models import Route, WindowKind
from qsuite.providers._endpoint import format_endpoint, resolve
from qsuite.ranges import Window

ROUTE = Route("YUL", "SIN")
WINDOW = Window(dt.date(2026, 10, 1), dt.date(2026, 10, 31), WindowKind.MONTH)


def test_placeholders_are_substituted():
    url = format_endpoint(
        "https://x/{origin}/{destination}/{year}-{month02}", ROUTE, WINDOW, "business")
    assert url == "https://x/YUL/SIN/2026-10"


def test_legacy_format_spec_still_works():
    url = format_endpoint("https://x/{year}-{month:02d}", ROUTE, WINDOW, "business")
    assert url.endswith("2026-10")


def test_template_with_query_string_replaces_default_params():
    """A calibrated URL is authoritative — our guesses must not be appended."""
    url, params = resolve(
        "https://x/api?o={origin}&d={destination}&when={date}",
        ROUTE, WINDOW, "business", default_params={"legacy": "yes"})
    assert url == "https://x/api?o=YUL&d=SIN&when=2026-10-01"
    assert params is None


def test_template_without_query_string_keeps_default_params():
    url, params = resolve("https://x/api/{origin}", ROUTE, WINDOW, "business",
                          default_params={"cabin": "business"})
    assert params == {"cabin": "business"}


def test_unknown_placeholder_fails_loudly():
    with pytest.raises(ValueError, match="unknown placeholder"):
        format_endpoint("https://x/{nonsense}", ROUTE, WINDOW, "business")


def test_cabin_token_is_available_to_templates():
    url = format_endpoint("https://x/?c={cabin}", ROUTE, WINDOW, "BUSINESS")
    assert url.endswith("c=BUSINESS")
