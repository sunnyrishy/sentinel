"""Wires source, ring buffer, detection stages, policy, display, event log and metrics.

Two execution modes share the same stage objects:
  * run():       threaded, wall-clock. Live microphone and real-time replay.
  * run_sync():  single thread, virtual stream clock. Deterministic, fast, used by tests and eval.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from sentinel.adapters.base import AsyncDisplay
from sentinel.buffer import RingBuffer
from sentinel.classify.yamnet import ClassifierStage
from sentinel.config import SentinelConfig
from sentinel.eventlog import AsyncEventLog, EventLog, utc_now_iso
from sentinel.gate.tonal import GateParams, GateState, process
from sentinel.metrics import Metrics
from sentinel.policy import (
    Action,
    ClearAlert,
    CreateEvent,
    Policy,
    ShowAlert,
    UpdateEvent,
)
from sentinel.recording import WavRecorder
from sentinel.types import Alert, AudioFrame, AudioSource, Detection, DisplayAdapter

log = logging.getLogger("sentinel.pipeline")
Clock = Callable[[], float]
Listener = Callable[[dict[str, Any]], None]


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
        try:
            self._inner.show(alert)
        except Exception:
            log.exception("display show failed")  # a broken display must never stop detection
            return
        self._on_shown(alert.event_id, self._clock())

    def clear(self, event_id: str | None = None) -> None:
        try:
            self._inner.clear(event_id)
        except Exception:
            log.exception("display clear failed")

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
        classifier: ClassifierStage | None = None,
        eventlog: EventLog | None = None,
        source_label: str = "live",
    ) -> None:
        self.cfg = cfg
        self.source = source
        self._inner = display
        self.display: DisplayAdapter = display
        self.metrics = metrics or Metrics()
        self.on_latency = on_latency
        self.on_detection = on_detection
        self.source_label = source_label
        self.eventlog = eventlog if eventlog is not None else EventLog(":memory:")
        self._elog = AsyncEventLog(self.eventlog)
        self.ring = RingBuffer(cfg.buffer.seconds, cfg.audio.sample_rate)
        self.gate = GateStage(cfg) if cfg.gate.enabled else None
        self.classifier = classifier
        self.policy = Policy(cfg.policy, cfg.gate.confirm_timeout_s, self.eventlog.next_id)
        self.clock: Clock = time.monotonic
        self._config_hash = cfg.config_hash()
        self._recorder = (
            WavRecorder(cfg.recording.dir) if (record or cfg.recording.enabled) else None
        )
        self._stop = threading.Event()
        self._natural_end = False
        self._times: dict[str, tuple[float, float]] = {}  # event_id -> (t_onset, t_detect)
        self._listeners: list[Listener] = []
        self._gate_q: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=200)
        self._clf_q: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=200)
        self._policy_q: queue.Queue[Detection | None] = queue.Queue()

    def add_listener(self, callback: Listener) -> None:
        """Receive WebSocket-protocol 'event' messages as they happen. Alert and clear messages come
        from the display adapter (display_sim or display_bridge), not from the pipeline."""
        self._listeners.append(callback)

    def _emit(self, message: dict[str, Any]) -> None:
        for cb in self._listeners:
            with contextlib.suppress(Exception):
                cb(message)

    # Policy output -> display, log, listeners -------------------------------------------------
    def _handle_detection(self, det: Detection) -> None:
        if self.on_detection:
            self.on_detection(det)
        self._apply(self.policy.on_detection(det, self.clock()))

    def _tick(self) -> None:
        self._apply(self.policy.tick(self.clock()))

    def _apply(self, actions: list[Action]) -> None:
        for a in actions:
            if isinstance(a, CreateEvent):
                self._create(a)
            elif isinstance(a, ShowAlert):
                self.display.show(a.alert)
            elif isinstance(a, UpdateEvent):
                self._elog.update(a.event_id, **a.changes)
                self._emit_event(a.event_id, a.changes)
            elif isinstance(a, ClearAlert):
                self.display.clear(a.event_id)

    def _create(self, e: CreateEvent) -> None:
        self._times[e.event_id] = (e.t_onset, e.t_detect)
        ts_wall = utc_now_iso()
        self._elog.insert(
            id=e.event_id,
            ts_wall=ts_wall,
            alert_class=e.alert_class.value,
            stage=e.stage,
            score=e.score,
            confirmed=e.confirmed,
            cancel_reason=e.cancel_reason,
            t_onset=e.t_onset,
            t_detect=e.t_detect,
            source=self.source_label,
            config_hash=self._config_hash,
        )
        self._emit_event(
            e.event_id,
            {
                "ts_wall": ts_wall,
                "alert_class": e.alert_class.value,
                "score": e.score,
                "stage": e.stage,
                "confirmed": e.confirmed,
                "cancel_reason": e.cancel_reason,
                "source": self.source_label,
            },
        )

    def _emit_event(self, event_id: str, fields: dict[str, Any]) -> None:
        self._emit({"type": "event", "event": {"id": event_id, **fields}})

    def _shown(self, event_id: str, t_display: float) -> None:
        """Called when the display's show returned: record both latencies."""
        times = self._times.get(event_id)
        if times is None:
            return
        t_onset, t_detect = times
        s = self.metrics.record_latency(event_id, t_onset, t_detect, t_display)
        self._elog.update(
            event_id,
            t_display=t_display,
            processing_ms=s.processing_ms,
            alarm_to_display_ms=s.alarm_to_display_ms,
        )
        self._emit_event(
            event_id,
            {
                "processing_ms": round(s.processing_ms, 2),
                "alarm_to_display_ms": round(s.alarm_to_display_ms, 1),
            },
        )
        if self.on_latency:
            self.on_latency(event_id, s.processing_ms, s.alarm_to_display_ms)

    # Shared per-frame work ---------------------------------------------------------------------
    def _ingest(self, frame: AudioFrame) -> None:
        self.ring.write(frame.samples, frame.t_end)
        if self._recorder:
            self._recorder.write(frame.samples)

    def _gate_dets(self, frame: AudioFrame) -> list[Detection]:
        return self.gate.step(frame, self.clock) if self.gate else []

    def _clf_dets(self, frame: AudioFrame) -> list[Detection]:
        return self.classifier.step(frame, self.ring, self.clock) if self.classifier else []

    # Synchronous mode --------------------------------------------------------------------------
    def run_sync(self) -> None:
        """Process the whole source on this thread with a virtual clock. Deterministic."""
        clock = StreamClock()
        self.clock = clock
        self.display = _SyncDisplay(self._inner, clock, self._shown)
        for frame in self.source.frames():
            clock.mark(frame.t_end)
            self._ingest(frame)
            for det in self._gate_dets(frame) + self._clf_dets(frame):
                self._handle_detection(det)
            self._tick()
        self._natural_end = True
        self._finish()

    # Threaded mode -----------------------------------------------------------------------------
    def run(self, max_seconds: float | None = None) -> None:
        """Threaded wall-clock run. Blocks until the source ends, stop() or max_seconds.

        An unpaced source (a file replayed as fast as possible) has audio time that runs far ahead
        of the wall clock, so the policy's timers would be meaningless. It is run on the virtual
        clock instead, which gives the same stages and a deterministic result.
        """
        if getattr(self.source, "lossless", False):
            self.run_sync()
            return
        self.clock = time.monotonic
        self.display = AsyncDisplay(
            self._inner,
            clock=lambda: self.clock(),
            on_shown=self._shown,
            on_timeout=self.metrics.count_display_timeout,
        )
        workers = [
            threading.Thread(target=self._gate_worker, name="gate", daemon=True),
            threading.Thread(target=self._clf_worker, name="classifier", daemon=True),
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
            else:
                self._natural_end = not self._stop.is_set()
        finally:
            self._gate_q.put(None)
            self._clf_q.put(None)
            for w in workers:
                w.join(timeout=5)
            self._finish()

    def stop(self) -> None:
        self._stop.set()
        self.source.close()

    def _offer(self, frame: AudioFrame) -> None:
        lossless = bool(getattr(self.source, "lossless", False))
        for q in (self._gate_q, self._clf_q):
            if lossless:
                while not self._stop.is_set():  # backpressure: wait for the worker to catch up
                    try:
                        q.put(frame, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                continue
            try:
                q.put_nowait(frame)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    q.get_nowait()
                self.metrics.count_drop()
                q.put_nowait(frame)

    def _gate_worker(self) -> None:
        while True:
            frame = self._gate_q.get()
            if frame is None:
                self._policy_q.put(None)
                return
            for det in self._gate_dets(frame):
                self._policy_q.put(det)

    def _clf_worker(self) -> None:
        while True:
            frame = self._clf_q.get()
            if frame is None:
                self._policy_q.put(None)
                return
            for det in self._clf_dets(frame):
                self._policy_q.put(det)

    def _policy_worker(self) -> None:
        ended = 0  # one end marker from each detection worker
        while True:
            try:
                det = self._policy_q.get(timeout=0.05)
            except queue.Empty:
                self._tick()
                continue
            if det is None:
                ended += 1
                if ended >= 2:
                    return
                continue
            self._handle_detection(det)
            self._tick()

    # Shutdown ----------------------------------------------------------------------------------
    def _settle(self) -> None:
        """When the source ended on its own, resolve pending timers as if time had passed, so every
        provisional alert ends as confirmed or cancelled in the log."""
        horizon = max(self.cfg.gate.confirm_timeout_s, self.cfg.policy.ttl_s) + 0.01
        self._apply(self.policy.tick(self.clock() + horizon))

    def _finish(self) -> None:
        if self._natural_end:
            self._settle()
        else:
            self.display.clear(None)
        self.display.close()
        self._elog.close()
        if self._recorder:
            self._recorder.close()
        self.source.close()
