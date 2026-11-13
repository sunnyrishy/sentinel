"""SQLite event log. Stores event metadata only. Never audio (spec rule 4, R6)."""

from __future__ import annotations

import csv
import io
import logging
import queue
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sentinel.config import resolve_path
from sentinel.metrics import percentile

log = logging.getLogger("sentinel.eventlog")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  ts_wall TEXT NOT NULL,             -- ISO 8601 UTC
  alert_class TEXT NOT NULL,
  stage TEXT NOT NULL,               -- gate | classifier | both
  score REAL NOT NULL,
  confirmed INTEGER NOT NULL,        -- 0 or 1
  cancel_reason TEXT,                -- timeout | superseded | NULL
  t_onset REAL, t_detect REAL, t_display REAL,   -- monotonic seconds
  processing_ms REAL,                -- t_display - t_detect
  alarm_to_display_ms REAL,          -- t_display - t_onset
  acknowledged INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,              -- live | replay | synthetic
  config_hash TEXT NOT NULL
);
"""

COLUMNS = [
    "id", "ts_wall", "alert_class", "stage", "score", "confirmed", "cancel_reason", "t_onset",
    "t_detect", "t_display", "processing_ms", "alarm_to_display_ms", "acknowledged", "source",
    "config_hash",
]  # fmt: skip
UPDATABLE = {
    "stage", "score", "confirmed", "cancel_reason", "t_display", "processing_ms",
    "alarm_to_display_ms", "acknowledged",
}  # fmt: skip
SOURCES = {"live", "replay", "synthetic"}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def format_events(rows: list[dict[str, Any]]) -> str:
    """A small text table of event rows, oldest first, for the command line tools."""
    if not rows:
        return "  (no events)"
    head = f"  {'id':<10} {'class':<16} {'stage':<10} {'confirmed':<9} {'cancel':<11} {'a2d ms':>7}"
    lines = [head]
    for r in reversed(rows):
        a2d = r["alarm_to_display_ms"]
        lines.append(
            f"  {r['id']:<10} {r['alert_class']:<16} {r['stage']:<10} {r['confirmed']!s:<9} "
            f"{(r['cancel_reason'] or '-'):<11} {(f'{a2d:.0f}' if a2d is not None else '-'):>7}"
        )
    return "\n".join(lines)


class EventLog:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) == ":memory:":
            target = ":memory:"
        else:
            p = resolve_path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            target = str(p)
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._next = self._max_numeric_id() + 1

    def _max_numeric_id(self) -> int:
        best = 0
        with self._lock:
            for (eid,) in self._conn.execute("SELECT id FROM events"):
                m = re.fullmatch(r"e_(\d+)", eid)
                if m:
                    best = max(best, int(m.group(1)))
        return best

    def next_id(self) -> str:
        """Next free event id in the e_000123 format, unique across runs of the same database."""
        with self._lock:
            n = self._next
            self._next += 1
        return f"e_{n:06d}"

    # Writes -----------------------------------------------------------------------------------
    def insert(self, **fields: Any) -> None:
        if fields.get("source") not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        row = {c: fields.get(c) for c in COLUMNS}
        row["ts_wall"] = row["ts_wall"] or utc_now_iso()
        row["confirmed"] = int(bool(row["confirmed"]))
        row["acknowledged"] = int(bool(row["acknowledged"]))
        marks = ", ".join("?" for _ in COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO events ({', '.join(COLUMNS)}) VALUES ({marks})",
                [row[c] for c in COLUMNS],
            )
            self._conn.commit()

    def update(self, event_id: str, **fields: Any) -> bool:
        bad = set(fields) - UPDATABLE
        if bad:
            raise ValueError(f"cannot update columns {sorted(bad)}")
        if not fields:
            return False
        for key in ("confirmed", "acknowledged"):
            if key in fields:
                fields[key] = int(bool(fields[key]))
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE events SET {sets} WHERE id = ?",
                [*fields.values(), event_id],
            )
            self._conn.commit()
            return cur.rowcount > 0

    def acknowledge(self, event_id: str) -> bool:
        return self.update(event_id, acknowledged=True)

    # Reads ------------------------------------------------------------------------------------
    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id = ?", [event_id]).fetchone()
        return dict(row) if row else None

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY rowid DESC LIMIT ? OFFSET ?", [limit, offset]
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def stats(self, now: datetime | None = None) -> dict[str, Any]:
        """Counters and latency percentiles for the dashboard."""
        now = now or datetime.now(UTC)
        since = (now - timedelta(hours=1)).isoformat(timespec="milliseconds")
        with self._lock:
            rows = [dict(r) for r in self._conn.execute("SELECT * FROM events")]
        proc = [r["processing_ms"] for r in rows if r["processing_ms"] is not None]
        a2d = [r["alarm_to_display_ms"] for r in rows if r["alarm_to_display_ms"] is not None]
        by_class: dict[str, int] = {}
        for r in rows:
            by_class[r["alert_class"]] = by_class.get(r["alert_class"], 0) + 1
        return {
            "events_total": len(rows),
            "by_class": by_class,
            "confirmed": sum(1 for r in rows if r["confirmed"]),
            "cancelled": sum(1 for r in rows if r["cancel_reason"]),
            "cancelled_timeout": sum(1 for r in rows if r["cancel_reason"] == "timeout"),
            "cancelled_superseded": sum(1 for r in rows if r["cancel_reason"] == "superseded"),
            "acknowledged": sum(1 for r in rows if r["acknowledged"]),
            "last_hour": sum(1 for r in rows if r["ts_wall"] >= since),
            "processing_p50_ms": percentile(proc, 50),
            "processing_p95_ms": percentile(proc, 95),
            "p50_ms": percentile(a2d, 50),
            "p95_ms": percentile(a2d, 95),
        }

    def per_hour(self) -> list[dict[str, Any]]:
        """Events per UTC hour, oldest first. The near-miss view."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT substr(ts_wall, 1, 13) AS hour, COUNT(*) AS n FROM events "
                "GROUP BY hour ORDER BY hour"
            ).fetchall()
        return [{"hour": r["hour"], "count": r["n"]} for r in rows]

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(COLUMNS)
        with self._lock:
            for r in self._conn.execute("SELECT * FROM events ORDER BY rowid"):
                w.writerow([r[c] for c in COLUMNS])
        return buf.getvalue()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class AsyncEventLog:
    """Moves writes onto their own thread so the policy worker never waits for the disk."""

    def __init__(self, log: EventLog) -> None:
        self.log = log
        self._q: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="eventlog", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            job = self._q.get()
            try:
                if job is None:
                    return
                try:
                    job()
                except Exception:
                    log.exception("event log write failed")
            finally:
                self._q.task_done()

    def insert(self, **fields: Any) -> None:
        self._q.put(lambda: self.log.insert(**fields))

    def update(self, event_id: str, **fields: Any) -> None:
        self._q.put(lambda: self.log.update(event_id, **fields))

    def flush(self) -> None:
        self._q.join()

    def close(self) -> None:
        self.flush()
        self._q.put(None)
        self._thread.join(timeout=2)
