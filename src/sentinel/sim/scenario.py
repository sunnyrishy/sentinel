"""Scenario files (scenarios/*.yaml): load, validate and render to audio plus ground truth."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sentinel.adapters.audio_file import load_wav_16k
from sentinel.config import ConfigError, resolve_path
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, AlertClass

GENERATED_KINDS = {
    "tonal_alarm",
    "broadband_alarm",
    "horn",
    "fire_alarm_t3",
    "tone",
    "beep",
    "click",
}
ALL_KINDS = GENERATED_KINDS | {"clip"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoiseSpec(_Strict):
    type: Literal["white", "pink", "brown", "babble"] = "white"
    level_dbfs: float = Field(-30.0, le=0)


class EventSpec(_Strict):
    kind: str
    start_s: float = Field(ge=0)
    snr_db: float = 10.0
    label: str | None = None  # an AlertClass value, or null for a decoy that must not alert
    duration_s: float | None = Field(None, gt=0)
    freq_hz: float | None = Field(None, gt=0)
    pulses_per_min: float | None = Field(None, gt=0)
    duty: float | None = Field(None, gt=0, lt=1)
    harmonics: int | None = Field(None, ge=0)
    band_hz: tuple[float, float] | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _check(self) -> EventSpec:
        if self.kind not in ALL_KINDS:
            raise ValueError(f"unknown event kind '{self.kind}'. Valid kinds: {sorted(ALL_KINDS)}")
        if self.label is not None and self.label not in [c.value for c in AlertClass]:
            raise ValueError(f"unknown label '{self.label}'. Use an alert class or null")
        if self.kind == "clip" and not self.path:
            raise ValueError("event kind 'clip' needs a path")
        if self.kind != "clip" and self.path:
            raise ValueError("only event kind 'clip' takes a path")
        return self


class Scenario(_Strict):
    name: str
    duration_s: float = Field(gt=0)
    seed: int = 0
    origin: Literal["synthetic", "real"] | None = None
    noise: NoiseSpec
    events: list[EventSpec] = []

    @model_validator(mode="after")
    def _check(self) -> Scenario:
        has_clip = any(e.kind == "clip" for e in self.events)
        if has_clip and self.origin is None:
            raise ValueError(
                "scenario has clip events, so it must say origin: real or origin: synthetic"
            )
        return self

    @property
    def audio_origin(self) -> str:
        return self.origin or "synthetic"


@dataclass(frozen=True)
class TruthEvent:
    label: str | None
    kind: str
    start_s: float
    end_s: float
    snr_db: float


@dataclass(frozen=True)
class RenderedScenario:
    name: str
    audio: np.ndarray
    truth: list[TruthEvent]
    origin: str  # 'synthetic' or 'real'
    noise_type: str
    noise_dbfs: float

    @property
    def duration_s(self) -> float:
        return len(self.audio) / SAMPLE_RATE


def load_scenario(path: str | Path) -> Scenario:
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text())
    except FileNotFoundError as err:
        raise ConfigError(f"Scenario file not found: {p}") from err
    try:
        return Scenario.model_validate(raw)
    except ValidationError as err:
        msgs = "\n".join(f"  {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())
        raise ConfigError(f"Invalid scenario {p}:\n{msgs}") from err


def _event_signal(ev: EventSpec, seed: int) -> np.ndarray:
    d = ev.duration_s
    k = ev.kind
    if k == "tonal_alarm":
        return synth.tonal_alarm(
            ev.freq_hz or 1900.0,
            ev.pulses_per_min or 70.0,
            d or 6.0,
            ev.duty or 0.5,
            ev.harmonics or 0,
        )
    if k == "broadband_alarm":
        return synth.broadband_alarm(
            ev.pulses_per_min or 70.0, d or 6.0, ev.band_hz or (300.0, 4000.0), seed=seed
        )
    if k == "horn":
        return synth.horn(d or 2.0)
    if k == "fire_alarm_t3":
        return synth.fire_alarm_t3(d or 8.0, ev.freq_hz or 3100.0)
    if k == "tone":
        return synth.tone(ev.freq_hz or 1900.0, d or 5.0)
    if k == "beep":
        n = int((d or 0.1) * SAMPLE_RATE)
        tone = synth.tone(ev.freq_hz or 1900.0, d or 0.1)
        return tone * synth.fade_gate(n, int(synth.FADE_S * SAMPLE_RATE))
    if k == "click":
        out = np.zeros(int(0.01 * SAMPLE_RATE), dtype=np.float32)
        out[: int(0.002 * SAMPLE_RATE)] = np.hanning(int(0.002 * SAMPLE_RATE)).astype(np.float32)
        return out
    return load_wav_16k(resolve_path(ev.path or ""))


def render(scenario: Scenario) -> RenderedScenario:
    """Render noise plus events at the requested SNRs. Deterministic for a given seed."""
    n = round(scenario.duration_s * SAMPLE_RATE)
    track = synth.noise(
        scenario.noise.type, scenario.duration_s, scenario.seed, scenario.noise.level_dbfs
    )
    noise_rms = synth.rms(track)
    audio = track.astype(np.float64)
    truth: list[TruthEvent] = []
    for i, ev in enumerate(scenario.events):
        sig = _event_signal(ev, scenario.seed + 1 + i)
        start = round(ev.start_s * SAMPLE_RATE)
        sig = sig[: max(0, n - start)]
        if len(sig) == 0:
            continue
        mask = synth.active_mask(sig)
        scaled = synth.scale_to_snr(sig, noise_rms, ev.snr_db)
        audio[start : start + len(sig)] += scaled
        idx = np.flatnonzero(mask)
        t0 = (start + (idx[0] if len(idx) else 0)) / SAMPLE_RATE
        t1 = (start + (idx[-1] + 1 if len(idx) else len(sig))) / SAMPLE_RATE
        truth.append(TruthEvent(ev.label, ev.kind, t0, t1, ev.snr_db))
    audio = np.clip(audio, -1.0, 1.0).astype(np.float32)
    return RenderedScenario(
        scenario.name,
        audio,
        truth,
        scenario.audio_origin,
        scenario.noise.type,
        scenario.noise.level_dbfs,
    )
