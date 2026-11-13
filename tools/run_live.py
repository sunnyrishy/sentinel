"""Run Sentinel on the live microphone.

Usage: python tools/run_live.py [--sim] [--device NAME_OR_INDEX] [--record]
Stop with Ctrl+C. No raw audio is stored unless --record is given.
With --sim (or --display console,sim) the dashboard and glasses simulator open at the
printed address.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from sentinel.adapters import make_audio_source
from sentinel.adapters.audio_mic import list_devices
from sentinel.classify.yamnet import build_classifier_stage
from sentinel.config import ConfigError, load_config
from sentinel.metrics import CpuMeter, Metrics
from sentinel.pipeline import Pipeline
from sentinel.runtime import start_runtime


def parse_device(value: str | None) -> str | int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--input",
        choices=["mic", "glasses"],
        default="mic",
        help="audio input: mic is the external microphone (L1), glasses is L0",
    )
    parser.add_argument("--device", default=None, help="sounddevice name or index")
    parser.add_argument(
        "--display", default=None, help="override display.adapter, e.g. console,sim"
    )
    parser.add_argument(
        "--sim", action="store_true", help="also show alerts in the browser simulator"
    )
    parser.add_argument("--no-dashboard", action="store_true", help="never start the dashboard")
    parser.add_argument("--db", default=None, help="event log path (default: config eventlog.path)")
    parser.add_argument("--record", action="store_true", help="save raw audio to data/recordings/")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--no-classifier", action="store_true", help="gate only, no model needed")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args(argv)
    if args.list_devices:
        print(list_devices())
        return 0

    runtime = None
    try:
        cfg = load_config(args.config)
        display_name = args.display
        if args.sim:
            display_name = (display_name or cfg.display.adapter) + ",sim"
        runtime = start_runtime(
            cfg, "live", display_name, args.db, use_dashboard=not args.no_dashboard
        )
        stage = None if args.no_classifier else build_classifier_stage(cfg.classifier)
        metrics = Metrics()
        source = make_audio_source(
            cfg, args.input, parse_device(args.device) if args.device else None, metrics
        )
    except (ConfigError, RuntimeError, NotImplementedError) as err:
        print(err, file=sys.stderr)
        if runtime:
            runtime.close()
        return 2

    def report(event_id: str, processing_ms: float, alarm_to_display_ms: float) -> None:
        print(
            f"  {event_id}: processing {processing_ms:.1f} ms, "
            f"alarm-to-display {alarm_to_display_ms:.0f} ms (software only)"
        )

    pipeline = Pipeline(
        cfg,
        source,
        runtime.display,
        metrics,
        on_latency=report,
        record=args.record,
        classifier=stage,
        eventlog=runtime.eventlog,
        source_label="live",
    )
    runtime.attach(pipeline)
    if threading.current_thread() is threading.main_thread():  # signals need the main thread
        signal.signal(signal.SIGINT, lambda *_: pipeline.stop())
    cpu = CpuMeter()
    print(f"Listening on the {args.input} input. Ctrl+C to stop.")
    print("Sentinel is an assistive alert, not a certified safety device.")
    if runtime.url:
        print(f"Dashboard and simulator: {runtime.url}")
    if args.record:
        print("Recording raw audio to data/recordings/ (--record)")
    try:
        pipeline.run(max_seconds=args.duration)
    finally:
        runtime.close()
    print(f"Summary: {metrics.summary()}  cpu={cpu.usage():.0%} of one core")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
