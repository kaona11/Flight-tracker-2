import pytest

from qsuite.providers._http import looks_blocked

CHALLENGES = [
    (200, "<html><title>Pardon Our Interruption</title></html>"),
    (200, "Access Denied. Reference #18.a4c2d17.1730000000.deadbeef"),
    (200, "<div>Please complete the CAPTCHA to continue</div>"),
    (200, "Incapsula incident ID: 1234"),
    (200, "We detected unusual traffic from your network"),
    (403, "forbidden"),
    (429, "too many requests"),
    (503, "service unavailable"),
]


@pytest.mark.parametrize("status,body", CHALLENGES)
def test_challenges_are_detected(status, body):
    assert looks_blocked(status, body) is not None


@pytest.mark.parametrize("status,body", [
    (200, '{"days": [{"date": "2026-11-01", "seats": 2}]}'),
    (200, "<html><body>Reward Flight Finder</body></html>"),
    (404, "not found"),
])
def test_ordinary_responses_are_not_flagged(status, body):
    assert looks_blocked(status, body) is None


def test_detection_only_scans_the_head_of_a_large_body():
    """A legitimate payload that merely mentions 'captcha' deep inside is fine."""
    body = '{"ok": true}' + ("x" * 20000) + "captcha"
    assert looks_blocked(200, body) is None
