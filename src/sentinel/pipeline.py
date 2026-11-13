"""Wires source, ring buffer, detection stages, policy, display and metrics.

Two execution modes share the same stage objects:
  * run():       threaded, wall-clock. Live microphone and real-time replay.
  * run_sync():  single thread, virtual stream clock. Deterministic, fast, used by tests and eval.
"""

from __future__ import annotations

import contextlib
import dataclasses
import itertools
import queue
import threading
import time
from collections.abc import Callable

from sentinel.adapters.base import AsyncDisplay
from sentinel.buffer import RingBuffer
from sentinel.config import SentinelConfig
from sentinel.gate.tonal import GateParams, GateState, process
from sentinel.metrics import Metrics
from sentinel.recording import WavRecorder
from sentinel.types import (
    ALERT_WORDING,
    SAMPLE_RATE,
    Alert,
    AlertClass,
    AudioFrame,
    AudioSource,
    Detection,
    DisplayAdapter,
)

Clock = Callable[[], float]


class StreamClock:
    """Virtual clock for non-real-time replay: the time of the last frame plus compute elapsed."""

    def __init__(self) -> None:
        self._t: float | None = None
        self._wall = 0.0

    def mark(self, t_stream: float) -> None:
        self._t = t_stream
        self._wall = time.perf_counter()

    def __call__(self) -> float:
        if self._t is None:
            return 0.0
        return self._t + (time.perf_counter() - self._wall)


class _SyncDisplay:
    """Calls the adapter inline and reports t_display. Used by run_sync (deterministic)."""

    def __init__(self, inner: DisplayAdapter, clock: Clock, on_shown: Callable[[str, float], None]):
        self._inner, self._clock, self._on_shown = inner, clock, on_shown

    def capabilities(self):
        return self._inner.capabilities()

    def show(self, alert: Alert) -> None:
        self._inner.show(alert)
        self._on_shown(alert.event_id, self._clock())

    def clear(self, event_id: str | None = None) -> None:
        self._inner.clear(event_id)

    def close(self) -> None:
        self._inner.close()


class GateStage:
    """Stateful wrapper around the pure gate. Stamps t_detect with the clock so compute counts."""

    def __init__(self, cfg: SentinelConfig) -> None:
        self.params = GateParams.from_config(cfg.gate, cfg.audio.sample_rate)
        self.state = GateState()

    def step(self, frame: AudioFrame, clock: Clock) -> list[Detection]:
        dets = process(self.state, frame.samples, frame.t_end, self.params)
        if not dets:
            return dets
        now = clock()
        return [dataclasses.replace(d, t_detect=now) for d in dets]


def make_alert(
    alert_class: AlertClass, event_id: str, cfg: SentinelConfig, blink: bool | None = None
) -> Alert:
    tier, line1, line2 = ALERT_WORDING[alert_class]
    return Alert(
        event_id=event_id,
        alert_class=alert_class,
        tier=tier,
        line1=line1,
        line2=line2,
        blink=cfg.policy.blink if blink is None else blink,
        ttl_s=cfg.policy.ttl_s,
    )


class Pipeline:
    def __init__(
        self,
        cfg: SentinelConfig,
        source: AudioSource,
        display: DisplayAdapter,
        metrics: Metrics | None = None,
        on_latency: Callable[[str, float, float], None] | None = None,
        record: bool = False,
        on_detection: Callable[[Detection], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.source = source
        self._inner = display
        self.display: DisplayAdapter = display
        self.metrics = metrics or Metrics()
        self.on_latency = on_latency
        self.on_detection = on_detection
        self.ring = RingBuffer(cfg.buffer.seconds, cfg.audio.sample_rate)
        self.gate = GateStage(cfg) if cfg.gate.enabled else None
        self.clock: Clock = time.monotonic
        self._recorder = (
            WavRecorder(cfg.recording.dir) if (record or cfg.recording.enabled) else None
        )
        self._stop = threading.Event()
        self._ids = itertools.count(1)
        self._pending: dict[str, Detection] = {}
        self._last_shown: dict[AlertClass, float] = {}
        self._active: tuple[str, float] | None = None  # (event_id, clear time)
        self._gate_q: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=200)
        self._policy_q: queue.Queue[Detection | None] = queue.Queue()

    # Interim alert handling. Replaced by the full policy state machine in M4. ------------------
    def _handle_detection(self, det: Detection) -> None:
        if self.on_detection:
            self.on_detection(det)
        now = self.clock()
        cool = self.cfg.policy.cooldown_s + self.cfg.policy.ttl_s
        if now - self._last_shown.get(det.alert_class, -1e9) < cool:
            return
        event_id = f"e_{next(self._ids):06d}"
        self._pending[event_id] = det
        self._last_shown[det.alert_class] = now
        self._active = (event_id, now + self.cfg.policy.ttl_s)
        self.display.show(make_alert(det.alert_class, event_id, self.cfg))

    def _tick(self) -> None:
        if self._active and self.clock() >= self._active[1]:
            self.display.clear(self._active[0])
            self._active = None

    def _shown(self, event_id: str, t_display: float) -> None:
        det = self._pending.get(event_id)
        if det is None:
            return
        s = self.metrics.record_latency(event_id, det.t_onset, det.t_detect, t_display)
        if self.on_latency:
            self.on_latency(event_id, s.processing_ms, s.alarm_to_display_ms)

    # Shared per-frame work ---------------------------------------------------------------------
    def _ingest(self, frame: AudioFrame) -> None:
        self.ring.write(frame.samples, frame.t_end)
        if self._recorder:
            self._recorder.write(frame.samples)

    def _stages(self, frame: AudioFrame) -> list[Detection]:
        return self.gate.step(frame, self.clock) if self.gate else []

    # Synchronous mode --------------------------------------------------------------------------
    def run_sync(self) -> None:
        """Process the whole source on this thread with a virtual clock. Deterministic."""
        clock = StreamClock()
        self.clock = clock
        self.display = _SyncDisplay(self._inner, clock, self._shown)
        for frame in self.source.frames():
            clock.mark(frame.t_end)
            self._ingest(frame)
            for det in self._stages(frame):
                self._handle_detection(det)
            self._tick()
        self._finish()

    # Threaded mode -----------------------------------------------------------------------------
    def run(self, max_seconds: float | None = None) -> None:
        """Threaded wall-clock run. Blocks until the source ends, stop() or max_seconds."""
        self.clock = time.monotonic
        self.display = AsyncDisplay(
            self._inner,
            clock=lambda: self.clock(),
            on_shown=self._shown,
            on_timeout=self.metrics.count_display_timeout,
        )
        workers = [
            threading.Thread(target=self._gate_worker, name="gate", daemon=True),
            threading.Thread(target=self._policy_worker, name="policy", daemon=True),
        ]
        for w in workers:
            w.start()
        started = time.monotonic()
        try:
            for frame in self.source.frames():
                if self._stop.is_set():
                    break
                self._ingest(frame)
                self._offer(frame)
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    break
        finally:
            self._gate_q.put(None)
            for w in workers:
                w.join(timeout=5)
            self._finish()

    def stop(self) -> None:
        self._stop.set()
        self.source.close()

    def _offer(self, frame: AudioFrame) -> None:
        try:
            self._gate_q.put_nowait(frame)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._gate_q.get_nowait()
            self.metrics.count_drop()
            self._gate_q.put_nowait(frame)

    def _gate_worker(self) -> None:
        while True:
            frame = self._gate_q.get()
            if frame is None:
                self._policy_q.put(None)
                return
            for det in self._stages(frame):
                self._policy_q.put(det)

    def _policy_worker(self) -> None:
        while True:
            try:
                det = self._policy_q.get(timeout=0.05)
            except queue.Empty:
                self._tick()
                continue
            if det is None:
                return
            self._handle_detection(det)
            self._tick()

    def _finish(self) -> None:
        if self._recorder:
            self._recorder.close()
        self.display.close()
        self.source.close()


def frames_for_seconds(seconds: float) -> int:
    return int(seconds * SAMPLE_RATE)
