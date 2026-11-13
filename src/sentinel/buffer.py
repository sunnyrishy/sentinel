"""Thread-safe ring buffer of float32 mono samples."""

from __future__ import annotations

import threading

import numpy as np

from sentinel.types import SAMPLE_RATE


class RingBuffer:
    """Fixed-size circular buffer. The capture path writes, workers read the latest samples."""

    def __init__(self, seconds: float, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self.capacity = max(1, int(seconds * sample_rate))
        self._data = np.zeros(self.capacity, dtype=np.float32)
        self._total = 0  # samples written since creation
        self._t_end = 0.0
        self._lock = threading.Lock()

    @property
    def total_written(self) -> int:
        with self._lock:
            return self._total

    @property
    def t_end(self) -> float:
        """Monotonic time of the last written sample."""
        with self._lock:
            return self._t_end

    def write(self, samples: np.ndarray, t_end: float) -> None:
        x = np.asarray(samples, dtype=np.float32)
        skipped = max(0, len(x) - self.capacity)
        if skipped:
            x = x[-self.capacity :]
        with self._lock:
            self._total += skipped  # dropped samples still advance the absolute stream position
            pos = self._total % self.capacity
            first = min(len(x), self.capacity - pos)
            self._data[pos : pos + first] = x[:first]
            if first < len(x):
                self._data[: len(x) - first] = x[first:]
            self._total += len(x)
            self._t_end = t_end

    def window(self, end_index: int, n: int) -> np.ndarray:
        """The n samples ending just before absolute sample index end_index (counted from the first
        sample ever written). Lets a worker read a window aligned to the frame it is processing even
        if the writer has moved on. Samples that are not available (too old) read as zeros."""
        n = min(n, self.capacity)
        with self._lock:
            end_index = min(end_index, self._total)
            oldest = max(0, self._total - self.capacity)
            start = max(oldest, end_index - n)
            avail = max(0, end_index - start)
            idx = np.arange(start, end_index) % self.capacity
            out = np.zeros(n, dtype=np.float32)
            if avail:
                out[n - avail :] = self._data[idx]
        return out

    def latest(self, n: int) -> np.ndarray:
        """The most recent n samples, oldest first. Left-padded with zeros if not yet available."""
        n = min(n, self.capacity)
        with self._lock:
            avail = min(n, self._total)
            end = self._total % self.capacity
            idx = (np.arange(end - avail, end)) % self.capacity
            out = np.zeros(n, dtype=np.float32)
            out[n - avail :] = self._data[idx]
        return out
