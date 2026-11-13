"""Pipeline behaviour: sync replay, threaded real-time replay, privacy default."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import yaml

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.config import DEFAULT_CONFIG_PATH, SentinelConfig, config_from_dict, load_config
from sentinel.metrics import Metrics
from sentinel.pipeline import Pipeline
from sentinel.sim.scenario import Scenario, load_scenario, render
from sentinel.types import SAMPLE_RATE, Alert, DisplayCaps

ROOT = Path(__file__).resolve().parents[1]


class RecordingDisplay:
    def __init__(self) -> None:
        self.shown: list[Alert] = []
        self.cleared: list[str | None] = []

    def capabilities(self) -> DisplayCaps:
        return DisplayCaps(supports_blink=True)

    def show(self, alert: Alert) -> None:
        self.shown.append(alert)

    def clear(self, event_id=None) -> None:
        self.cleared.append(event_id)

    def close(self) -> None: ...


def cfg_with(tmp_path: Path, **overrides) -> SentinelConfig:
    raw = copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()))
    raw["recording"]["dir"] = str(tmp_path / "rec")
    for key, val in overrides.items():
        raw[key].update(val)
    return config_from_dict(raw)


def test_sync_replay_shows_alert_and_records_latency(tmp_path: Path) -> None:
    rendered = render(load_scenario(ROOT / "scenarios" / "tonal_10db.yaml"))
    metrics, display = Metrics(), RecordingDisplay()
    p = Pipeline(cfg_with(tmp_path), AudioFileSource(rendered.audio, t0=0.0), display, metrics)
    p.run_sync()
    assert len(display.shown) == 1
    assert display.shown[0].line1 == "BACKUP ALARM" and display.shown[0].line2 == "NEARBY"
    assert display.cleared  # cleared after ttl
    s = metrics.summary()
    assert s["n"] == 1
    assert 250 < s["alarm_to_display_p50_ms"] < 600  # synthetic audio, software only
    assert s["processing_p50_ms"] < 50


def test_sync_replay_is_deterministic(tmp_path: Path) -> None:
    rendered = render(load_scenario(ROOT / "scenarios" / "tonal_10db.yaml"))
    runs = []
    for _ in range(2):
        d = RecordingDisplay()
        Pipeline(cfg_with(tmp_path), AudioFileSource(rendered.audio, t0=0.0), d).run_sync()
        runs.append([(a.line1, a.event_id) for a in d.shown])
    assert runs[0] == runs[1]


def test_no_alert_on_negatives(tmp_path: Path) -> None:
    rendered = render(load_scenario(ROOT / "scenarios" / "negatives_synthetic.yaml"))
    d = RecordingDisplay()
    Pipeline(cfg_with(tmp_path), AudioFileSource(rendered.audio, t0=0.0), d).run_sync()
    assert d.shown == []


def test_threaded_realtime_replay_alerts(tmp_path: Path) -> None:
    sc = Scenario.model_validate(
        {
            "name": "rt",
            "duration_s": 5,
            "seed": 3,
            "noise": {"type": "pink", "level_dbfs": -30},
            "events": [
                {
                    "kind": "tonal_alarm",
                    "start_s": 0.6,
                    "duration_s": 3,
                    "snr_db": 10,
                    "label": "reversing_alarm",
                }
            ],
        }
    )
    rendered = render(sc)
    metrics, display = Metrics(), RecordingDisplay()
    src = AudioFileSource(rendered.audio, realtime=True)
    Pipeline(cfg_with(tmp_path), src, display, metrics).run()
    assert len(display.shown) == 1
    s = metrics.summary()
    assert s["n"] == 1 and 250 < s["alarm_to_display_p50_ms"] < 800


def test_no_audio_written_by_default(tmp_path: Path) -> None:
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    Pipeline(cfg_with(tmp_path), AudioFileSource(audio, t0=0.0), RecordingDisplay()).run_sync()
    assert not (tmp_path / "rec").exists()


def test_record_flag_writes_audio(tmp_path: Path) -> None:
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    Pipeline(
        cfg_with(tmp_path), AudioFileSource(audio, t0=0.0), RecordingDisplay(), record=True
    ).run_sync()
    assert len(list((tmp_path / "rec").glob("*.wav"))) == 1


def test_default_config_has_recording_off() -> None:
    assert load_config().recording.enabled is False


def test_gate_disabled_means_no_detections(tmp_path: Path) -> None:
    rendered = render(load_scenario(ROOT / "scenarios" / "tonal_10db.yaml"))
    d = RecordingDisplay()
    cfg = cfg_with(tmp_path, gate={"enabled": False})
    Pipeline(cfg, AudioFileSource(rendered.audio, t0=0.0), d).run_sync()
    assert d.shown == []


def test_threaded_pipeline_survives_failing_display(tmp_path: Path) -> None:
    class Boom(RecordingDisplay):
        def show(self, alert: Alert) -> None:
            raise RuntimeError("display exploded")

    audio = render(load_scenario(ROOT / "scenarios" / "tonal_10db.yaml")).audio[8 * SAMPLE_RATE :]
    metrics = Metrics()
    Pipeline(cfg_with(tmp_path), AudioFileSource(audio), Boom(), metrics).run()
    assert metrics.summary()["n"] == 0  # a display that raised never counts as shown


# ---- policy and log through the pipeline (M4) --------------------------------------------------
def run_to_log(tmp_path: Path, scenario: dict, classifier=None, label: str = "synthetic"):
    from sentinel.eventlog import EventLog

    rendered = render(Scenario.model_validate(scenario))
    log = EventLog()
    cfg = cfg_with(tmp_path)
    Pipeline(
        cfg,
        AudioFileSource(rendered.audio, t0=0.0),
        RecordingDisplay(),
        eventlog=log,
        classifier=classifier,
        source_label=label,
    ).run_sync()
    return log, cfg


BASE = {"duration_s": 30, "seed": 7, "noise": {"type": "pink", "level_dbfs": -30}}


def test_replay_event_lands_in_sqlite_with_stage_latency_and_hash(tmp_path: Path) -> None:
    sc = {
        **BASE,
        "name": "t",
        "events": [
            {
                "kind": "tonal_alarm",
                "start_s": 10,
                "duration_s": 6,
                "snr_db": 10,
                "label": "reversing_alarm",
            }
        ],
    }
    log, cfg = run_to_log(tmp_path, sc)
    rows = log.list()
    assert len(rows) == 1
    r = rows[0]
    assert r["alert_class"] == "reversing_alarm" and r["stage"] == "gate"
    assert r["confirmed"] == 1 and r["cancel_reason"] is None
    assert r["source"] == "synthetic" and r["config_hash"] == cfg.config_hash()
    assert r["processing_ms"] is not None and r["processing_ms"] < 50
    assert 250 < r["alarm_to_display_ms"] < 600
    assert r["t_display"] > r["t_detect"] > r["t_onset"]
    assert r["acknowledged"] == 0


def test_unconfirmed_provisional_is_logged_as_timeout(tmp_path: Path) -> None:
    # A tone that starts mid-stream raises one provisional alert and is never confirmed (D9).
    sc = {
        **BASE,
        "name": "t",
        "events": [{"kind": "tone", "start_s": 5, "duration_s": 8, "snr_db": 10}],
    }
    log, _ = run_to_log(tmp_path, sc)
    rows = log.list()
    assert len(rows) == 1
    assert rows[0]["confirmed"] == 0 and rows[0]["cancel_reason"] == "timeout"
    assert rows[0]["stage"] == "gate"


def test_negatives_leave_the_log_empty(tmp_path: Path) -> None:
    sc = {
        **BASE,
        "name": "t",
        "events": [
            {"kind": "beep", "start_s": 5, "duration_s": 0.1, "snr_db": 10},
            {"kind": "click", "start_s": 9, "snr_db": 10},
        ],
    }
    log, _ = run_to_log(tmp_path, sc)
    assert log.count() == 0


def test_source_label_is_stored(tmp_path: Path) -> None:
    sc = {
        **BASE,
        "name": "t",
        "events": [
            {
                "kind": "tonal_alarm",
                "start_s": 10,
                "duration_s": 6,
                "snr_db": 10,
                "label": "reversing_alarm",
            }
        ],
    }
    log, _ = run_to_log(tmp_path, sc, label="replay")
    assert log.list()[0]["source"] == "replay"


def test_listeners_receive_event_messages_only(tmp_path: Path) -> None:
    """Pipeline listeners get 'event' messages. Alert and clear come from the display adapter."""
    rendered = render(load_scenario(ROOT / "scenarios" / "tonal_10db.yaml"))
    msgs: list[dict] = []
    p = Pipeline(cfg_with(tmp_path), AudioFileSource(rendered.audio, t0=0.0), RecordingDisplay())
    p.add_listener(msgs.append)
    p.run_sync()
    assert {m["type"] for m in msgs} == {"event"}
    first = msgs[0]["event"]
    assert first["id"] == "e_000001" and first["alert_class"] == "reversing_alarm"
    assert first["confirmed"] is False and first["ts_wall"].endswith("+00:00")
    assert any(m["event"].get("confirmed") is True for m in msgs)
    assert any("alarm_to_display_ms" in m["event"] for m in msgs)


def test_run_replay_tool_writes_events_to_sqlite(tmp_path: Path, capsys) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_replay", ROOT / "tools" / "run_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db = tmp_path / "ev.sqlite"
    rc = mod.main([str(ROOT / "scenarios" / "tonal_10db.yaml"), "--no-classifier", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "provisional" in out and "CONFIRMED" in out and "1 new event" in out
    from sentinel.eventlog import EventLog

    rows = EventLog(db).list()
    assert len(rows) == 1 and rows[0]["stage"] == "gate" and rows[0]["source"] == "synthetic"


def test_threaded_run_over_a_fast_file_source_loses_no_frames(tmp_path: Path) -> None:
    """Regression: an unpaced source used to overflow the queues and silently drop audio."""
    sc = Scenario.model_validate(
        {
            "name": "fast",
            "duration_s": 200,
            "seed": 9,
            "noise": {"type": "pink", "level_dbfs": -30},
            "events": [
                {
                    "kind": "tonal_alarm",
                    "start_s": 20 + 40 * i,
                    "duration_s": 6,
                    "snr_db": 8,
                    "label": "reversing_alarm",
                }
                for i in range(4)
            ],
        }
    )
    rendered = render(sc)
    metrics, display = Metrics(), RecordingDisplay()
    Pipeline(cfg_with(tmp_path), AudioFileSource(rendered.audio), display, metrics).run()
    assert metrics.dropped_frames == 0
    assert len(display.shown) == 4
