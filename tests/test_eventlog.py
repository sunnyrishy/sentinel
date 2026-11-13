"""Event log tests: schema, inserts, updates, queries, ids, export."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sentinel.eventlog import COLUMNS, AsyncEventLog, EventLog


def ev(log: EventLog, **kw) -> str:
    eid = kw.pop("id", None) or log.next_id()
    base = dict(
        id=eid,
        alert_class="reversing_alarm",
        stage="gate",
        score=0.9,
        confirmed=False,
        t_onset=1.0,
        t_detect=1.3,
        source="synthetic",
        config_hash="abc123",
    )
    base.update(kw)
    log.insert(**base)
    return eid


def test_schema_matches_spec_columns() -> None:
    log = EventLog()
    cols = [r[1] for r in log._conn.execute("PRAGMA table_info(events)")]
    assert (
        cols
        == COLUMNS
        == [
            "id",
            "ts_wall",
            "alert_class",
            "stage",
            "score",
            "confirmed",
            "cancel_reason",
            "t_onset",
            "t_detect",
            "t_display",
            "processing_ms",
            "alarm_to_display_ms",
            "acknowledged",
            "source",
            "config_hash",
        ]
    )
    assert not any("audio" in c or "wav" in c for c in cols)  # metadata only


def test_insert_and_get_roundtrip() -> None:
    log = EventLog()
    eid = ev(log)
    row = log.get(eid)
    assert row is not None
    assert row["alert_class"] == "reversing_alarm" and row["confirmed"] == 0
    assert row["acknowledged"] == 0 and row["cancel_reason"] is None
    assert row["ts_wall"].endswith("+00:00")
    assert row["config_hash"] == "abc123" and row["source"] == "synthetic"


def test_update_on_confirmation_and_latency() -> None:
    log = EventLog()
    eid = ev(log)
    assert log.update(eid, confirmed=True, stage="both", score=0.95)
    assert log.update(eid, t_display=1.31, processing_ms=10.0, alarm_to_display_ms=310.0)
    row = log.get(eid)
    assert row["confirmed"] == 1 and row["stage"] == "both" and row["score"] == 0.95
    assert row["processing_ms"] == 10.0 and row["alarm_to_display_ms"] == 310.0
    assert not log.update("e_missing", confirmed=True)


def test_update_rejects_unknown_or_immutable_columns() -> None:
    log = EventLog()
    eid = ev(log)
    with pytest.raises(ValueError, match="cannot update"):
        log.update(eid, id="other")
    with pytest.raises(ValueError, match="cannot update"):
        log.update(eid, nonsense=1)


def test_insert_validates_source_and_unique_id() -> None:
    log = EventLog()
    with pytest.raises(ValueError, match="source"):
        ev(log, source="cloud")
    eid = ev(log)
    with pytest.raises(sqlite3.IntegrityError):
        ev(log, id=eid)


def test_list_is_newest_first_with_limit() -> None:
    log = EventLog()
    ids = [ev(log) for _ in range(5)]
    rows = log.list(limit=3)
    assert [r["id"] for r in rows] == ids[::-1][:3]
    assert log.count() == 5 and len(log.list(limit=100)) == 5


def test_acknowledge() -> None:
    log = EventLog()
    eid = ev(log)
    assert log.acknowledge(eid) and log.get(eid)["acknowledged"] == 1
    assert not log.acknowledge("e_nope")


def test_ids_have_spec_format_and_continue_across_sessions(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "events.sqlite"
    log = EventLog(path)
    assert log.next_id() == "e_000001"
    ev(log, id="e_000007")
    log.close()
    again = EventLog(path)
    assert again.next_id() == "e_000008"  # no clash with stored ids
    assert again.count() == 1


def test_stats_counts_and_percentiles() -> None:
    log = EventLog()
    a = ev(log, confirmed=True, alert_class="vehicle_horn")
    b = ev(log, cancel_reason="timeout")
    c = ev(log, cancel_reason="superseded", confirmed=True)
    for i, eid in enumerate([a, b, c]):
        log.update(eid, processing_ms=10.0 * (i + 1), alarm_to_display_ms=100.0 * (i + 1))
    log.acknowledge(a)
    s = log.stats()
    assert s["events_total"] == 3 and s["confirmed"] == 2
    assert s["cancelled"] == 2 and s["cancelled_timeout"] == 1 and s["cancelled_superseded"] == 1
    assert s["by_class"] == {"vehicle_horn": 1, "reversing_alarm": 2}
    assert s["acknowledged"] == 1 and s["last_hour"] == 3
    assert s["p50_ms"] == 200.0 and s["processing_p50_ms"] == 20.0
    assert s["p95_ms"] == pytest.approx(290.0)
    assert EventLog().stats()["p50_ms"] is None


def test_last_hour_window() -> None:
    log = EventLog()
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="milliseconds")
    ev(log, ts_wall=old)
    ev(log)
    assert log.stats()["last_hour"] == 1


def test_per_hour_buckets() -> None:
    log = EventLog()
    ev(log, ts_wall="2026-11-20T21:05:00.000+00:00")
    ev(log, ts_wall="2026-11-20T21:55:00.000+00:00")
    ev(log, ts_wall="2026-11-20T22:01:00.000+00:00")
    assert log.per_hour() == [
        {"hour": "2026-11-20T21", "count": 2},
        {"hour": "2026-11-20T22", "count": 1},
    ]


def test_csv_export() -> None:
    log = EventLog()
    ev(log)
    lines = log.to_csv().strip().splitlines()
    assert lines[0].split(",") == COLUMNS and len(lines) == 2
    assert "reversing_alarm" in lines[1]


def test_async_writer_keeps_order_and_flushes() -> None:
    log = EventLog()
    w = AsyncEventLog(log)
    eid = log.next_id()
    w.insert(
        id=eid,
        alert_class="fire_alarm",
        stage="classifier",
        score=0.5,
        confirmed=True,
        source="live",
        config_hash="h",
    )
    w.update(eid, acknowledged=True)  # queued behind the insert
    w.flush()
    assert log.get(eid)["acknowledged"] == 1
    w.close()


def test_concurrent_writers_are_safe() -> None:
    log = EventLog()

    def work() -> None:
        for _ in range(40):
            ev(log)

    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert log.count() == 160
