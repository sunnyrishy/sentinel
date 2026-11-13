"""Evaluate Sentinel on scenarios and write a Markdown report to reports/.

Examples:
  python tools/evaluate.py --synthetic-suite                 # broad SYNTHETIC suite (dev only)
  python tools/evaluate.py --synthetic-suite --quick         # small suite, about a minute
  python tools/evaluate.py scenarios/generated/*.yaml        # real test set from make_test_set.py
  python tools/evaluate.py scenarios/generated/*.yaml --split heldout --sweep-thresholds

One report is written per audio origin (synthetic or real), so the two are never mixed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime

import numpy as np

from sentinel.classify.yamnet import YamnetClassifier
from sentinel.config import PROJECT_ROOT, ConfigError, load_config
from sentinel.evaluation import (
    ScenarioResult,
    SweepItem,
    default_template,
    render_report,
    run_scenario,
    sweep_thresholds,
    synthetic_suite,
    window_scores,
    write_report,
)
from sentinel.sim.scenario import Scenario, load_scenario, render


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5, check=False,
        )  # fmt: skip
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def parse_thresholds(text: str) -> list[float]:
    lo, hi, step = (float(x) for x in text.split(":"))
    return [round(float(v), 4) for v in np.arange(lo, hi + step / 2, step)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "scenarios", nargs="*", help="scenario YAML files (default: scenarios/*.yaml)"
    )
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--synthetic-suite", action="store_true", help="evaluate the SYNTHETIC suite"
    )
    parser.add_argument(
        "--quick", action="store_true", help="with --synthetic-suite: a small suite"
    )
    parser.add_argument("--split", choices=["dev", "heldout"], default=None)
    parser.add_argument("--no-classifier", action="store_true", help="evaluate the tonal gate only")
    parser.add_argument(
        "--sweep-thresholds",
        action="store_true",
        help="print precision and recall per classifier threshold",
    )
    parser.add_argument("--thresholds", default="0.05:0.95:0.05", help="lo:hi:step")
    parser.add_argument("--out", default="reports", help="directory for the Markdown report")
    parser.add_argument("--name", default=None, help="report file name stem")
    parser.add_argument("--stdout", action="store_true", help="also print the report")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
        classifier = None if args.no_classifier else YamnetClassifier(cfg.classifier)
        if args.synthetic_suite:
            scenarios: list[Scenario] = synthetic_suite(quick=args.quick)
        else:
            paths = args.scenarios or sorted(
                str(p) for p in (PROJECT_ROOT / "scenarios").glob("*.yaml")
            )
            if not paths:
                print(
                    "No scenario files found. Pass paths or use --synthetic-suite.", file=sys.stderr
                )
                return 2
            scenarios = [load_scenario(p) for p in paths]
    except (ConfigError, RuntimeError) as err:
        print(err, file=sys.stderr)
        if "Model not found" in str(err) or "Class map not found" in str(err):
            print("Use --no-classifier to evaluate the tonal gate only.", file=sys.stderr)
        return 2
    if args.split:
        scenarios = [s for s in scenarios if s.split == args.split]
        if not scenarios:
            print(f"No scenarios with split '{args.split}'.", file=sys.stderr)
            return 2

    thresholds = parse_thresholds(args.thresholds)
    groups: dict[str, list[ScenarioResult]] = {}
    sweeps: dict[str, list[SweepItem]] = {}
    for i, sc in enumerate(scenarios, 1):
        try:
            rendered = render(sc)
        except (ConfigError, OSError) as err:
            print(f"Skipping {sc.name}: {err}", file=sys.stderr)
            continue
        print(
            f"[{i}/{len(scenarios)}] {sc.name} ({rendered.origin}, {rendered.duration_s:.0f} s)",
            flush=True,
        )
        result = run_scenario(rendered, cfg, classifier)
        groups.setdefault(rendered.origin, []).append(result)
        if args.sweep_thresholds and classifier is not None:
            times, per = window_scores(rendered.audio, classifier, cfg.classifier.hop_s)
            sweeps.setdefault(rendered.origin, []).append(
                SweepItem(result.truth, rendered.duration_s, times, per)
            )

    if not groups:
        print("Nothing was evaluated.", file=sys.stderr)
        return 2
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    template = default_template()
    for origin, results in groups.items():
        sweep = None
        if args.sweep_thresholds:
            sm = cfg.classifier.smoothing
            sweep = (
                sweep_thresholds(sweeps.get(origin, []), sm.hits, sm.windows, thresholds)
                if classifier is not None else None
            )  # fmt: skip
        text = render_report(
            template, origin, results, cfg, sweep, when, git_commit(), classifier is not None
        )
        stem = args.name or f"eval_{stamp}"
        path = write_report(text, PROJECT_ROOT / args.out, f"{stem}_{origin}")
        print(f"\nWrote {path} ({origin.upper()} audio, {len(results)} scenarios)")
        if args.stdout:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
