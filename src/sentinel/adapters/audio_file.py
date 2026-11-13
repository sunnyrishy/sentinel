"""Wav replay source. Deterministic: same file, same frames. Also the wav loader used everywhere."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from sentinel.types import SAMPLE_RATE, AudioFrame


def resample_to_16k(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == SAMPLE_RATE:
        return x.astype(np.float32)
    g = gcd(SAMPLE_RATE, sr)
    return resample_poly(x, SAMPLE_RATE // g, sr // g).astype(np.float32)


def load_wav_16k(path: str | Path) -> np.ndarray:
    """Read any soundfile-readable file as mono 16 kHz float32 in [-1, 1]."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    return np.clip(resample_to_16k(mono, sr), -1.0, 1.0)


class AudioFileSource:
    """Replays a float32 array (or wav path) as AudioFrames.

    Stream time is virtual: frame k ends at t0 + (k+1) * frame_s. With realtime=True the source
    sleeps so frames are delivered at wall-clock speed, which makes the pipeline's monotonic
    timestamps meaningful for latency measurements. With realtime=False it runs as fast as the
    consumer pulls, still stamping virtual times, so replays are deterministic.
    """

    sample_rate = SAMPLE_RATE

    def __init__(
        self,
        audio: np.ndarray | str | Path,
        frame_ms: int = 32,
        realtime: bool = False,
        t0: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._audio = load_wav_16k(audio) if isinstance(audio, str | Path) else audio
        self._frame = max(1, int(SAMPLE_RATE * frame_ms / 1000))
        self._realtime = realtime
        self._t0 = t0
        self._sleep = sleep
        self._clock = clock
        self._closed = False
        self.started_at: float | None = None  # clock value when frames() began

    @property
    def duration_s(self) -> float:
        return len(self._audio) / SAMPLE_RATE

    def frames(self) -> Iterator[AudioFrame]:
        start_wall = self._clock()
        self.started_at = start_wall
        t0 = self._t0 if self._t0 is not None else start_wall
        for pos in range(0, len(self._audio), self._frame):
            if self._closed:
                return
            chunk = self._audio[pos : pos + self._frame]
            t_end = t0 + (pos + len(chunk)) / SAMPLE_RATE
            if self._realtime:
                delay = (start_wall + (pos + len(chunk)) / SAMPLE_RATE) - self._clock()
                if delay > 0:
                    self._sleep(delay)
                t_end = self._clock()
            yield AudioFrame(chunk.astype(np.float32, copy=False), t_end)

    def close(self) -> None:
        self._closed = True
