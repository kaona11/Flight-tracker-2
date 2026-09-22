"""SQLite persistence: every scan's cells, kept so runs can be diffed.

The whole point of requirement 5 is "tell me what's *new*", which means the
tool has to remember what it saw last time. One file, no server, safe to commit
to a cron job or a GitHub Action cache.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Iterable, Optional

from .models import Cabin, Cell, Offer, Route, ScanResult, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    scan_id        TEXT PRIMARY KEY,
    route          TEXT NOT NULL,
    cabin          TEXT NOT NULL,
    start_date     TEXT NOT NULL,
    end_date       TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    total_requests INTEGER DEFAULT 0,
    runs_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_route ON scans(route, cabin, started_at DESC);

CREATE TABLE IF NOT EXISTS cells (
    scan_id     TEXT NOT NULL,
    date        TEXT NOT NULL,
    route       TEXT NOT NULL,
    cabin       TEXT NOT NULL,
    provider    TEXT NOT NULL,
    status      TEXT NOT NULL,
    miles       INTEGER,
    seats       INTEGER,
    qsuite      INTEGER,
    note        TEXT,
    offers_json TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (scan_id, date, provider),
    FOREIGN KEY (scan_id) REFERENCES scans(scan_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cells_lookup ON cells(route, cabin, date, provider);

CREATE TABLE IF NOT EXISTS alerts_sent (
    route      TEXT NOT NULL,
    cabin      TEXT NOT NULL,
    date       TEXT NOT NULL,
    provider   TEXT NOT NULL,
    sent_at    TEXT NOT NULL,
    PRIMARY KEY (route, cabin, date, provider)
);
"""


class Store:
    def __init__(self, path: str | Path = "data/qsuite.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- writing ----------------------------------------------------------

    def save(self, result: ScanResult) -> str:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO scans
                   (scan_id, route, cabin, start_date, end_date, started_at,
                    finished_at, total_requests, runs_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (result.scan_id, str(result.route), result.cabin.value,
                 result.start.isoformat(), result.end.isoformat(),
                 result.started_at.isoformat(),
                 result.finished_at.isoformat() if result.finished_at else None,
                 result.total_requests,
                 json.dumps([r.to_dict() for r in result.runs])),
            )
            self.conn.executemany(
                """INSERT OR REPLACE INTO cells
                   (scan_id, date, route, cabin, provider, status, miles, seats,
                    qsuite, note, offers_json, observed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(result.scan_id, c.date.isoformat(), str(c.route), c.cabin.value,
                  c.provider, c.status.value, c.cheapest_miles, c.max_seats,
                  1 if c.qsuite_confirmed else 0, c.note,
                  json.dumps([o.to_dict() for o in c.offers]),
                  c.observed_at.isoformat())
                 for c in result.cells],
            )
        return result.scan_id

    def mark_alerted(self, route: Route, cabin: Cabin,
                     items: Iterable[tuple[dt.date, str]]) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with self.conn:
            self.conn.executemany(
                """INSERT OR REPLACE INTO alerts_sent
                   (route, cabin, date, provider, sent_at) VALUES (?,?,?,?,?)""",
                [(str(route), cabin.value, d.isoformat(), p, now) for d, p in items],
            )

    def already_alerted(self, route: Route, cabin: Cabin) -> set[tuple[dt.date, str]]:
        rows = self.conn.execute(
            "SELECT date, provider FROM alerts_sent WHERE route=? AND cabin=?",
            (str(route), cabin.value)).fetchall()
        return {(dt.date.fromisoformat(r["date"]), r["provider"]) for r in rows}

    # ---- reading ----------------------------------------------------------

    def scan_ids(self, route: Route, cabin: Cabin, limit: int = 10) -> list[str]:
        rows = self.conn.execute(
            """SELECT scan_id FROM scans WHERE route=? AND cabin=?
               ORDER BY started_at DESC LIMIT ?""",
            (str(route), cabin.value, limit)).fetchall()
        return [r["scan_id"] for r in rows]

    def previous_scan_id(self, route: Route, cabin: Cabin,
                         before: str | None = None) -> Optional[str]:
        """The most recent completed scan other than ``before``."""
        ids = self.scan_ids(route, cabin, limit=5)
        for sid in ids:
            if sid != before:
                return sid
        return None

    def cells_for_scan(self, scan_id: str) -> list[Cell]:
        rows = self.conn.execute(
            "SELECT * FROM cells WHERE scan_id=? ORDER BY date, provider",
            (scan_id,)).fetchall()
        return [self._row_to_cell(r) for r in rows]

    @staticmethod
    def _row_to_cell(r: sqlite3.Row) -> Cell:
        offers = [Offer(**o) for o in json.loads(r["offers_json"] or "[]")]
        return Cell(
            date=dt.date.fromisoformat(r["date"]),
            route=Route.parse(r["route"]),
            cabin=Cabin(r["cabin"]),
            provider=r["provider"],
            status=Status(r["status"]),
            offers=offers,
            note=r["note"],
            observed_at=dt.datetime.fromisoformat(r["observed_at"]),
        )

    def prune(self, keep: int = 30) -> int:
        """Drop all but the newest ``keep`` scans per route/cabin."""
        removed = 0
        pairs = self.conn.execute("SELECT DISTINCT route, cabin FROM scans").fetchall()
        for p in pairs:
            ids = self.conn.execute(
                """SELECT scan_id FROM scans WHERE route=? AND cabin=?
                   ORDER BY started_at DESC LIMIT -1 OFFSET ?""",
                (p["route"], p["cabin"], keep)).fetchall()
            if not ids:
                continue
            with self.conn:
                self.conn.executemany("DELETE FROM scans WHERE scan_id=?",
                                      [(i["scan_id"],) for i in ids])
                self.conn.executemany("DELETE FROM cells WHERE scan_id=?",
                                      [(i["scan_id"],) for i in ids])
            removed += len(ids)
        with closing(self.conn.cursor()) as cur:
            cur.execute("VACUUM")
        return removed

    def close(self) -> None:
        self.conn.close()


def new_scan_id() -> str:
    return f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
