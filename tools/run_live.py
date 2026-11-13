"""Run Sentinel on the live microphone.

Usage: python tools/run_live.py [--device NAME_OR_INDEX] [--display console] [--record]
Stop with Ctrl+C. No raw audio is stored unless --record is given.
"""

from __future__ import annotations

import argparse
import signal
import sys

from sentinel.adapters import make_display
from sentinel.adapters.audio_mic import AudioMicSource, list_devices
from sentinel.config import ConfigError, load_config
from sentinel.metrics import CpuMeter, Metrics
from sentinel.pipeline import Pipeline


def parse_device(value: str | None) -> str | int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--device", default=None, help="sounddevice name or index")
    parser.add_argument("--display", default=None, help="override display.adapter")
    parser.add_argument("--record", action="store_true", help="save raw audio to data/recordings/")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args(argv)
    if args.list_devices:
        print(list_devices())
        return 0
    try:
        cfg = load_config(args.config)
        display = make_display(cfg, args.display)
        metrics = Metrics()
        source = AudioMicSource(
            parse_device(args.device) if args.device else cfg.audio.device,
            cfg.audio.frame_ms,
            metrics=metrics,
        )
    except (ConfigError, RuntimeError) as err:
        print(err, file=sys.stderr)
        return 2

    def report(event_id: str, processing_ms: float, alarm_to_display_ms: float) -> None:
        print(
            f"  {event_id}: processing {processing_ms:.1f} ms, "
            f"alarm-to-display {alarm_to_display_ms:.0f} ms (software only)"
        )

    pipeline = Pipeline(cfg, source, display, metrics, on_latency=report, record=args.record)
    signal.signal(signal.SIGINT, lambda *_: pipeline.stop())
    cpu = CpuMeter()
    print("Listening on the live microphone. Ctrl+C to stop. Assistive alert, not a safety device.")
    if args.record:
        print("Recording raw audio to data/recordings/ (--record)")
    pipeline.run(max_seconds=args.duration)
    print(f"Summary: {metrics.summary()}  cpu={cpu.usage():.0%} of one core")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
