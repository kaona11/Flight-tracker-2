"""Alert channels and the filtering that decides what is worth sending."""

from __future__ import annotations

import abc
import datetime as dt
import logging
from typing import Iterable

from ..config import AlertConfig
from ..diffing import Change, ChangeKind, Diff
from ..models import Cabin, Route

log = logging.getLogger(__name__)


class AlertChannel(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def send(self, subject: str, body_text: str, changes: list[Change]) -> bool:
        """Deliver one notification. Return True on success."""


def filter_changes(diff: Diff, cfg: AlertConfig) -> list[Change]:
    """Apply the user's noise filters to the alertable set.

    Deliberately conservative about ``qsuite_only``: a change whose Qsuite
    verdict is merely *unconfirmed* still gets through unless the user opted
    into strict mode, because most engines never report equipment and silently
    dropping everything would be worse than a little noise.
    """
    out: list[Change] = []
    for c in diff.alertable:
        if cfg.qsuite_only and not c.qsuite:
            continue
        if c.seats is not None and c.seats < cfg.min_seats:
            continue
        if cfg.max_miles is not None and c.miles is not None and c.miles > cfg.max_miles:
            continue
        out.append(c)
    return out


def build_message(route: Route, cabin: Cabin, changes: list[Change]) -> tuple[str, str]:
    """Return ``(subject, plain-text body)`` for a set of changes."""
    n = len(changes)
    newly = sum(1 for c in changes if c.kind is ChangeKind.NEWLY_OPENED)
    subject = (f"{n} new {route} {cabin.value} award date"
               f"{'s' if n != 1 else ''} "
               f"({newly} newly opened)")

    lines = [f"{route} · {cabin.value} · new award availability",
             f"Detected {dt.datetime.now(dt.timezone.utc):%d %b %Y %H:%M UTC}", ""]
    for c in sorted(changes, key=lambda x: x.date):
        tag = "NEW  " if c.kind is ChangeKind.NEWLY_OPENED else "1st  "
        lines.append(f"{tag}{c.describe()}")
    lines += ["",
              "Availability moves fast — confirm on the booking engine before planning.",
              "Qsuite verdicts are inferred from aircraft type and can change on a swap."]
    return subject, "\n".join(lines)


def dispatch(channels: Iterable[AlertChannel], route: Route, cabin: Cabin,
             changes: list[Change]) -> dict[str, bool]:
    """Send to every channel. One channel failing never blocks the others."""
    if not changes:
        log.info("no alertable changes; nothing sent")
        return {}
    subject, body = build_message(route, cabin, changes)
    results: dict[str, bool] = {}
    for ch in channels:
        try:
            results[ch.name] = ch.send(subject, body, changes)
        except Exception as exc:  # noqa: BLE001 - a dead webhook must not kill the run
            log.error("alert channel %s failed: %s", ch.name, exc)
            results[ch.name] = False
    return results
