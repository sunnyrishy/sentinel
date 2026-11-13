"""Replay a scenario (YAML) or a wav file through the gate and print detections.

Usage: python tools/run_replay.py scenarios/tonal_10db.yaml
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from sentinel.adapters import make_display
from sentinel.adapters.audio_file import AudioFileSource, load_wav_16k
from sentinel.classify.yamnet import YamnetClassifier, build_classifier_stage
from sentinel.config import ConfigError, SentinelConfig, load_config
from sentinel.gate.tonal import GateParams, GateState, process
from sentinel.pipeline import Pipeline
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="scenario .yaml or audio file")
    parser.add_argument("--config", default=None)
    parser.add_argument("--scores", action="store_true", help="print classifier scores per hop")
    parser.add_argument("--no-classifier", action="store_true", help="gate only")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.input.endswith((".yaml", ".yml")):
            scenario = load_scenario(args.input)
            rendered = render(scenario)
            audio, label = rendered.audio, f"{rendered.origin.upper()} scenario '{rendered.name}'"
            for ev in rendered.truth:
                print(f"truth  {ev.start_s:7.3f}s to {ev.end_s:7.3f}s  {ev.kind} -> {ev.label}")
        else:
            audio, label = load_wav_16k(args.input), f"recording '{args.input}'"
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 2

    if args.scores:
        try:
            print_scores(audio, cfg)
        except ConfigError as err:
            print(err, file=sys.stderr)
            return 2
        return 0
    print(f"Replaying {label}, {len(audio) / SAMPLE_RATE:.1f} s")
    params = GateParams.from_config(cfg.gate)
    for d in replay_gate(audio, params):
        kind = "provisional" if d.provisional else "CONFIRMED  "
        print(
            f"{kind} {d.alert_class.value} stage={d.stage} score={d.score:.2f} "
            f"onset={d.t_onset:7.3f}s detect={d.t_detect:7.3f}s "
            f"alarm-to-detect={(d.t_detect - d.t_onset) * 1000:.0f} ms"
        )
    try:
        None if args.no_classifier else build_classifier_stage(cfg.classifier)
    except (ConfigError, RuntimeError) as err:
        print(err, file=sys.stderr)
        return 2
    print("Pipeline run (virtual clock, console display):")
    pipeline = Pipeline(
        cfg,
        AudioFileSource(audio, cfg.audio.frame_ms, t0=0.0),
        make_display(cfg),
        on_latency=lambda e, p, a: print(
            f"  {e}: processing {p:.1f} ms, alarm-to-display {a:.0f} ms"
        ),
    )
    pipeline.run_sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
