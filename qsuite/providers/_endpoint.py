"""Endpoint templating, so a moved API is a config change rather than a patch.

These engines are undocumented and they move. The original design had each
provider hard-code its path *and* its query parameter names, which meant a
rename upstream needed a code edit and a release. It does not any more: an
endpoint template can carry its whole query string, and everything variable is
a placeholder.

Available placeholders::

    {origin} {destination}      YUL, SIN
    {year} {month} {month02}    2026, 10, "10"
    {date}                      2026-10-01   (window start, ISO)
    {date_ddmmyyyy}             01-10-2026
    {date_mmddyyyy}             10/01/2026
    {date_compact}              20261001
    {cabin}                     the engine's own cabin token
    {adults}                    1

``qsuite calibrate`` writes these templates for you from a request copied out
of the browser, which is the intended way to repair a provider.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from ..models import Route
from ..ranges import Window


def placeholders(route: Route, window: Window, cabin_code: str,
                 adults: int = 1) -> dict[str, Any]:
    start: dt.date = window.start
    return {
        "origin": route.origin,
        "destination": route.destination,
        "year": start.year,
        "month": start.month,
        "month02": f"{start.month:02d}",
        "date": start.isoformat(),
        "date_ddmmyyyy": start.strftime("%d-%m-%Y"),
        "date_mmddyyyy": start.strftime("%m/%d/%Y"),
        "date_compact": start.strftime("%Y%m%d"),
        "cabin": cabin_code,
        "adults": adults,
    }


def format_endpoint(template: str, route: Route, window: Window,
                    cabin_code: str, adults: int = 1) -> str:
    """Substitute placeholders into an endpoint template.

    A template referring to something we do not supply raises rather than
    silently producing a broken URL -- a 404 three layers down is much harder
    to diagnose than a clear error here.
    """
    try:
        return template.format(**placeholders(route, window, cabin_code, adults))
    except KeyError as exc:
        raise ValueError(
            f"endpoint template refers to unknown placeholder {exc}. "
            f"Available: {', '.join(sorted(placeholders(route, window, cabin_code)))}"
        ) from None


def resolve(template: str, route: Route, window: Window, cabin_code: str,
            default_params: dict[str, Any], adults: int = 1
            ) -> tuple[str, Optional[dict[str, Any]]]:
    """Return ``(url, params)`` for a request.

    If the template carries its own query string it is taken as complete and
    the provider's built-in parameters are dropped. That is what lets a
    captured browser request be pasted in verbatim: whatever the site wants
    today wins over whatever we guessed when this was written.
    """
    url = format_endpoint(template, route, window, cabin_code, adults)
    if "?" in url:
        return url, None
    return url, default_params
