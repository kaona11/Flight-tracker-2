"""Calibration: a request copied from the browser becomes working config."""

import datetime as dt

import pytest

from qsuite.calibrate import calibrate, parse_curl, templatise

DATE = dt.date(2026, 10, 1)

CHROME_CURL = """curl 'https://www.alaskaair.com/search/api/flightresults?origin=YUL&destination=SIN&departureDate=2026-10-01&numADTs=1&awardType=MilesOnly' \\
  -H 'accept: application/json' \\
  -H 'accept-language: en-CA,en;q=0.9' \\
  -H 'cookie: sessionid=supersecret; abck=deadbeef' \\
  -H 'referer: https://www.alaskaair.com/search' \\
  -H 'sec-ch-ua: "Chromium";v="131"' """


def test_parses_a_chrome_copy_as_curl():
    cap = parse_curl(CHROME_CURL)
    assert cap.url.startswith("https://www.alaskaair.com/search/api/flightresults")
    assert cap.method == "GET"
    assert cap.headers["accept"] == "application/json"


def test_credentials_are_never_carried_into_config():
    """A captured request is full of session state. It must not reach a file."""
    cap = parse_curl(CHROME_CURL)
    assert "cookie" in cap.dropped_sensitive
    assert not any(k.lower() == "cookie" for k in cap.headers)
    assert "supersecret" not in str(cap.headers)


def test_noise_headers_are_dropped():
    cap = parse_curl(CHROME_CURL)
    assert not any(k.startswith("sec-ch-ua") for k in cap.headers)


def test_authorization_header_is_dropped():
    cap = parse_curl("curl 'https://x/y' -H 'authorization: Bearer abc123'")
    assert cap.dropped_sensitive == ["authorization"]
    assert cap.headers == {}


def test_cookie_flag_is_dropped_too():
    cap = parse_curl("curl 'https://x/y' -b 'sessionid=secret'")
    assert "cookie" in cap.dropped_sensitive


def test_route_and_date_become_placeholders():
    template, notes = templatise(
        "https://x/api?origin=YUL&destination=SIN&departureDate=2026-10-01",
        "YUL", "SIN", DATE)
    assert "{origin}" in template and "{destination}" in template
    assert "{date}" in template
    assert len(notes) == 3


def test_path_segments_are_templated():
    template, _ = templatise("https://x/api/YUL/SIN/2026-10-01", "YUL", "SIN", DATE)
    assert template == "https://x/api/{origin}/{destination}/{date}"


@pytest.mark.parametrize("raw,placeholder", [
    ("2026-10-01", "{date}"),
    ("01-10-2026", "{date_ddmmyyyy}"),
    ("10/01/2026", "{date_mmddyyyy}"),
    ("20261001", "{date_compact}"),
])
def test_common_date_formats_are_recognised(raw, placeholder):
    template, _ = templatise(f"https://x/api?d={raw}", "YUL", "SIN", DATE)
    assert placeholder in template


def test_year_month_is_recognised_for_month_view_engines():
    template, _ = templatise("https://x/api?month=2026-10", "YUL", "SIN", DATE)
    assert "{year}-{month02}" in template


def test_substitution_is_exact_match_only():
    """A parameter that merely contains the code must not be mangled."""
    template, _ = templatise(
        "https://x/api?o=YUL&label=YULSIN-route", "YUL", "SIN", DATE)
    assert "label=YULSIN-route" in template
    assert "o={origin}" in template


def test_unrelated_parameters_survive_untouched():
    template, _ = templatise(
        "https://x/api?origin=YUL&awardType=MilesOnly&numADTs=1", "YUL", "SIN", DATE)
    assert "awardType=MilesOnly" in template
    assert "numADTs=1" in template


def test_braces_are_not_percent_encoded():
    template, _ = templatise("https://x/api?o=YUL", "YUL", "SIN", DATE)
    assert "%7B" not in template and "{origin}" in template


def test_end_to_end_emits_usable_yaml():
    yaml_block, template, notes, dropped = calibrate(
        CHROME_CURL, "alaska", "YUL", "SIN", DATE)
    assert "providers:" in yaml_block
    assert "  alaska:" in yaml_block
    assert "{origin}" in yaml_block
    assert "supersecret" not in yaml_block
    assert dropped == ["cookie"]


def test_post_requests_are_flagged_not_silently_accepted():
    _, _, notes, _ = calibrate(
        "curl 'https://x/api' -X POST --data-raw '{\"a\":1}'",
        "alaska", "YUL", "SIN", DATE)
    assert any("POST" in n for n in notes)


def test_empty_input_is_rejected_clearly():
    with pytest.raises(ValueError, match="paste the copied"):
        parse_curl("   ")


def test_non_curl_input_is_rejected_clearly():
    with pytest.raises(ValueError, match="does not start with 'curl'"):
        parse_curl("wget https://x/y")


def test_missing_url_is_rejected():
    with pytest.raises(ValueError, match="no URL found"):
        parse_curl("curl -H 'accept: application/json'")
