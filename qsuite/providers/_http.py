"""Shared HTTP helpers: block detection, capture, and browser-ish headers."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .base import BlockedError, ProviderError

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-CA,en;q=0.9",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

#: Fingerprints of the interstitials these sites serve instead of an honest 403.
_BLOCK_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"access denied",
        r"pardon our interruption",
        r"are you a (human|robot)",
        r"\bcaptcha\b",
        r"incapsula",
        r"distil",
        r"perimeterx",
        r"px-captcha",
        r"bot ?detection",
        r"unusual (traffic|activity)",
        r"request (was )?blocked",
        r"reference\s*#\s*[0-9a-f.]{8,}",
    )
]


def looks_blocked(status_code: int, body: str) -> Optional[str]:
    """Return a reason string if this response is bot-detection, else None."""
    if status_code in (401, 403, 405, 406, 429, 503):
        snippet = body[:200].replace("\n", " ")
        return f"HTTP {status_code}: {snippet}"
    head = body[:8000]
    for pat in _BLOCK_PATTERNS:
        m = pat.search(head)
        if m:
            return f"HTTP {status_code}, challenge page matched {pat.pattern!r} ({m.group(0)!r})"
    return None


async def get_json(ctx, url: str, *, params: dict | None = None,
                   headers: dict | None = None, capture_tag: str | None = None) -> Any:
    """GET and parse JSON, raising :class:`BlockedError` on an interstitial."""
    if ctx.http is None:
        raise ProviderError("no HTTP client configured (install httpx, or use --provider fixture)")
    h = {**BASE_HEADERS, **(headers or {})}
    resp = await ctx.http.get(url, params=params, headers=h)
    text = resp.text
    _capture(ctx, capture_tag, url, resp.status_code, text)
    reason = looks_blocked(resp.status_code, text)
    if reason:
        raise BlockedError(reason)
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code} from {url}")
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"response from {url} was not JSON: {exc}") from exc
    return normalise_payload(payload)


def normalise_payload(payload: Any) -> Any:
    """Undo transport-level encodings before any provider sees the data.

    Right now that means SvelteKit's flattened ``__data.json`` format, which
    several airline sites now serve. Decoding here rather than in each parser
    keeps it where it belongs -- it is a property of how the response was
    transmitted, not of what the airline is telling us.
    """
    from ._sveltekit import decode, looks_like_sveltekit

    if looks_like_sveltekit(payload):
        log.debug("decoded a SvelteKit __data.json payload")
        return decode(payload)
    return payload


def _capture(ctx, tag: str | None, url: str, status: int, body: str) -> None:
    """Save a raw response when ``--capture DIR`` is on.

    These engines change their payload shapes without notice. When a parser
    starts returning nothing, a captured response is the difference between
    fixing it in ten minutes and reverse-engineering the site again.
    """
    d = ctx.options.get("capture_dir")
    if not d or not tag:
        return
    out = Path(d)
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", tag)[:120]
    (out / f"{safe}.json").write_text(json.dumps(
        {"url": url, "status": status, "body": body[:2_000_000]}, indent=2))
    log.debug("captured %s -> %s", url, out / f"{safe}.json")


def first(d: Any, *keys: str, default: Any = None) -> Any:
    """Pull the first present key out of a dict, tolerating schema drift."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def walk(node: Any, predicate) -> list[Any]:
    """Depth-first collect of every sub-node matching ``predicate``.

    Booking APIs nest availability arbitrarily deep and rename their wrappers
    between releases. Walking for a shape instead of a fixed path makes the
    parsers survive most of that churn.
    """
    found: list[Any] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if predicate(cur):
            found.append(cur)
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return found
