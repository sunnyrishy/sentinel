"""Mix user-supplied clips with noise into scenario files, plus a manifest of each clip's licence.

Folder layout (see docs/RECORDING_GUIDE.md):
  data/clips/reversing_alarm/*.wav   data/clips/vehicle_horn/*.wav   data/clips/fire_alarm/*.wav
  data/clips/negative/*.wav          data/clips/manifest.csv

Steps:
  python tools/make_test_set.py --init-manifest      # writes blank rows, you fill in licences
  python tools/make_test_set.py --heldout-session day2_garage
Then: python tools/evaluate.py scenarios/generated/*.yaml --split heldout
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sentinel import testset
from sentinel.config import ConfigError, resolve_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clips", default="data/clips", help="folder with the clip subfolders")
    parser.add_argument(
        "--manifest", default=None, help="manifest CSV (default: <clips>/manifest.csv)"
    )
    parser.add_argument(
        "--init-manifest",
        action="store_true",
        help="add a blank row per clip to the manifest and exit",
    )
    parser.add_argument("--out", default="scenarios/generated", help="where to write scenario YAML")
    parser.add_argument(
        "--origin",
        choices=["real", "synthetic"],
        default="real",
        help="what the clips are (default real: your own recordings)",
    )
    parser.add_argument(
        "--heldout-session",
        default=None,
        help="session name (from the manifest) that forms the held-out split",
    )
    parser.add_argument(
        "--levels",
        default="easy=10,loud=5,very_loud=0",
        help="noise levels as name=signal-to-noise dB",
    )
    parser.add_argument(
        "--noise-type", choices=["white", "pink", "brown", "babble"], default="pink"
    )
    parser.add_argument("--noise-dbfs", type=float, default=-30.0)
    parser.add_argument(
        "--gap-s",
        type=float,
        default=10.0,
        help="silence after each positive clip (must exceed ttl + cooldown, 8 s)",
    )
    parser.add_argument("--max-scenario-s", type=float, default=600.0)
    parser.add_argument("--noise-only-s", type=float, default=300.0)
    parser.add_argument("--allow-nc", action="store_true", help="accept CC-BY-NC clips (dev only)")
    parser.add_argument("--prefix", default="testset")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--attributions", default="data/ATTRIBUTIONS.md")
    parser.add_argument("--no-attributions", action="store_true")
    args = parser.parse_args(argv)

    clips_dir = resolve_path(args.clips)
    manifest_path = resolve_path(args.manifest) if args.manifest else clips_dir / "manifest.csv"
    try:
        if args.init_manifest:
            n = testset.write_skeleton(clips_dir, manifest_path)
            print(
                f"Added {n} blank row(s) to {manifest_path}. Fill in licence, attribution, session."
            )
            return 0
        if args.gap_s < 8.0:
            print(
                "warning: --gap-s under 8 s lets cooldown merge neighbouring events",
                file=sys.stderr,
            )
        clips, warnings = testset.collect_clips(
            clips_dir, testset.read_manifest(manifest_path), args.heldout_session, args.allow_nc
        )
        scenarios = testset.build_scenarios(
            clips, testset.parse_levels(args.levels), args.noise_type, args.noise_dbfs, args.gap_s,
            args.max_scenario_s, args.noise_only_s, args.origin, args.prefix, args.seed,
        )  # fmt: skip
        out_dir = resolve_path(args.out)
        paths = testset.write_scenarios(scenarios, out_dir, args.origin)
        params = {k: v for k, v in vars(args).items() if k != "init_manifest"}
        manifest = testset.write_manifest(out_dir, clips, scenarios, warnings, params)
        if not args.no_attributions:
            testset.update_attributions(resolve_path(args.attributions), clips)
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 2

    n_pos = sum(1 for c in clips if c.label)
    print(f"{len(clips)} clips ({n_pos} alarms, {len(clips) - n_pos} negatives)")
    for split in ("dev", "heldout"):
        sc = [c for c in clips if c.split == split]
        by_class = {k: sum(1 for c in sc if c.label == k) for k in testset.POSITIVE_DIRS}
        print(f"  {split}: {len(sc)} clips, alarms per class {by_class}")
    print(f"Wrote {len(paths)} scenario files to {Path(out_dir)} and {manifest}")
    for w in warnings:
        print(f"warning: {w}")
    print("Next: python tools/evaluate.py " + str(Path(args.out) / "*.yaml") + " --split heldout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
