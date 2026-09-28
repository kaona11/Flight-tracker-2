"""Turn a request copied out of the browser into a working endpoint template.

These engines are undocumented and they move; the URL guessed when a provider
was written may already be a 404. Rather than making that a code change, this
takes the real request -- copied from your own browser, on your own connection,
with your own session -- and rewrites it into config.

The flow is:

1. Search the route on the airline's site with DevTools open (Network tab).
2. Right-click the request that returned the availability JSON and choose
   "Copy as cURL (bash)".
3. ``qsuite calibrate --provider alaska --route YUL-SIN --date 2026-10-01``
   and paste.

Values matching the route and date you searched are replaced with placeholders,
so the one captured request generalises to every month of a scan.
"""

from __future__ import annotations

import datetime as dt
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

#: Headers worth keeping. Everything else is either noise, per-request state
#: that will be stale by the next run, or a credential we refuse to write into
#: a config file (see SENSITIVE below).
USEFUL_HEADERS = {
    "accept", "accept-language", "content-type", "referer", "origin",
    "x-requested-with",
}

#: Never written to config. A captured request routinely carries session
#: cookies and bearer tokens; persisting those into a YAML file the user may
#: commit is how credentials leak. They belong in an environment variable.
SENSITIVE = {"cookie", "authorization", "x-csrf-token", "x-api-key",
             "x-auth-token", "set-cookie", "proxy-authorization"}


@dataclass
class Capture:
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: Optional[str] = None
    dropped_sensitive: list[str] = field(default_factory=list)


def parse_curl(text: str) -> Capture:
    """Parse a ``curl`` command as browsers emit it.

    Chrome's "Copy as cURL (bash)" is the supported form. The cmd/PowerShell
    variants use different quoting and caret escapes, so we tell the user to
    pick bash rather than trying to guess which shell mangled it.
    """
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("nothing to parse — paste the copied cURL command")
    # Join shell line continuations.
    cleaned = re.sub(r"\\\s*\n", " ", cleaned)
    cleaned = re.sub(r"\^\s*\n", " ", cleaned)      # cmd.exe continuation
    cleaned = cleaned.replace("\n", " ")

    try:
        tokens = shlex.split(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"could not parse that as a shell command ({exc}). In Chrome use "
            "'Copy as cURL (bash)' rather than the cmd or PowerShell variant."
        ) from None

    if not tokens or tokens[0] != "curl":
        raise ValueError("that does not start with 'curl' — copy the whole command")

    cap = Capture(url="")
    i = 1
    method: Optional[str] = None
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-H", "--header") and i + 1 < len(tokens):
            name, _, value = tokens[i + 1].partition(":")
            key = name.strip().lower()
            value = value.strip()
            if key in SENSITIVE:
                cap.dropped_sensitive.append(key)
            elif key in USEFUL_HEADERS:
                cap.headers[name.strip()] = value
            i += 2
        elif tok in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
        elif tok in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(tokens):
            cap.body = tokens[i + 1]
            i += 2
        elif tok in ("-b", "--cookie") and i + 1 < len(tokens):
            cap.dropped_sensitive.append("cookie")
            i += 2
        elif tok.startswith("-"):
            i += 1                                   # flags we do not care about
        else:
            if not cap.url:
                cap.url = tok
            i += 1

    if not cap.url:
        raise ValueError("no URL found in that command")
    cap.method = method or ("POST" if cap.body else "GET")
    return cap


def templatise(url: str, route_origin: str, route_dest: str,
               date: Optional[dt.date]) -> tuple[str, list[str]]:
    """Replace the searched route and date in a URL with placeholders.

    Returns ``(template, notes)``. Substitution is done on query-parameter
    values and path segments, and is deliberately exact-match: guessing at
    partial matches would corrupt unrelated parameters.
    """
    parts = urlsplit(url)
    notes: list[str] = []

    subs: dict[str, str] = {
        route_origin.upper(): "{origin}",
        route_dest.upper(): "{destination}",
    }
    if date:
        subs.update({
            date.isoformat(): "{date}",
            date.strftime("%d-%m-%Y"): "{date_ddmmyyyy}",
            date.strftime("%m/%d/%Y"): "{date_mmddyyyy}",
            date.strftime("%Y%m%d"): "{date_compact}",
            f"{date.year}-{date.month:02d}": "{year}-{month02}",
        })

    def swap(value: str) -> str:
        # Longest first, so a full date is matched before its year-month prefix.
        for needle in sorted(subs, key=len, reverse=True):
            if value == needle:
                return subs[needle]
        return value

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    new_pairs: list[tuple[str, str]] = []
    for key, value in pairs:
        swapped = swap(value)
        if swapped != value:
            notes.append(f"query {key}={value} -> {swapped}")
        new_pairs.append((key, swapped))

    segments = parts.path.split("/")
    new_segments = []
    for seg in segments:
        swapped = swap(seg)
        if swapped != seg:
            notes.append(f"path segment {seg} -> {swapped}")
        new_segments.append(swapped)

    # urlencode would percent-encode our braces back into %7B.
    query = "&".join(f"{k}={v}" for k, v in new_pairs) if new_pairs else ""
    template = urlunsplit((parts.scheme, parts.netloc, "/".join(new_segments),
                           query, ""))
    return template, notes


def to_yaml(provider: str, template: str, headers: dict[str, str]) -> str:
    """Emit the config block for this provider."""
    lines = ["providers:", f"  {provider}:",
             f"    endpoint: \"{template}\""]
    if headers:
        lines.append("    headers:")
        for k, v in sorted(headers.items()):
            lines.append(f"      {k}: \"{v}\"")
    return "\n".join(lines)


def calibrate(curl_text: str, provider: str, origin: str, destination: str,
              date: Optional[dt.date]) -> tuple[str, str, list[str], list[str]]:
    """Full pipeline. Returns ``(yaml, template, notes, dropped_sensitive)``."""
    cap = parse_curl(curl_text)
    if cap.method != "GET":
        notes_prefix = [
            f"NOTE: the captured request was {cap.method}, and only GET endpoints "
            "are supported. The body was discarded; if this engine needs a POST "
            "the provider will need a code change."
        ]
    else:
        notes_prefix = []
    template, notes = templatise(cap.url, origin, destination, date)
    return (to_yaml(provider, template, cap.headers), template,
            notes_prefix + notes, sorted(set(cap.dropped_sensitive)))
