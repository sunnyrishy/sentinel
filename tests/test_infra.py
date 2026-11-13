"""Ring buffer, metrics, resampler, mic source (fake stream), displays and recording."""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.adapters.audio_mic import AudioMicSource, StreamResampler
from sentinel.adapters.base import AsyncDisplay, fit_lines
from sentinel.adapters.display_console import ConsoleDisplay
from sentinel.buffer import RingBuffer
from sentinel.metrics import CpuMeter, Metrics, percentile
from sentinel.recording import WavRecorder
from sentinel.types import SAMPLE_RATE, Alert, AlertClass, DisplayCaps


def make_alert(line1: str = "BACKUP ALARM", line2: str | None = "NEARBY", **kw) -> Alert:
    return Alert(
        "e_000001", AlertClass.REVERSING_ALARM, 1, line1, line2, kw.get("blink", False), 3.0
    )


# ---- ring buffer ----------------------------------------------------------------------------
def test_ring_buffer_latest_before_and_after_wrap() -> None:
    rb = RingBuffer(seconds=1.0, sample_rate=10)  # capacity 10
    rb.write(np.arange(4, dtype=np.float32), t_end=1.0)
    assert rb.latest(6).tolist() == [0, 0, 0, 1, 2, 3]  # left padded with zeros
    rb.write(np.arange(4, 12, dtype=np.float32), t_end=2.0)  # wraps
    assert rb.latest(10).tolist() == list(range(2, 12))
    assert rb.latest(3).tolist() == [9, 10, 11]
    assert rb.total_written == 12 and rb.t_end == 2.0


def test_ring_buffer_window_is_aligned_to_a_stream_position() -> None:
    rb = RingBuffer(seconds=1.0, sample_rate=10)  # capacity 10
    rb.write(np.arange(25, dtype=np.float32), 1.0)  # writer is ahead: holds samples 15..24
    assert rb.window(20, 3).tolist() == [17, 18, 19]
    assert rb.window(25, 10).tolist() == list(range(15, 25))
    assert rb.window(18, 6).tolist() == [0, 0, 0, 15, 16, 17]  # oldest part has been overwritten
    assert rb.window(100, 2).tolist() == [23, 24]  # clamped to what exists


def test_ring_buffer_oversized_write_keeps_tail() -> None:
    rb = RingBuffer(seconds=1.0, sample_rate=10)
    rb.write(np.arange(25, dtype=np.float32), 1.0)
    assert rb.latest(10).tolist() == list(range(15, 25))


def test_ring_buffer_is_thread_safe() -> None:
    rb = RingBuffer(seconds=2.0, sample_rate=1000)
    errors: list[Exception] = []

    def writer() -> None:
        for i in range(500):
            rb.write(np.full(7, i, dtype=np.float32), float(i))

    def reader() -> None:
        try:
            for _ in range(500):
                assert len(rb.latest(100)) == 100
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    ts = [threading.Thread(target=f) for f in (writer, writer, reader)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and rb.total_written == 7000


# ---- metrics --------------------------------------------------------------------------------
def test_metrics_percentiles() -> None:
    m = Metrics()
    for i in range(1, 101):
        m.record_latency(f"e{i}", t_onset=0.0, t_detect=i / 1000, t_display=i / 1000 + 0.001)
    s = m.summary()
    assert s["n"] == 100
    assert s["processing_p50_ms"] == pytest.approx(1.0)
    assert s["alarm_to_display_p50_ms"] == pytest.approx(51.0, abs=1.0)
    assert s["alarm_to_display_p95_ms"] == pytest.approx(96.0, abs=1.0)
    assert percentile([], 50) is None
    assert Metrics().summary()["processing_p50_ms"] is None


def test_cpu_meter_reports_busy_work() -> None:
    cpu = CpuMeter()
    t_end = time.perf_counter() + 0.2
    while time.perf_counter() < t_end:
        pass
    assert 0.3 < cpu.usage() <= 1.5


# ---- resampler and sources ------------------------------------------------------------------
@pytest.mark.parametrize("rate", [48000, 44100, 22050])
def test_stream_resampler_preserves_tone_frequency(rate: int) -> None:
    t = np.arange(rate * 2) / rate
    x = (0.5 * np.sin(2 * np.pi * 1900 * t)).astype(np.float32)
    rs = StreamResampler(rate, SAMPLE_RATE)
    y = np.concatenate([rs.process(x[i : i + 1024]) for i in range(0, len(x), 1024)])
    assert abs(len(y) - 2 * SAMPLE_RATE) < 40
    spec = np.abs(np.fft.rfft(y[4000:20000] * np.hanning(16000)))
    assert np.argmax(spec) * SAMPLE_RATE / 16000 == pytest.approx(1900, abs=5)
    assert np.max(np.abs(y[2000:])) == pytest.approx(0.5, abs=0.05)


class FakeStream:
    def __init__(self, rate: int, callback) -> None:
        self.rate, self.callback, self.started, self.closed = rate, callback, False, False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None: ...

    def close(self) -> None:
        self.closed = True

    def feed(self, samples: np.ndarray, block: int) -> None:
        for i in range(0, len(samples), block):
            self.callback(samples[i : i + block, None], block, None, None)


def test_mic_source_yields_frames_at_16k() -> None:
    holder: list[FakeStream] = []

    def factory(device, rate, block, cb):
        holder.append(FakeStream(rate, cb))
        return holder[0]

    src = AudioMicSource(stream_factory=factory)
    assert holder[0].started and holder[0].rate == SAMPLE_RATE
    data = np.linspace(-0.5, 0.5, 512 * 5, dtype=np.float32)
    holder[0].feed(data, 512)
    it = src.frames()
    got = [next(it) for _ in range(5)]
    assert np.allclose(np.concatenate([f.samples for f in got]), data)
    assert all(f.t_end > 0 for f in got)
    src.close()
    assert holder[0].closed


def test_mic_source_falls_back_to_native_rate_and_resamples() -> None:
    holder: list[FakeStream] = []

    def factory(device, rate, block, cb):
        if rate == SAMPLE_RATE:
            raise OSError("invalid sample rate")
        holder.append(FakeStream(rate, cb))
        return holder[0]

    src = AudioMicSource(stream_factory=factory, native_rate=48000)
    assert holder[0].rate == 48000
    t = np.arange(48000) / 48000
    holder[0].feed((0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32), 1536)
    it = src.frames()
    n = sum(len(next(it).samples) for _ in range(31))
    assert abs(n - 31 * 512) < 20
    src.close()


def test_mic_source_reports_failure_clearly() -> None:
    def factory(*_args):
        raise OSError("no device")

    with pytest.raises(RuntimeError, match="Could not open the microphone"):
        AudioMicSource(stream_factory=factory, native_rate=48000)


def test_mic_queue_overflow_counts_drops() -> None:
    m = Metrics()
    holder: list[FakeStream] = []

    def factory(device, rate, block, cb):
        holder.append(FakeStream(rate, cb))
        return holder[0]

    AudioMicSource(stream_factory=factory, metrics=m, max_queue=3)
    holder[0].feed(np.zeros(512 * 10, dtype=np.float32), 512)
    assert m.dropped_frames == 7


def test_file_source_virtual_and_realtime_timing() -> None:
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    frames = list(AudioFileSource(audio, frame_ms=32, t0=100.0).frames())
    assert len(frames) == 32 and frames[-1].t_end == pytest.approx(101.0)
    slept: list[float] = []
    clock = iter(np.arange(0, 100, 0.001))
    src = AudioFileSource(
        audio[:3200], 32, realtime=True, sleep=slept.append, clock=lambda: next(clock)
    )
    list(src.frames())
    assert slept and all(s > 0 for s in slept)


# ---- displays -------------------------------------------------------------------------------
def test_fit_lines_truncates_to_caps() -> None:
    caps = DisplayCaps(lines=2, max_chars_per_line=6)
    assert fit_lines(make_alert(), caps) == ["BACKUP", "NEARBY"]
    assert fit_lines(make_alert("VEHICLE HORN", None), caps) == ["VEHICLE"[:6]]
    assert fit_lines(make_alert(), DisplayCaps(lines=1, max_chars_per_line=12)) == ["BACKUP ALARM"]


def test_console_display_prints_truncated_text() -> None:
    buf = io.StringIO()
    d = ConsoleDisplay(DisplayCaps(max_chars_per_line=8), stream=buf)
    d.show(make_alert())
    d.clear("e_000001")
    out = buf.getvalue()
    assert "BACKUP A | NEARBY" in out and "clear e_000001" in out


class SlowDisplay:
    def __init__(self, delay: float) -> None:
        self.delay, self.shown, self.cleared = delay, [], []

    def capabilities(self) -> DisplayCaps:
        return DisplayCaps(supports_blink=True)

    def show(self, alert: Alert) -> None:
        time.sleep(self.delay)
        self.shown.append(alert.event_id)

    def clear(self, event_id=None) -> None:
        self.cleared.append(event_id)

    def close(self) -> None: ...


def test_slow_show_does_not_block_caller_and_times_out() -> None:
    inner = SlowDisplay(delay=1.0)
    timeouts: list[int] = []
    shown: list[str] = []
    d = AsyncDisplay(
        inner,
        call_timeout_s=0.1,
        on_timeout=lambda: timeouts.append(1),
        on_shown=lambda e, t: shown.append(e),
    )
    t0 = time.perf_counter()
    for _ in range(5):
        d.show(make_alert())
        d.clear()
    assert time.perf_counter() - t0 < 0.05  # the caller never waits for the display
    time.sleep(0.8)
    assert timeouts  # slow calls were abandoned
    assert not shown  # never reported as shown in time
    assert inner.cleared  # later commands still ran
    d.close()


def test_async_display_reports_show_time() -> None:
    inner = SlowDisplay(delay=0.0)
    got: list[tuple[str, float]] = []
    d = AsyncDisplay(inner, on_shown=lambda e, t: got.append((e, t)))
    d.show(make_alert())
    deadline = time.time() + 1
    while not got and time.time() < deadline:
        time.sleep(0.01)
    assert got and got[0][0] == "e_000001"
    d.close()


def test_blink_is_emulated_when_adapter_cannot_blink() -> None:
    class NoBlink(SlowDisplay):
        def capabilities(self) -> DisplayCaps:
            return DisplayCaps(supports_blink=False)

    inner = NoBlink(0.0)
    d = AsyncDisplay(inner, blink_period_s=0.05)
    d.show(make_alert(blink=True))
    time.sleep(0.4)
    d.clear("e_000001")
    time.sleep(0.1)
    assert len(inner.shown) >= 3 and len(inner.cleared) >= 3  # show and clear cycles
    n = len(inner.shown)
    time.sleep(0.2)
    assert len(inner.shown) == n  # stopped after clear
    d.close()


def test_blink_not_emulated_when_adapter_supports_it() -> None:
    inner = SlowDisplay(0.0)
    d = AsyncDisplay(inner, blink_period_s=0.05)
    d.show(make_alert(blink=True))
    time.sleep(0.3)
    assert inner.shown == ["e_000001"]
    d.close()


# ---- recording ------------------------------------------------------------------------------
def test_recorder_writes_wav(tmp_path: Path) -> None:
    import soundfile as sf

    rec = WavRecorder(tmp_path)
    rec.write(np.zeros(1600, dtype=np.float32))
    rec.close()
    data, sr = sf.read(str(rec.path))
    assert sr == SAMPLE_RATE and len(data) == 1600
