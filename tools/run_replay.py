"""Replay a scenario (YAML) or a wav file through the pipeline and print what happens.

Usage:
  python tools/run_replay.py scenarios/tonal_10db.yaml
  python tools/run_replay.py scenarios/tonal_10db.yaml --serve    # alerts in the browser simulator
  python tools/run_replay.py some.wav --scores                    # class scores per hop
Replays are labelled synthetic or replay in the event log and never presented as live.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from sentinel.adapters.audio_file import AudioFileSource, load_wav_16k
from sentinel.classify.yamnet import YamnetClassifier, build_classifier_stage
from sentinel.config import ConfigError, SentinelConfig, load_config
from sentinel.eventlog import format_events
from sentinel.gate.tonal import GateParams, GateState, process
from sentinel.pipeline import Pipeline
from sentinel.runtime import start_runtime
from sentinel.sim.scenario import load_scenario, render
from sentinel.types import SAMPLE_RATE, Detection


def replay_gate(audio: np.ndarray, params: GateParams, frame_ms: int = 32) -> list[Detection]:
    """Feed audio through the gate in frame-sized blocks using virtual stream time from 0."""
    state = GateState()
    step = int(SAMPLE_RATE * frame_ms / 1000)
    out: list[Detection] = []
    for pos in range(0, len(audio), step):
        chunk = audio[pos : pos + step]
        out.extend(process(state, chunk, (pos + len(chunk)) / SAMPLE_RATE, params))
    return out


def print_scores(audio: np.ndarray, cfg: SentinelConfig, top: int = 3) -> None:
    """Print class scores per classifier hop: the top classes and the three Sentinel classes."""
    clf = YamnetClassifier(cfg.classifier)
    hop = int(cfg.classifier.hop_s * SAMPLE_RATE)
    print(f"Classifier window {clf.window_len} samples ({clf.window_s:.3f} s), hop {hop} samples")
    for end in range(clf.window_len, len(audio) + 1, hop):
        scores = clf.infer(audio[end - clf.window_len : end])
        best = np.argsort(scores)[::-1][:top]
        top_txt = ", ".join(f"{clf.class_names[i]} {scores[i]:.2f}" for i in best)
        ours = " ".join(f"{c.value} {v:.2f}" for c, v in clf.class_scores(scores).items())
        print(f"t={end / SAMPLE_RATE:6.2f}s  top: {top_txt}  | {ours}")


def load_input(path: str) -> tuple[np.ndarray, str, str]:
    """Returns audio, a printable label and the event log source tag."""
    if path.endswith((".yaml", ".yml")):
        rendered = render(load_scenario(path))
        for ev in rendered.truth:
            print(f"truth  {ev.start_s:7.3f}s to {ev.end_s:7.3f}s  {ev.kind} -> {ev.label}")
        label = f"{rendered.origin.upper()} scenario '{rendered.name}'"
        return rendered.audio, label, "synthetic" if rendered.origin == "synthetic" else "replay"
    return load_wav_16k(path), f"recording '{path}'", "replay"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="scenario .yaml or audio file")
    parser.add_argument("--config", default=None)
    parser.add_argument("--scores", action="store_true", help="print classifier scores per hop")
    parser.add_argument("--no-classifier", action="store_true", help="gate only")
    parser.add_argument("--db", default=None, help="event log path (default: config eventlog.path)")
    parser.add_argument(
        "--display", default=None, help="override display.adapter, e.g. console,sim"
    )
    parser.add_argument(
        "--sim", action="store_true", help="also show alerts in the browser simulator"
    )
    parser.add_argument(
        "--realtime", action="store_true", help="pace the replay at wall-clock speed"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="serve the dashboard and simulator, replay in real time, keep serving",
    )
    parser.add_argument("--no-dashboard", action="store_true", help="never start the dashboard")
    args = parser.parse_args(argv)

    runtime = None
    try:
        cfg = load_config(args.config)
        audio, label, source_label = load_input(args.input)
        if args.scores:
            print_scores(audio, cfg)
            return 0
        print(f"Replaying {label}, {len(audio) / SAMPLE_RATE:.1f} s")
        for d in replay_gate(audio, GateParams.from_config(cfg.gate)):
            kind = "provisional" if d.provisional else "CONFIRMED  "
            print(
                f"{kind} {d.alert_class.value} stage={d.stage} score={d.score:.2f} "
                f"onset={d.t_onset:7.3f}s detect={d.t_detect:7.3f}s "
                f"alarm-to-detect={(d.t_detect - d.t_onset) * 1000:.0f} ms"
            )
        display_name = args.display
        if args.sim:
            display_name = (display_name or cfg.display.adapter) + ",sim"
        runtime = start_runtime(
            cfg, source_label, display_name, args.db,
            serve=args.serve, use_dashboard=not args.no_dashboard,
        )  # fmt: skip
        stage = None if args.no_classifier else build_classifier_stage(cfg.classifier)
    except (ConfigError, RuntimeError, NotImplementedError) as err:
        print(err, file=sys.stderr)
        if runtime:
            runtime.close()
        return 2

    realtime = args.realtime or args.serve
    mode = "wall clock" if realtime else "virtual clock"
    print(f"Pipeline run ({mode}, source tagged '{source_label}'):")
    if runtime.url:
        print(f"Dashboard and simulator: {runtime.url}")
    before = runtime.eventlog.count()
    pipeline = Pipeline(
        cfg,
        AudioFileSource(audio, cfg.audio.frame_ms, realtime=realtime, t0=None if realtime else 0.0),
        runtime.display,
        on_latency=lambda e, p, a: print(
            f"  {e}: processing {p:.1f} ms, alarm-to-display {a:.0f} ms"
        ),
        classifier=stage,
        eventlog=runtime.eventlog,
        source_label=source_label,
    )
    runtime.attach(pipeline)
    pipeline.run() if realtime else pipeline.run_sync()
    new = runtime.eventlog.count() - before
    print(f"Event log: {new} new event(s)")
    print(format_events(runtime.eventlog.list(limit=max(new, 0))))
    if args.serve:
        runtime.hold()
    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
