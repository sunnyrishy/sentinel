"""Stage 2: class-name check, smoothing, model loading offline. Audio here is SYNTHETIC."""

from __future__ import annotations

import copy
import socket
from pathlib import Path

import numpy as np
import pytest
import yaml

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.buffer import RingBuffer
from sentinel.classify.yamnet import (
    ClassifierStage,
    ClassMapError,
    YamnetClassifier,
    build_classifier_stage,
    class_scores,
    load_class_map,
    resolve_class_indices,
)
from sentinel.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    config_from_dict,
    load_config,
    resolve_path,
)
from sentinel.metrics import Metrics
from sentinel.pipeline import Pipeline
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, AlertClass, AudioFrame

CFG = load_config()
MODEL_PRESENT = resolve_path(CFG.classifier.model_path).exists()
needs_model = pytest.mark.skipif(not MODEL_PRESENT, reason="run tools/fetch_models.py first")

FAKE_NAMES = [f"class {i}" for i in range(521)]
FAKE_NAMES[10] = "Reversing beeps"
FAKE_NAMES[20] = "Vehicle horn, car horn, honking"
FAKE_NAMES[21] = "Air horn, truck horn"
FAKE_NAMES[30] = "Fire alarm"
FAKE_NAMES[31] = "Smoke detector, smoke alarm"


class FakeClassifier:
    """Scripted classifier: returns queued score dicts, then silence."""

    def __init__(self, script: list[dict[AlertClass, float]], window_len: int = 8000) -> None:
        self.window_len = window_len
        self.class_indices = resolve_class_indices(CFG.classifier, FAKE_NAMES)
        self._script = list(script)
        self.calls = 0

    def infer(self, window: np.ndarray) -> np.ndarray:
        self.calls += 1
        scores = np.zeros(521, dtype=np.float32)
        step = self._script.pop(0) if self._script else {}
        for cls, val in step.items():
            scores[self.class_indices[cls][0]] = val
        return scores


def test_default_names_resolve_in_fake_map() -> None:
    idx = resolve_class_indices(CFG.classifier, FAKE_NAMES)
    assert idx[AlertClass.VEHICLE_HORN] == [20, 21]
    assert idx[AlertClass.FIRE_ALARM] == [30, 31]


def test_missing_class_name_fails_with_closest_names() -> None:
    raw = copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()))
    raw["classifier"]["classes"]["reversing_alarm"]["names"] = ["Reversing beep"]
    cfg = config_from_dict(raw)
    with pytest.raises(ClassMapError, match="Reversing beep") as exc:
        resolve_class_indices(cfg.classifier, FAKE_NAMES)
    assert "Closest names" in str(exc.value) and "Reversing beeps" in str(exc.value)


def test_class_scores_take_max_over_names() -> None:
    scores = np.zeros(521, dtype=np.float32)
    scores[20], scores[21] = 0.2, 0.7
    idx = resolve_class_indices(CFG.classifier, FAKE_NAMES)
    assert class_scores(scores, idx)[AlertClass.VEHICLE_HORN] == pytest.approx(0.7)


def test_missing_files_have_actionable_messages(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="fetch_models"):
        load_class_map(tmp_path / "nope.csv")


def stage_with(script, window_len=8000) -> tuple[ClassifierStage, RingBuffer]:
    clf = FakeClassifier(script, window_len)
    return ClassifierStage(clf, CFG.classifier), RingBuffer(10.0)


def feed(stage: ClassifierStage, ring: RingBuffer, seconds: float, t0: float):
    """Write audio in hop-sized steps and collect detections with the stream time."""
    out = []
    hop = stage.hop
    n_steps = int(seconds * SAMPLE_RATE) // hop
    for i in range(n_steps):
        t_end = t0 + (i + 1) * hop / SAMPLE_RATE
        frame = AudioFrame(np.zeros(hop, dtype=np.float32), t_end)
        ring.write(frame.samples, t_end)
        out.extend((t_end, d) for d in stage.step(frame, ring, lambda t=t_end: t))
    return out


def test_two_of_three_smoothing() -> None:
    hit = {AlertClass.VEHICLE_HORN: 0.9}
    stage, ring = stage_with([{}, hit, {}, hit, hit, {}, {}, {}])
    dets = feed(stage, ring, 2.0, t0=0.0)
    times = [round(t, 2) for t, d in dets if d.alert_class == AlertClass.VEHICLE_HORN]
    # first inference at 0.5 s (window full), then every 0.25 s. Hits are inferences 2 and 4, so the
    # second hit, at t = 0.5 + 3 * 0.25 = 1.25 s, is the first with 2 hits inside the last 3.
    assert times[0] == pytest.approx(1.25)
    assert all(d.stage == "classifier" and not d.provisional for _, d in dets)


def test_single_hit_does_not_trigger() -> None:
    stage, ring = stage_with([{}, {AlertClass.FIRE_ALARM: 0.95}, {}, {}, {}, {}])
    assert feed(stage, ring, 1.5, t0=0.0) == []


def test_below_threshold_does_not_trigger() -> None:
    weak = {AlertClass.REVERSING_ALARM: 0.29}
    stage, ring = stage_with([weak] * 6)
    assert feed(stage, ring, 1.5, t0=0.0) == []


def test_no_inference_until_window_is_full() -> None:
    stage, ring = stage_with([{AlertClass.FIRE_ALARM: 1.0}] * 10, window_len=16000)
    feed(stage, ring, 0.75, t0=0.0)
    assert stage.clf.calls == 0  # type: ignore[attr-defined]
    feed(stage, ring, 1.0, t0=0.75)
    assert stage.clf.calls > 0  # type: ignore[attr-defined]


def test_inference_cadence_is_hop_s() -> None:
    stage, ring = stage_with([{}] * 50)
    feed(stage, ring, 5.0, t0=0.0)
    # window fills at 0.5 s, then one inference per 0.25 s hop up to 5.0 s
    assert stage.clf.calls == 19  # type: ignore[attr-defined]


def test_onset_estimate_is_before_detection() -> None:
    hit = {AlertClass.VEHICLE_HORN: 0.9}
    stage, ring = stage_with([hit] * 6)
    _, det = feed(stage, ring, 2.0, t0=0.0)[0]
    assert det.t_onset < det.t_detect


def test_pipeline_classifier_only_alert() -> None:
    hit = {AlertClass.VEHICLE_HORN: 0.9}
    stage, _ = stage_with([{}, {}, hit, hit, hit, hit, {}, {}])
    audio = np.zeros(6 * SAMPLE_RATE, dtype=np.float32)
    shown = []

    class Disp:
        def capabilities(self):
            from sentinel.types import DisplayCaps

            return DisplayCaps(supports_blink=True)

        def show(self, alert):
            shown.append(alert)

        def clear(self, event_id=None): ...

        def close(self): ...

    metrics = Metrics()
    Pipeline(CFG, AudioFileSource(audio, t0=0.0), Disp(), metrics, classifier=stage).run_sync()
    assert [a.line1 for a in shown] == ["VEHICLE HORN"]


def test_pipeline_threaded_with_classifier_stage() -> None:
    hit = {AlertClass.FIRE_ALARM: 0.9}
    stage, _ = stage_with([hit] * 20)
    audio = np.zeros(3 * SAMPLE_RATE, dtype=np.float32)
    shown = []

    class Disp:
        def capabilities(self):
            from sentinel.types import DisplayCaps

            return DisplayCaps(supports_blink=True)

        def show(self, alert):
            shown.append(alert)

        def clear(self, event_id=None): ...

        def close(self): ...

    Pipeline(CFG, AudioFileSource(audio), Disp(), classifier=stage).run()
    assert [a.line1 for a in shown] == ["FIRE ALARM"]


# ---- real model ------------------------------------------------------------------------------
@needs_model
def test_every_configured_class_name_resolves_in_the_real_class_map() -> None:
    names = load_class_map(CFG.classifier.class_map_path)
    assert len(names) == 521
    idx = resolve_class_indices(CFG.classifier, names)
    assert all(idx[c] for c in AlertClass)


@needs_model
def test_model_loads_and_infers_with_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    clf = YamnetClassifier(CFG.classifier)
    assert clf.window_len == 15600 and clf.window_s == pytest.approx(0.975)
    scores = clf.infer(synth.noise("pink", 1.0, seed=1)[: clf.window_len])
    assert scores.shape == (521,) and scores.min() >= 0.0 and scores.max() <= 1.0
    top = clf.class_names[int(np.argmax(scores))]
    assert "noise" in top.lower()  # synthetic pink noise


@needs_model
def test_real_model_wrong_window_length_is_rejected() -> None:
    clf = YamnetClassifier(CFG.classifier)
    with pytest.raises(ValueError, match="expected 15600"):
        clf.infer(np.zeros(100, dtype=np.float32))


@needs_model
def test_real_model_scores_are_deterministic() -> None:
    clf = YamnetClassifier(CFG.classifier)
    x = synth.noise("white", 1.0, seed=3)[: clf.window_len]
    assert np.allclose(clf.infer(x), clf.infer(x))


@needs_model
def test_build_stage_respects_enabled_flag() -> None:
    assert build_classifier_stage(CFG.classifier) is not None
    raw = copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()))
    raw["classifier"]["enabled"] = False
    assert build_classifier_stage(config_from_dict(raw).classifier) is None
