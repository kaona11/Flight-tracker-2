"""JSON export: the full scan, plus the merged per-date view."""

from __future__ import annotations

import json
from typing import Any, Optional

from ..diffing import Diff
from ..models import ScanResult
from ..scanner import coverage_report
from .common import build_day_views, status_counts


def render_json(result: ScanResult, diff: Optional[Diff] = None, indent: int = 2) -> str:
    views = build_day_views(result)
    payload: dict[str, Any] = result.to_dict()
    payload["coverage"] = coverage_report(result)
    payload["summary"] = {
        "counts": {s.value: n for s, n in status_counts(views).items()},
        "available_dates": sorted(d.isoformat() for d, v in views.items()
                                  if v.status.value == "available"),
    }
    payload["calendar"] = [
        {
            "date": d.isoformat(),
            "status": v.status.value,
            "providers": v.providers_available,
            "miles": v.best_miles,
            "seats": v.best_seats,
            "qsuite": v.qsuite,
            "per_provider": {p: s.value for p, s in v.per_provider.items()},
        }
        for d, v in sorted(views.items())
    ]
    if diff is not None:
        payload["changes"] = [
            {"kind": c.kind.value, "date": c.date.isoformat(), "provider": c.provider,
             "status": c.status.value,
             "prev_status": c.prev_status.value if c.prev_status else None,
             "miles": c.miles, "seats": c.seats, "qsuite": c.qsuite}
            for c in diff.changes
        ]
    return json.dumps(payload, indent=indent)
