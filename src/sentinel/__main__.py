"""Command line entry point: python -m sentinel."""

from __future__ import annotations

import argparse
import sys

from sentinel import __version__
from sentinel.config import ConfigError, load_config

TOOLS_HELP = """\
tools (run from the project root):
  python tools/fetch_models.py     download YAMNet (one time, needs network)
  python tools/run_replay.py       replay a scenario or wav through the pipeline
  python tools/run_live.py         run on the microphone
  python tools/evaluate.py         recall, false alerts and latency report
  python tools/make_test_set.py    mix your own clips with noise into scenarios
  python tools/latency_probe.py    print timestamps for slow-motion video timing
  python tools/hw_smoke_test.py    show, clear and time a burst on an adapter
  python tools/bridge_echo.py      print alerts published by the core
  python tools/demo.py             start everything in demo mode
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sentinel",
        description="Sentinel: sound-to-sight alarm alerts. "
        "An assistive alert, not a certified safety device.",
        epilog=TOOLS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"sentinel {__version__}")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check-config", help="validate a config file and print its hash")
    check.add_argument("--config", default=None, help="path to a YAML config")
    models = sub.add_parser("check-models", help="check the model files and every class name")
    models.add_argument("--config", default=None, help="path to a YAML config")
    return parser


def _check_models(config_path: str | None) -> int:
    from sentinel.classify.yamnet import YamnetClassifier

    try:
        cfg = load_config(config_path)
        clf = YamnetClassifier(cfg.classifier)
    except (ConfigError, RuntimeError) as err:
        print(err, file=sys.stderr)
        return 2
    print(f"model ok: window {clf.window_len} samples ({clf.window_s:.3f} s), 521 classes")
    for cls, idx in clf.class_indices.items():
        print(f"  {cls.value}: " + ", ".join(f"{clf.class_names[i]} [{i}]" for i in idx))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check-config":
        try:
            cfg = load_config(args.config)
        except ConfigError as err:
            print(err, file=sys.stderr)
            return 2
        print(f"config ok, hash {cfg.config_hash()}")
        return 0
    if args.command == "check-models":
        return _check_models(args.config)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
