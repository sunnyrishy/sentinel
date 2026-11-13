"""Live microphone source using sounddevice. Resamples to 16 kHz mono float32.

Real microphone behaviour cannot be unit tested without hardware, so the stream is created through
an injectable factory. Tests drive the same code path with a fake stream.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

from sentinel.metrics import Metrics
from sentinel.types import SAMPLE_RATE, AudioFrame

StreamFactory = Callable[[str | int | None, int, int, Callable[..., None]], Any]


class StreamResampler:
    """Stateful low-pass plus linear-interpolation resampler for chunked audio.

    Adequate for alarm detection (tones and broadband bursts below 4 kHz). Not meant for hi-fi.
    """

    def __init__(self, in_rate: int, out_rate: int = SAMPLE_RATE) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._step = in_rate / out_rate
        self._pass = in_rate == out_rate
        if not self._pass:
            cutoff = min(0.45 * out_rate, 0.45 * in_rate) / (in_rate / 2)
            self._sos = butter(6, cutoff, output="sos")
            self._zi = sosfilt_zi(self._sos) * 0.0
        self._pos = 0.0  # position of the next output sample, relative to the next block start
        self._prev = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        if self._pass:
            return x.astype(np.float32, copy=False)
        y, self._zi = sosfilt(self._sos, x.astype(np.float64), zi=self._zi)
        n = len(y)
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        ext = np.concatenate([[self._prev], y])  # ext[i + 1] is y[i]; ext[0] is the last old sample
        count = max(0, int(np.floor((n - 1 - self._pos) / self._step)) + 1)
        t = self._pos + self._step * np.arange(count)
        lo = np.floor(t).astype(int)
        frac = t - lo
        hi = np.minimum(lo + 2, n)
        out = ext[lo + 1] * (1 - frac) + ext[hi] * frac
        self._pos = self._pos + self._step * count - n
        self._prev = float(y[-1])
        return out.astype(np.float32)


def _default_factory(
    device: str | int | None, rate: int, blocksize: int, callback: Callable[..., None]
) -> Any:
    try:
        import sounddevice as sd
    except OSError as err:  # PortAudio shared library missing
        raise RuntimeError(
            "sounddevice could not load PortAudio. Install it (Linux: apt install libportaudio2, "
            "macOS and Windows wheels bundle it) or use a wav replay instead."
        ) from err
    return sd.InputStream(
        device=device,
        samplerate=rate,
        blocksize=blocksize,
        channels=1,
        dtype="float32",
        callback=callback,
    )


def list_devices() -> str:
    try:
        import sounddevice as sd
    except OSError as err:
        return f"sounddevice unavailable: {err}"
    return str(sd.query_devices())


class AudioMicSource:
    """Captures mono audio. The callback only copies into a bounded queue (spec rule)."""

    sample_rate = SAMPLE_RATE

    def __init__(
        self,
        device: str | int | None = None,
        frame_ms: int = 32,
        stream_factory: StreamFactory | None = None,
        native_rate: int | None = None,
        metrics: Metrics | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_queue: int = 400,
    ) -> None:
        self._clock = clock
        self._metrics = metrics
        self._q: queue.Queue[tuple[np.ndarray, float]] = queue.Queue(maxsize=max_queue)
        self._closed = False
        self._factory = stream_factory or _default_factory
        self._blocksize_16k = max(1, SAMPLE_RATE * frame_ms // 1000)
        self._stream, rate = self._open(device, native_rate)
        self._resampler = StreamResampler(rate, SAMPLE_RATE)
        self._stream.start()

    def _open(self, device: str | int | None, native_rate: int | None) -> tuple[Any, int]:
        """Prefer opening at 16 kHz. Fall back to the device rate and resample ourselves."""
        try:
            return self._factory(
                device, SAMPLE_RATE, self._blocksize_16k, self._callback
            ), SAMPLE_RATE
        except Exception as first:
            rate = native_rate or self._query_default_rate(device)
            if rate is None or rate == SAMPLE_RATE:
                raise RuntimeError(f"Could not open the microphone: {first}") from first
            block = max(1, rate * self._blocksize_16k // SAMPLE_RATE)
            try:
                return self._factory(device, rate, block, self._callback), rate
            except Exception as second:
                raise RuntimeError(f"Could not open the microphone: {second}") from second

    @staticmethod
    def _query_default_rate(device: str | int | None) -> int | None:
        try:
            import sounddevice as sd

            return int(sd.query_devices(device, "input")["default_samplerate"])
        except Exception:
            return None

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        t = self._clock()
        try:
            self._q.put_nowait((np.array(indata[:, 0], dtype=np.float32), t))
        except queue.Full:
            if self._metrics:
                self._metrics.count_drop()

    def frames(self) -> Iterator[AudioFrame]:
        while not self._closed:
            try:
                raw, t_end = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            out = self._resampler.process(raw)
            if len(out):
                yield AudioFrame(np.clip(out, -1.0, 1.0), t_end)

    def close(self) -> None:
        self._closed = True
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
