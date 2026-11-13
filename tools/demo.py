"""Start everything in demo mode with one command.

  python tools/demo.py                 # live microphone, dashboard, glasses simulator, kiosk page
  python tools/demo.py --mode replay   # labeled recording replay (the fallback)
  python tools/demo.py --mode replay --input my_demo_clip.wav

What starts: the pipeline, the dashboard at http://127.0.0.1:8765/, the glasses simulator (shown
full screen at /?kiosk=1) and the console display. Everything runs offline.

A recording is never presented as live. In replay mode, and when the microphone cannot be opened and
the demo falls back to a replay, the page shows a RECORDING badge and every event is logged with
source replay or synthetic. Sentinel is an assistive alert, not a certified safety device.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator

from sentinel.adapters import make_audio_source
from sentinel.adapters.audio_file import AudioFileSource, load_wav_16k
from sentinel.classify.yamnet import build_classifier_stage
from sentinel.config import ConfigError, SentinelConfig, load_config, resolve_path
from sentinel.eventlog import format_events
from sentinel.metrics import Metrics
from sentinel.pipeline import Pipeline
from sentinel.runtime import Runtime, start_runtime
from sentinel.sim.scenario import load_scenario, render
from sentinel.types import SAMPLE_RATE, AudioFrame

DEMO_SCENARIO = "scenarios/demo.yaml"
DEMO_DB = "data/demo_events.sqlite"


class LoopingSource:
    """Replays the same audio over and over at wall-clock speed until closed."""

    sample_rate = SAMPLE_RATE

    def __init__(self, audio, frame_ms: int, gap_s: float = 2.0) -> None:
        self._audio, self._frame_ms, self._gap_s = audio, frame_ms, gap_s
        self._closed = threading.Event()
        self._current: AudioFileSource | None = None

    def frames(self) -> Iterator[AudioFrame]:
        while not self._closed.is_set():
            self._current = AudioFileSource(self._audio, self._frame_ms, realtime=True)
            yield from self._current.frames()
            self._closed.wait(self._gap_s)

    def close(self) -> None:
        self._closed.set()
        if self._current:
            self._current.close()


def load_replay_audio(path: str) -> tuple[object, str, str]:
    """Returns audio, a label for the console and the event log source tag."""
    if path.endswith((".yaml", ".yml")):
        rendered = render(load_scenario(resolve_path(path)))
        origin = "synthetic" if rendered.origin == "synthetic" else "replay"
        return rendered.audio, f"{rendered.origin} scenario '{rendered.name}'", origin
    return load_wav_16k(resolve_path(path)), f"recording '{path}'", "replay"


def banner(lines: list[str]) -> None:
    width = max(len(line) for line in lines) + 4
    print("+" + "-" * width + "+")
    for line in lines:
        print("|  " + line.ljust(width - 2) + "|")
    print("+" + "-" * width + "+")


def apply_strict(cfg: SentinelConfig) -> None:
    """Demo-mode toggle: require 3 of 3 classifier windows. Fewer false alerts, a bit slower.

    This changes the config hash, so events recorded in strict mode are tagged as such.
    """
    cfg.classifier.smoothing.hits = 3
    cfg.classifier.smoothing.windows = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--mode", choices=["live", "replay"], default="live")
    parser.add_argument(
        "--input",
        default=DEMO_SCENARIO,
        help="replay mode: scenario .yaml or audio file (default: scenarios/demo.yaml)",
    )
    parser.add_argument("--device", default=None, help="microphone name or index (live mode)")
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="live mode: fail instead of falling back to a labeled replay",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="require 3 of 3 classifier windows to reduce false alerts",
    )
    parser.add_argument("--no-classifier", action="store_true", help="gate only, no model needed")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument(
        "--no-kiosk", action="store_true", help="open the full dashboard, not kiosk"
    )
    parser.add_argument("--db", default=DEMO_DB, help="event log for this demo")
    parser.add_argument("--keep-log", action="store_true", help="keep events from earlier demos")
    parser.add_argument("--record", action="store_true", help="save raw audio to data/recordings/")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    args = parser.parse_args(argv)

    runtime: Runtime | None = None
    try:
        cfg = load_config(args.config)
        if args.strict:
            apply_strict(cfg)
        if not args.keep_log and args.db != ":memory:":
            resolve_path(args.db).unlink(missing_ok=True)
        stage = None
        if not args.no_classifier:
            try:
                stage = build_classifier_stage(cfg.classifier)
            except ConfigError as err:
                print(f"Classifier off, gate only: {err}", file=sys.stderr)

        source = None
        label = "live"
        mode = args.mode
        replay_note = ""
        if mode == "live":
            try:
                dev = int(args.device) if args.device and args.device.isdigit() else args.device
                source = make_audio_source(cfg, "mic", dev, None)
            except (RuntimeError, NotImplementedError) as err:
                if args.no_fallback:
                    raise
                print(f"\nMicrophone unavailable: {err}", file=sys.stderr)
                mode = "replay"
                replay_note = "NOT LIVE: the microphone could not be opened, so this is a replay."
        if mode == "replay":
            audio, desc, label = load_replay_audio(args.input)
            source = LoopingSource(audio, cfg.audio.frame_ms)
            replay_note = (
                replay_note or "RECORDING: replaying audio. This is not the live microphone."
            )
        else:
            desc = "live microphone"

        runtime = start_runtime(
            cfg, label, "console,sim", args.db, use_dashboard=True,
            extra_status={"mode": "demo", "strict": args.strict, "input": desc},
        )  # fmt: skip
    except (ConfigError, RuntimeError, NotImplementedError, OSError) as err:
        print(err, file=sys.stderr)
        if runtime:
            runtime.close()
        return 2

    assert source is not None and runtime is not None
    page = runtime.url + ("" if args.no_kiosk else "?kiosk=1")
    classifier_text = "off (gate only)"
    if stage:
        classifier_text = "on, strict 3 of 3" if args.strict else "on"
    lines = [
        "Sentinel demo mode",
        f"Input:      {desc}",
        f"Classifier: {classifier_text}",
        f"Page:       {page}",
        "Offline:    no network calls",
        "Assistive alert, not a certified safety device.",
    ]  # fmt: skip
    if replay_note:
        lines.insert(1, replay_note)
    banner(lines)
    if not args.no_browser:
        try:
            webbrowser.open(page)
        except Exception:
            print("Could not open a browser. Open the page above by hand.")

    def report(event_id: str, processing_ms: float, alarm_to_display_ms: float) -> None:
        print(f"  {event_id}: processing {processing_ms:.1f} ms, alarm-to-display "
              f"{alarm_to_display_ms:.0f} ms (software only)")  # fmt: skip

    metrics = Metrics()
    pipeline = Pipeline(
        cfg, source, runtime.display, metrics, record=args.record, classifier=stage,
        eventlog=runtime.eventlog, source_label=label,
        on_latency=report,
    )  # fmt: skip
    runtime.attach(pipeline)
    if threading.current_thread() is threading.main_thread():  # signals need the main thread
        signal.signal(signal.SIGINT, lambda *_: pipeline.stop())
    started = time.monotonic()
    try:
        pipeline.run(max_seconds=args.duration)
    finally:
        rows = runtime.eventlog.list(limit=20)
        runtime.close()
    print(f"\nDemo ended after {time.monotonic() - started:.0f} s. Events this demo:")
    print(format_events(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
