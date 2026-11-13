"""Latency probes, percentiles and a CPU meter. Thread-safe and dependency free."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np


def percentile(values: list[float], q: float) -> float | None:
    """q in 0..100. None for an empty list."""
    return float(np.percentile(values, q)) if values else None


@dataclass(frozen=True)
class LatencySample:
    event_id: str
    processing_ms: float  # t_display - t_detect
    alarm_to_display_ms: float  # t_display - t_onset


class Metrics:
    """Collects per-event latencies plus a few health counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: list[LatencySample] = []
        self.dropped_frames = 0
        self.display_timeouts = 0

    def record_latency(
        self, event_id: str, t_onset: float, t_detect: float, t_display: float
    ) -> LatencySample:
        sample = LatencySample(
            event_id, (t_display - t_detect) * 1000.0, (t_display - t_onset) * 1000.0
        )
        with self._lock:
            self._samples.append(sample)
        return sample

    def count_drop(self, n: int = 1) -> None:
        with self._lock:
            self.dropped_frames += n

    def count_display_timeout(self) -> None:
        with self._lock:
            self.display_timeouts += 1

    def samples(self) -> list[LatencySample]:
        with self._lock:
            return list(self._samples)

    def summary(self) -> dict[str, float | int | None]:
        s = self.samples()
        proc = [x.processing_ms for x in s]
        a2d = [x.alarm_to_display_ms for x in s]
        return {
            "n": len(s),
            "processing_p50_ms": percentile(proc, 50),
            "processing_p95_ms": percentile(proc, 95),
            "alarm_to_display_p50_ms": percentile(a2d, 50),
            "alarm_to_display_p95_ms": percentile(a2d, 95),
            "dropped_frames": self.dropped_frames,
            "display_timeouts": self.display_timeouts,
        }


class CpuMeter:
    """Process CPU time divided by wall time, as a fraction of one core."""

    def __init__(self) -> None:
        self._cpu0 = time.process_time()
        self._wall0 = time.perf_counter()

    def usage(self) -> float:
        wall = time.perf_counter() - self._wall0
        return (time.process_time() - self._cpu0) / wall if wall > 0 else 0.0
