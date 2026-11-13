"""Dashboard: endpoints, WebSocket feed, simulator display, acknowledge, end to end."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from websockets.sync.client import connect

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.adapters.base import MultiDisplay
from sentinel.adapters.display_sim import SimDisplay
from sentinel.config import DEFAULT_CONFIG_PATH, config_from_dict, load_config
from sentinel.dashboard.server import DashboardServer, Hub, create_app, stats_message
from sentinel.eventlog import EventLog
from sentinel.pipeline import Pipeline
from sentinel.sim.scenario import load_scenario, render
from sentinel.types import Alert, AlertClass, DisplayCaps

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = chr(0x2014)  # built from the code point so this file never contains one


def alert(line1="BACKUP ALARM", line2="NEARBY", blink=True, ttl=3.0) -> Alert:
    return Alert("e_000123", AlertClass.REVERSING_ALARM, 1, line1, line2, blink, ttl)


def add_event(log: EventLog, **kw) -> str:
    eid = kw.pop("id", None) or log.next_id()
    log.insert(
        id=eid,
        alert_class="reversing_alarm",
        stage="gate",
        score=0.9,
        confirmed=True,
        t_onset=1.0,
        t_detect=1.3,
        source="synthetic",
        config_hash="h",
        **kw,
    )
    return eid


@pytest.fixture
def parts():
    log, hub = EventLog(), Hub()
    app = create_app(
        log, hub, status=lambda: {"source": "replay", "recording_badge": True}, stats_interval_s=60
    )
    with TestClient(app) as client:
        yield log, hub, client


# ---- HTTP --------------------------------------------------------------------------------------
def test_index_serves_dashboard_page(parts) -> None:
    _, _, client = parts
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Sentinel" in r.text and "kiosk" in r.text and "RECORDING" in r.text


def test_events_endpoint_newest_first_and_limit(parts) -> None:
    log, _, client = parts
    ids = [add_event(log) for _ in range(5)]
    rows = client.get("/api/events", params={"limit": 3}).json()
    assert [r["id"] for r in rows] == ids[::-1][:3]
    assert client.get("/api/events").json()[0]["id"] == ids[-1]
    assert client.get("/api/events", params={"limit": 0}).status_code == 422


def test_stats_per_hour_and_status(parts) -> None:
    log, _, client = parts
    eid = add_event(log)
    log.update(eid, processing_ms=5.0, alarm_to_display_ms=320.0)
    s = client.get("/api/stats").json()
    assert (
        s["events_total"] == 1 and s["p50_ms"] == 320.0 and s["by_class"] == {"reversing_alarm": 1}
    )
    assert client.get("/api/per_hour").json()[0]["count"] == 1
    assert client.get("/api/status").json() == {"source": "replay", "recording_badge": True}


def test_csv_export(parts) -> None:
    log, _, client = parts
    add_event(log)
    r = client.get("/api/events.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,ts_wall,alert_class") and len(lines) == 2


def test_acknowledge_updates_row_and_missing_is_404(parts) -> None:
    log, _, client = parts
    eid = add_event(log)
    r = client.post(f"/api/events/{eid}/ack")
    assert r.status_code == 200 and r.json() == {"id": eid, "acknowledged": True}
    assert log.get(eid)["acknowledged"] == 1
    assert client.get("/api/events").json()[0]["acknowledged"] == 1
    assert client.post("/api/events/e_999999/ack").status_code == 404


# ---- WebSocket -----------------------------------------------------------------------------
def test_ws_sends_stats_first_then_published_messages(parts) -> None:
    log, hub, client = parts
    add_event(log)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first == {"type": "stats", "events_total": 1, "p50_ms": None, "p95_ms": None}
        hub.publish(
            {
                "type": "alert",
                "event_id": "e_000001",
                "line1": "BACKUP ALARM",
                "line2": "NEARBY",
                "tier": 1,
                "blink": True,
                "ttl_ms": 3000,
            }
        )
        hub.publish({"type": "clear", "event_id": "e_000001"})
        assert ws.receive_json()["line1"] == "BACKUP ALARM"
        assert ws.receive_json() == {"type": "clear", "event_id": "e_000001"}


def test_ack_is_broadcast_to_subscribers(parts) -> None:
    log, _, client = parts
    eid = add_event(log)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post(f"/api/events/{eid}/ack")
        assert ws.receive_json() == {"type": "event", "event": {"id": eid, "acknowledged": True}}


def test_late_subscriber_sees_the_active_alert(parts) -> None:
    _, hub, client = parts
    hub.publish(
        {
            "type": "alert",
            "event_id": "e_000002",
            "line1": "FIRE ALARM",
            "line2": "NEARBY",
            "tier": 1,
            "blink": True,
            "ttl_ms": 3000,
        }
    )
    time.sleep(0.05)
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "alert" and msg["event_id"] == "e_000002"
        assert 0 < msg["ttl_ms"] <= 3000
    hub.publish({"type": "clear", "event_id": "e_000002"})
    assert hub.snapshot() == []


def test_hub_publish_without_loop_is_safe() -> None:
    Hub().publish({"type": "clear", "event_id": None})


def test_stats_message_shape() -> None:
    log = EventLog()
    eid = add_event(log)
    log.update(eid, alarm_to_display_ms=410.4)
    assert stats_message(log) == {"type": "stats", "events_total": 1, "p50_ms": 410, "p95_ms": 410}


# ---- simulator display -----------------------------------------------------------------------
def test_sim_display_publishes_protocol_messages() -> None:
    sent: list[dict] = []
    d = SimDisplay(sent.append, DisplayCaps(max_chars_per_line=12))
    d.show(alert())
    d.clear("e_000123")
    assert sent[0] == {
        "type": "alert",
        "event_id": "e_000123",
        "line1": "BACKUP ALARM",
        "line2": "NEARBY",
        "tier": 1,
        "blink": True,
        "ttl_ms": 3000,
    }
    assert sent[1] == {"type": "clear", "event_id": "e_000123"}
    assert d.capabilities().supports_blink is True


def test_sim_display_truncates_to_caps() -> None:
    sent: list[dict] = []
    SimDisplay(sent.append, DisplayCaps(max_chars_per_line=6)).show(alert())
    assert sent[0]["line1"] == "BACKUP" and sent[0]["line2"] == "NEARBY"
    sent.clear()
    SimDisplay(sent.append, DisplayCaps(lines=1)).show(alert())
    assert sent[0]["line2"] is None


def test_multi_display_isolates_failures_and_fans_out() -> None:
    seen: list[str] = []

    class Boom:
        def capabilities(self):
            return DisplayCaps(supports_blink=True)

        def show(self, a):
            raise RuntimeError("boom")

        def clear(self, e=None): ...
        def close(self): ...

    class Good(Boom):
        def show(self, a):
            seen.append(a.event_id)

    m = MultiDisplay([Boom(), Good()])
    m.show(alert())
    assert seen == ["e_000123"]
    assert m.capabilities().supports_blink is True


# ---- real server, end to end -------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_end_to_end_replay_shows_alert_and_ack_updates_row(tmp_path: Path) -> None:
    """M5 acceptance: a replay shows alerts over the WebSocket; acknowledging updates the row."""
    cfg = load_config()
    log, hub = EventLog(), Hub()
    port = free_port()
    server = DashboardServer(create_app(log, hub, stats_interval_s=60), "127.0.0.1", port)
    server.start()
    received: list[dict] = []
    ready = threading.Event()

    def listen() -> None:
        with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
            ready.set()
            try:
                while True:
                    received.append(json.loads(ws.recv(timeout=4)))
            except Exception:
                return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    assert ready.wait(5)
    time.sleep(0.2)
    try:
        rendered = render(load_scenario(ROOT / "scenarios" / "tonal_10db.yaml"))
        raw = json.loads(json.dumps(__import__("yaml").safe_load(DEFAULT_CONFIG_PATH.read_text())))
        raw["recording"]["dir"] = str(tmp_path / "rec")
        pipe = Pipeline(
            config_from_dict(raw),
            AudioFileSource(rendered.audio, t0=0.0),
            SimDisplay(hub.publish, cfg.display.caps.to_caps()),
            eventlog=log,
            source_label="synthetic",
        )
        pipe.add_listener(hub.publish)
        pipe.run_sync()
        time.sleep(0.3)
        types = [m["type"] for m in received]
        assert "alert" in types and "clear" in types and "event" in types
        shown = next(m for m in received if m["type"] == "alert")
        assert shown["line1"] == "BACKUP ALARM" and shown["line2"] == "NEARBY" and shown["blink"]
        event_id = shown["event_id"]
        r = httpx.post(f"http://127.0.0.1:{port}/api/events/{event_id}/ack")
        assert r.status_code == 200
        row = httpx.get(f"http://127.0.0.1:{port}/api/events").json()[0]
        assert row["id"] == event_id and row["acknowledged"] == 1 and row["confirmed"] == 1
        assert row["source"] == "synthetic"
    finally:
        server.stop()


def test_server_reports_port_in_use() -> None:
    port = free_port()
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", port))
        busy.listen()
        server = DashboardServer(create_app(EventLog(), Hub()), "127.0.0.1", port)
        with pytest.raises(RuntimeError, match="already in use"):
            server.start(timeout_s=3)


def test_no_em_dashes_anywhere_in_text_files() -> None:
    """Style rule: no em dashes in docs, comments or UI text."""
    bad = []
    for pattern in ("*.md", "*.py", "*.yaml", "*.html", "*.toml"):
        for path in ROOT.rglob(pattern):
            if any(
                part in {".venv", ".git", ".ruff_cache", ".pytest_cache"} for part in path.parts
            ):
                continue
            if EM_DASH in path.read_text(encoding="utf-8"):
                bad.append(str(path.relative_to(ROOT)))
    assert not bad, f"em dashes found in {bad}"
