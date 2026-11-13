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
