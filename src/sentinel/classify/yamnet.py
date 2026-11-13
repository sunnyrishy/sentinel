"""Stage 2: YAMNet classifier (TFLite). Confirms the class; covers horns, fire, wideband alarms.

Runs fully offline once the model and class map are in models/ (tools/fetch_models.py).
"""

from __future__ import annotations

import csv
import difflib
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from sentinel.buffer import RingBuffer
from sentinel.config import ClassifierConfig, ConfigError, resolve_path
from sentinel.types import SAMPLE_RATE, AlertClass, AudioFrame, Detection

EXPECTED_CLASSES = 521


class ClassMapError(ConfigError):
    """A configured class name is missing from the class map."""


def load_class_map(path: str | Path) -> list[str]:
    """Display names in model output order."""
    p = resolve_path(path)
    if not p.exists():
        raise ConfigError(f"Class map not found: {p}. Run: python tools/fetch_models.py")
    with p.open(newline="") as f:
        names = [row["display_name"] for row in csv.DictReader(f)]
    if len(names) != EXPECTED_CLASSES:
        raise ConfigError(f"Class map {p} has {len(names)} rows, expected {EXPECTED_CLASSES}")
    return names


def resolve_class_indices(
    cfg: ClassifierConfig, class_names: list[str]
) -> dict[AlertClass, list[int]]:
    """Map each Sentinel class to the class-map indices of its configured names.

    Matching is by exact display name. Missing names fail with the closest suggestions.
    """
    lookup = {name: i for i, name in enumerate(class_names)}
    problems: list[str] = []
    out: dict[AlertClass, list[int]] = {}
    for cls_key, rule in cfg.classes.items():
        idx: list[int] = []
        for name in rule.names:
            if name in lookup:
                idx.append(lookup[name])
            else:
                close = difflib.get_close_matches(name, class_names, n=5, cutoff=0.4)
                problems.append(
                    f"  {cls_key}: '{name}' is not in the class map. Closest names: {close}"
                )
        out[AlertClass(cls_key)] = idx
    if problems:
        raise ClassMapError("Unknown classifier class names:\n" + "\n".join(problems))
    return out


def _make_interpreter(model_path: Path, num_threads: int) -> Any:
    """Prefer ai-edge-litert, then tflite-runtime, then tensorflow."""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter  # type: ignore[no-redef]
        except ImportError:
            try:
                from tensorflow.lite import Interpreter  # type: ignore[no-redef]
            except ImportError as err:
                raise RuntimeError(
                    "No TFLite runtime found. Install one: pip install ai-edge-litert"
                ) from err
    return Interpreter(model_path=str(model_path), num_threads=num_threads)


class Classifier(Protocol):
    """What ClassifierStage needs. YamnetClassifier implements it; tests use a fake."""

    window_len: int
    class_indices: dict[AlertClass, list[int]]

    def infer(self, window: np.ndarray) -> np.ndarray: ...


class YamnetClassifier:
    def __init__(
        self,
        cfg: ClassifierConfig,
        num_threads: int = 2,
        interpreter_factory: Callable[[Path, int], Any] = _make_interpreter,
    ) -> None:
        self.class_names = load_class_map(cfg.class_map_path)
        self.class_indices = resolve_class_indices(cfg, self.class_names)
        model = resolve_path(cfg.model_path)
        if not model.exists():
            raise ConfigError(f"Model not found: {model}. Run: python tools/fetch_models.py")
        self._interp = interpreter_factory(model, num_threads)
        self._interp.allocate_tensors()
        inp = self._interp.get_input_details()[0]
        self._in_index = inp["index"]
        self._in_shape = tuple(int(x) for x in inp["shape"])
        self.window_len = int(np.prod(self._in_shape))
        self._out_index = self._interp.get_output_details()[0]["index"]

    @property
    def window_s(self) -> float:
        return self.window_len / SAMPLE_RATE

    def infer(self, window: np.ndarray) -> np.ndarray:
        """Run one inference on exactly window_len samples. Returns the 521 class scores."""
        if len(window) != self.window_len:
            raise ValueError(f"expected {self.window_len} samples, got {len(window)}")
        x = np.asarray(window, dtype=np.float32).reshape(self._in_shape)
        self._interp.set_tensor(self._in_index, x)
        self._interp.invoke()
        scores = np.asarray(self._interp.get_tensor(self._out_index), dtype=np.float32)
        scores = scores.reshape(-1, scores.shape[-1]).mean(axis=0) if scores.ndim > 1 else scores
        if len(scores) != len(self.class_names):
            raise RuntimeError(
                f"model returned {len(scores)} scores but the class map has {len(self.class_names)}"
            )
        return scores

    def class_scores(self, scores: np.ndarray) -> dict[AlertClass, float]:
        return class_scores(scores, self.class_indices)


def class_scores(
    scores: np.ndarray, class_indices: dict[AlertClass, list[int]]
) -> dict[AlertClass, float]:
    """Per Sentinel class: the maximum score over its configured AudioSet class names."""
    return {c: float(max(scores[i] for i in idx)) for c, idx in class_indices.items()}


class ClassifierStage:
    """Streaming wrapper: infer every hop_s of audio, then require hits of the last windows.

    Inference is driven by the number of samples written to the ring buffer, not by wall time, so
    replays are deterministic. Onset is estimated as the middle of the first window of the current
    positive streak (see docs/DECISIONS.md D18).
    """

    def __init__(self, classifier: Classifier, cfg: ClassifierConfig) -> None:
        self.clf = classifier
        self.cfg = cfg
        self.hop = max(1, int(cfg.hop_s * SAMPLE_RATE))
        self._thresholds = {AlertClass(k): r.threshold for k, r in cfg.classes.items()}
        self._history: dict[AlertClass, deque[tuple[bool, float]]] = {
            c: deque(maxlen=cfg.smoothing.windows) for c in self._thresholds
        }
        self._seen = 0  # samples seen by this stage, i.e. position in the stream
        self._last_total = 0
        self.last_scores: dict[AlertClass, float] = {}

    def due(self, total_written: int) -> bool:
        full = total_written >= self.clf.window_len
        return full and total_written - self._last_total >= self.hop

    def step(
        self, frame: AudioFrame, ring: RingBuffer, clock: Callable[[], float] = time.monotonic
    ) -> list[Detection]:
        """Account for one frame. When a hop has elapsed, classify the window ending at this frame.

        The window is read from the ring at this stage's own stream position, so results do not
        depend on how far ahead the capture thread has run.
        """
        self._seen += len(frame.samples)
        if not self.due(self._seen):
            return []
        self._last_total = self._seen
        window = ring.window(self._seen, self.clf.window_len)
        scores = class_scores(self.clf.infer(window), self.clf.class_indices)
        return self.update(scores, frame.t_end, clock())

    def update(
        self, scores: dict[AlertClass, float], t_window_end: float, t_now: float
    ) -> list[Detection]:
        """Apply thresholds and hits-of-windows smoothing to one inference result."""
        self.last_scores = scores
        out: list[Detection] = []
        window_s = self.clf.window_len / SAMPLE_RATE
        for cls, score in scores.items():
            hist = self._history[cls]
            hist.append((score >= self._thresholds[cls], t_window_end))
            positives = [t for ok, t in hist if ok]
            if len(positives) >= self.cfg.smoothing.hits:
                out.append(
                    Detection(
                        alert_class=cls,
                        stage="classifier",
                        score=min(1.0, score),
                        provisional=False,
                        t_onset=positives[0] - window_s / 2,
                        t_detect=t_now,
                    )
                )
        return out


def build_classifier_stage(cfg: ClassifierConfig) -> ClassifierStage | None:
    """The classifier stage for a config, or None if the classifier is disabled."""
    if not cfg.enabled:
        return None
    return ClassifierStage(YamnetClassifier(cfg), cfg)
