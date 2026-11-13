"""Replay a scenario (YAML) or a wav file through the gate and print detections.

Usage: python tools/run_replay.py scenarios/tonal_10db.yaml
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from sentinel.adapters.audio_file import load_wav_16k
from sentinel.config import ConfigError, load_config
from sentinel.gate.tonal import GateParams, GateState, process
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="scenario .yaml or audio file")
    parser.add_argument("--config", default=None)
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

    print(f"Replaying {label}, {len(audio) / SAMPLE_RATE:.1f} s")
    params = GateParams.from_config(cfg.gate)
    for d in replay_gate(audio, params):
        kind = "provisional" if d.provisional else "CONFIRMED  "
        print(
            f"{kind} {d.alert_class.value} stage={d.stage} score={d.score:.2f} "
            f"onset={d.t_onset:7.3f}s detect={d.t_detect:7.3f}s "
            f"alarm-to-detect={(d.t_detect - d.t_onset) * 1000:.0f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
