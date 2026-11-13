"""Print timestamps that make a true end-to-end latency measurement easy.

Software can only time the path up to the moment the display call returns. The rest (display link,
panel, photons) needs a camera. This tool plays a known alarm burst on a fixed schedule and prints
the wall-clock time of every step, so you can compare slow-motion video against the log.

Usage:
  python tools/latency_probe.py --mode simulate            # no hardware: software path only
  python tools/latency_probe.py --mode live --display bridge --probes 5

How to measure with video (live mode):
  1. Film the glasses display and the speaker together with a phone in slow motion (120 or 240 fps).
  2. Open the video in an editor. Find the frame where the alarm sound starts (look at the audio
     waveform) and the frame where the text first appears on the lens. The difference is the true
     end-to-end latency for that probe.
  3. Compare with 'software alarm-to-display' below. The gap is the display link plus the panel.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from sentinel.adapters import make_audio_source
from sentinel.adapters.audio_file import AudioFileSource
from sentinel.classify.yamnet import build_classifier_stage
from sentinel.config import ConfigError, SentinelConfig, load_config
from sentinel.metrics import Metrics, percentile
from sentinel.pipeline import Pipeline
from sentinel.runtime import start_runtime
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, Alert, DisplayAdapter, DisplayCaps

LEAD_S = 1.0
ALARM_S = 3.0
TAIL_S = 1.5


@dataclass
class Call:
    kind: str  # show or clear
    event_id: str | None
    t_start: float  # time.monotonic()
    t_end: float


@dataclass
class TimedDisplay:
    """Wraps a display and records when each call started and returned."""

    inner: DisplayAdapter
    calls: list[Call] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def capabilities(self) -> DisplayCaps:
        return self.inner.capabilities()

    def _timed(self, kind: str, event_id: str | None, fn) -> None:
        t0 = time.monotonic()
        try:
            fn()
        finally:
            with self._lock:
                self.calls.append(Call(kind, event_id, t0, time.monotonic()))

    def show(self, alert: Alert) -> None:
        self._timed("show", alert.event_id, lambda: self.inner.show(alert))

    def clear(self, event_id: str | None = None) -> None:
        self._timed("clear", event_id, lambda: self.inner.clear(event_id))

    def close(self) -> None:
        self.inner.close()


def probe_audio(
    n: int, interval_s: float, freq_hz: float = 1900.0
) -> tuple[np.ndarray, list[float]]:
    """Silence, then n tonal alarm bursts separated by interval_s. Returns audio and onset times."""
    total = LEAD_S + (n - 1) * interval_s + ALARM_S + TAIL_S
    audio = np.zeros(int(total * SAMPLE_RATE), dtype=np.float32)
    onsets = []
    for k in range(n):
        onset = LEAD_S + k * interval_s
        burst = synth.tonal_alarm(freq_hz, 70, ALARM_S) * 0.6
        s = int(onset * SAMPLE_RATE)
        audio[s : s + len(burst)] = burst
        onsets.append(onset)
    return audio, onsets


def wall(t_mono: float, mono0: float, wall0: float) -> str:
    w = wall0 + (t_mono - mono0)
    return time.strftime("%H:%M:%S", time.localtime(w)) + f".{int((w % 1) * 1000):03d}"


def play_live(audio: np.ndarray, device: str | int | None) -> float:
    """Start playing the probe through the speaker. Returns the monotonic time of the call."""
    try:
        import sounddevice as sd
    except OSError as err:
        raise RuntimeError(f"sounddevice could not load PortAudio: {err}") from err
    t = time.monotonic()
    sd.play(audio, SAMPLE_RATE, device=device)
    return t


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--mode",
        choices=["simulate", "live"],
        default="simulate",
        help="simulate feeds the probe straight into the pipeline, no hardware",
    )
    parser.add_argument(
        "--display", default=None, help="display adapter(s), e.g. bridge or console"
    )
    parser.add_argument("--probes", type=int, default=3)
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="seconds between probes (must exceed alert ttl + cooldown, 8 s)",
    )
    parser.add_argument("--device", default=None, help="microphone device for live mode")
    parser.add_argument("--output-device", default=None, help="speaker device for live mode")
    parser.add_argument("--db", default=":memory:", help="event log (default: a throwaway one)")
    parser.add_argument("--no-classifier", action="store_true")
    args = parser.parse_args(argv)
    if args.interval < 8.0:
        print(
            "--interval must be at least 8 s so cooldown does not suppress probes", file=sys.stderr
        )
        return 2

    runtime = None
    try:
        cfg: SentinelConfig = load_config(args.config)
        runtime = start_runtime(
            cfg, "live" if args.mode == "live" else "replay", args.display, args.db
        )
        stage = None if args.no_classifier else build_classifier_stage(cfg.classifier)
        audio, onsets = probe_audio(args.probes, args.interval)
        metrics = Metrics()
        if args.mode == "simulate":
            source = AudioFileSource(audio, cfg.audio.frame_ms, realtime=True)
        else:
            dev = int(args.device) if args.device and args.device.isdigit() else args.device
            source = make_audio_source(cfg, "mic", dev, metrics)
    except (ConfigError, RuntimeError, NotImplementedError) as err:
        print(err, file=sys.stderr)
        if runtime:
            runtime.close()
        return 2

    timed = TimedDisplay(runtime.display)
    pipeline = Pipeline(cfg, source, timed, metrics, classifier=stage, eventlog=runtime.eventlog,
                        source_label="live" if args.mode == "live" else "replay")  # fmt: skip
    runtime.attach(pipeline)
    mono0, wall0 = time.monotonic(), time.time()
    print(f"Probe schedule: {args.probes} bursts, {args.interval:g} s apart, mode {args.mode}")
    if runtime.url:
        print(f"Dashboard and simulator: {runtime.url}")
    if args.mode == "live":
        print("Live: the probe plays through the speaker and the microphone listens to it.")
    base_holder: list[float] = []

    def start_audio() -> None:
        if args.mode == "live":
            base_holder.append(play_live(audio, args.output_device))
        else:
            while source.started_at is None:  # type: ignore[union-attr]
                time.sleep(0.001)
            base_holder.append(source.started_at)  # type: ignore[union-attr]

    starter = threading.Thread(target=start_audio, daemon=True)
    starter.start()
    total_s = len(audio) / SAMPLE_RATE + 1.0
    try:
        pipeline.run(max_seconds=total_s if args.mode == "live" else None)
    finally:
        runtime.close()
    starter.join(timeout=1)
    base = base_holder[0] if base_holder else mono0

    print(f"\n{'probe':<6}{'sound starts':<15}{'show called':<15}{'show returned':<15}"
          f"{'clear called':<15}{'software alarm-to-display':>27}")  # fmt: skip
    latencies: list[float] = []
    shows = [c for c in timed.calls if c.kind == "show"]
    clears = [c for c in timed.calls if c.kind == "clear"]
    for k, onset in enumerate(onsets):
        t_sound = base + onset
        t_next = base + (onsets[k + 1] if k + 1 < len(onsets) else onset + args.interval)
        show = next((c for c in shows if t_sound - 0.2 <= c.t_start < t_next), None)
        if show is None:
            print(
                f"{k + 1:<6}{wall(t_sound, mono0, wall0):<15}no alert shown. "
                "Check the microphone level or the speaker volume."
            )
            continue
        clear = next((c for c in clears if c.t_start >= show.t_end), None)
        lat = (show.t_end - t_sound) * 1000.0
        latencies.append(lat)
        print(
            f"{k + 1:<6}{wall(t_sound, mono0, wall0):<15}{wall(show.t_start, mono0, wall0):<15}"
            f"{wall(show.t_end, mono0, wall0):<15}"
            f"{wall(clear.t_start, mono0, wall0) if clear else '-':<15}{lat:>24.0f} ms"
        )
    print()
    if latencies:
        print(
            f"Software alarm-to-display over {len(latencies)} probes: "
            f"median {percentile(latencies, 50):.0f} ms, p95 {percentile(latencies, 95):.0f} ms."
        )
        print(
            "This stops when the display call returns. Measure the rest with slow-motion video "
            "(see --help)."
        )
    show_ms = [(c.t_end - c.t_start) * 1000 for c in shows]
    if show_ms:
        print(
            f"Display show() call time: p50 {percentile(show_ms, 50):.2f} ms, "
            f"max {max(show_ms):.2f} ms"
        )
    return 0 if len(latencies) == len(onsets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
