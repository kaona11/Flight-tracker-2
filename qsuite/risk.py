"""Per-source bot-detection risk and calendar-view feasibility registry.

This is the honest-caveats layer. Every entry says three things:

1. What protection the site runs, and therefore what will break your scan.
2. Whether the site really has a month/calendar view we can drive in one
   request, or whether we are stuck querying date-by-date.
3. What you personally risk -- account flags matter far more than IP blocks,
   because a flagged frequent-flyer account can lose its balance.

The verdicts reflect what these engines looked like when this was written.
Airlines change protection vendors and redesign booking flows without notice,
so treat every entry as a starting hypothesis and re-check with
``qsuite risk --verify`` behaviour in mind: if a provider starts returning
BLOCKED, its entry here is out of date.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SEVERE = "severe"


class CalendarSupport(str, Enum):
    NATIVE_MONTH = "native-month"     # a real month grid in one request
    PARTIAL_STRIP = "partial-strip"   # +/- N days around an anchor date
    NONE = "none"                     # one request per date, no way around it


@dataclass
class RiskProfile:
    provider: str
    label: str
    protection: str
    calendar: CalendarSupport
    window_days: int
    requests_for_330d: int
    automatable: str
    block_risk: RiskLevel
    captcha_risk: RiskLevel
    account_risk: RiskLevel
    needs_auth: bool
    recommended_concurrency: int
    recommended_delay_s: float
    notes: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["calendar"] = self.calendar.value
        for k in ("block_risk", "captcha_risk", "account_risk"):
            d[k] = getattr(self, k).value
        return d


PROFILES: dict[str, RiskProfile] = {
    "ba": RiskProfile(
        provider="ba",
        label="British Airways — Reward Flight Finder (Avios)",
        protection="Akamai Bot Manager on the booking path; the Reward Flight Finder "
                   "month endpoint is comparatively lightly defended but is session-bound.",
        calendar=CalendarSupport.NATIVE_MONTH,
        window_days=31,
        requests_for_330d=12,
        automatable="Best target. The Reward Flight Finder is built around a month-at-a-"
                    "glance grid, so one request per month covers the whole range. Drive it "
                    "with a real browser session for the first call to pick up cookies, then "
                    "reuse that session for the remaining months.",
        block_risk=RiskLevel.MEDIUM,
        captcha_risk=RiskLevel.LOW,
        account_risk=RiskLevel.MEDIUM,
        needs_auth=True,
        recommended_concurrency=2,
        recommended_delay_s=3.0,
        notes="Requires an Executive Club login for partner award space, and BA has "
              "historically restricted which partners RFF exposes. Verify that Qatar-"
              "operated space actually shows before trusting a run of empty months: an "
              "engine that cannot see QR at all looks identical to an engine that sees "
              "no availability. The scanner reports coverage separately for this reason.",
    ),
    "qantas": RiskProfile(
        provider="qantas",
        label="Qantas — Classic Reward seats",
        protection="Moderate. Cloud WAF with rate limiting; no interactive CAPTCHA on "
                   "the search path under normal pacing.",
        calendar=CalendarSupport.NATIVE_MONTH,
        window_days=31,
        requests_for_330d=12,
        automatable="Strong second target. The Classic Rewards flow has a month view that "
                    "returns per-day lowest-points cells, so the whole range is ~12 requests. "
                    "Qantas surfaces Qatar-operated space as a oneworld partner.",
        block_risk=RiskLevel.MEDIUM,
        captcha_risk=RiskLevel.LOW,
        account_risk=RiskLevel.LOW,
        needs_auth=True,
        recommended_concurrency=2,
        recommended_delay_s=2.5,
        notes="Needs a Frequent Flyer login with a non-zero points balance to see Classic "
              "Reward inventory. Qantas throttles aggressively if you hammer it; the default "
              "pacing here stays well under that.",
    ),
    "alaska": RiskProfile(
        provider="alaska",
        label="Alaska Airlines — Mileage Plan partner award search",
        protection="Moderate. Bot defences exist but the flexible-date calendar is a "
                   "first-class part of the UI and behaves under normal pacing.",
        calendar=CalendarSupport.NATIVE_MONTH,
        window_days=31,
        requests_for_330d=12,
        automatable="Good target and the most pleasant of the three month-view engines to "
                    "automate: no login is needed just to see award calendars, which removes "
                    "the account-flag risk entirely.",
        block_risk=RiskLevel.MEDIUM,
        captcha_risk=RiskLevel.MEDIUM,
        account_risk=RiskLevel.LOW,
        needs_auth=False,
        recommended_concurrency=3,
        recommended_delay_s=2.0,
        notes="Alaska's partner award display can lag Qatar's own inventory, and it prices "
              "some partner space differently. Use it as a fast wide net, then confirm any "
              "hit on Qatar's own engine before you commit.",
    ),
    "qatar": RiskProfile(
        provider="qatar",
        label="Qatar Airways — Privilege Club Avios award search",
        protection="Akamai Bot Manager, aggressive. Device fingerprinting, TLS/JA3 "
                   "fingerprinting, and sensor-data collection on the booking path.",
        calendar=CalendarSupport.PARTIAL_STRIP,
        window_days=7,
        requests_for_330d=48,
        automatable="Hardest of the set, and the one whose data you most want. There is no "
                    "true month grid -- the flexible-date display is a short strip around the "
                    "searched date -- so the range costs ~48 windowed requests even when it "
                    "works. Expect to need a real (non-headless-looking) browser, residential "
                    "egress, and slow pacing. Treat it as the confirmation engine for dates "
                    "the month-view sites already flagged, not as the primary sweep.",
        block_risk=RiskLevel.SEVERE,
        captcha_risk=RiskLevel.HIGH,
        account_risk=RiskLevel.HIGH,
        needs_auth=True,
        recommended_concurrency=1,
        recommended_delay_s=8.0,
        notes="This is the account-risk one. Qatar has form for locking Privilege Club "
              "accounts it believes are being scraped, and a locked account can mean a "
              "frozen Avios balance. Strongly prefer running this logged out where the flow "
              "permits it, or against a throwaway account, and keep concurrency at 1.",
    ),
    "aa": RiskProfile(
        provider="aa",
        label="American Airlines — AAdvantage partner award search",
        protection="Akamai plus a second behavioural layer. Known for silently degrading: "
                   "it returns plausible-looking empty results rather than an error.",
        calendar=CalendarSupport.NATIVE_MONTH,
        window_days=31,
        requests_for_330d=12,
        automatable="Has a month calendar, but the silent-degradation behaviour makes it the "
                    "least trustworthy source: a blocked scan and a genuinely empty month look "
                    "the same. Only use it with the freshness cross-check enabled.",
        block_risk=RiskLevel.HIGH,
        captcha_risk=RiskLevel.HIGH,
        account_risk=RiskLevel.MEDIUM,
        needs_auth=False,
        recommended_concurrency=1,
        recommended_delay_s=6.0,
        notes="AA dropped and re-added various partners over the years; confirm AA can see "
              "Qatar space at all before reading anything into empty results.",
    ),
    "fixture": RiskProfile(
        provider="fixture",
        label="Fixture — offline synthetic data",
        protection="None: reads from a local file or a seeded generator.",
        calendar=CalendarSupport.NATIVE_MONTH,
        window_days=31,
        requests_for_330d=0,
        automatable="Always. This exists so the pipeline -- planning, storage, diffing, "
                    "alerting, reporting -- can be exercised and tested without touching a "
                    "live airline site.",
        block_risk=RiskLevel.LOW,
        captcha_risk=RiskLevel.LOW,
        account_risk=RiskLevel.LOW,
        needs_auth=False,
        recommended_concurrency=8,
        recommended_delay_s=0.0,
        notes="Deterministic for a given seed, so tests and demos are reproducible.",
    ),
}


def get(provider: str) -> RiskProfile | None:
    return PROFILES.get(provider)


def calendar_capable() -> list[str]:
    """Providers with a true month view, best-first."""
    return [p for p, prof in PROFILES.items()
            if prof.calendar is CalendarSupport.NATIVE_MONTH and p != "fixture"]


def summary_rows() -> list[dict[str, Any]]:
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.SEVERE: 3}
    rows = [p.to_dict() for p in PROFILES.values() if p.provider != "fixture"]
    rows.sort(key=lambda r: (r["calendar"] != "native-month",
                             order[RiskLevel(r["block_risk"])]))
    return rows
