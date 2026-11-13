"""Stage 1: tonal gate for pulsed backup alarms.

Pure module: a function from a block of float32 samples and a small state object to a list of
detections. No I/O, no clock, no threads, so it is easy to test and to port to C++. The only
dependencies are numpy and the Detection type.

Algorithm (spec, Detection algorithms, Stage 1):
  1. STFT, Hann window n_fft, hop samples.
  2. A frame is tonal if the strongest in-band bin is peak_to_median_db above the band median
     and is a local maximum against its neighbours.
  3. A tone is stable while its peak frequency stays within freq_tolerance_hz of the run
     reference, with gaps of up to max_gap_frames.
  4. A burst is a run of stable tonal frames. Valid if burst_min_s <= length <= burst_max_s.
     A tone that outlasts burst_max_s is continuous and ignored.
  5. Provisional detection when a burst reaches burst_min_s. Onset is the first tonal frame start.
  6. A second valid burst whose onset follows the previous onset by pulse_period_s (and sits at
     the same frequency) emits a confirmed detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from sentinel.types import AlertClass, Detection

if TYPE_CHECKING:
    from sentinel.config import GateConfig

_EPS = 1e-12


@dataclass(frozen=True)
class GateParams:
    sample_rate: int = 16_000
    n_fft: int = 1024
    hop: int = 256
    band_hz: tuple[float, float] = (800.0, 3000.0)
    peak_to_median_db: float = 15.0
    freq_tolerance_hz: float = 50.0
    min_stable_frames: int = 5
    max_gap_frames: int = 2
    burst_min_s: float = 0.25
    burst_max_s: float = 1.0
    pulse_period_s: tuple[float, float] = (0.6, 1.5)
    lead_quiet_frames: int = 2  # a burst needs this many non-tonal frames before it

    @classmethod
    def from_config(cls, cfg: GateConfig, sample_rate: int = 16_000) -> GateParams:
        return cls(
            sample_rate=sample_rate,
            n_fft=cfg.n_fft,
            hop=cfg.hop,
            band_hz=cfg.band_hz,
            peak_to_median_db=cfg.peak_to_median_db,
            freq_tolerance_hz=cfg.freq_tolerance_hz,
            min_stable_frames=cfg.min_stable_frames,
            burst_min_s=cfg.burst_min_s,
            burst_max_s=cfg.burst_max_s,
            pulse_period_s=cfg.pulse_period_s,
        )


@dataclass
class _Run:
    first: int  # first tonal frame index
    last: int  # last tonal frame index
    ref_freq: float
    n_tonal: int = 1
    ppm_sum: float = 0.0
    gap: int = 0
    ignore: bool = False  # tone already on at stream start: no rising edge seen
    continuous: bool = False
    fired: bool = False


@dataclass
class _Burst:
    onset_sample: int
    freq: float


@dataclass
class GateState:
    """Mutable carry between blocks. Create one per stream."""

    buf: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    n_total: int = 0  # samples received so far
    next_frame: int = 0  # index of the next frame to analyse
    quiet: int = 0  # consecutive non-tonal frames just seen
    run: _Run | None = None
    prev_burst: _Burst | None = None


def analyze_frames(
    frames: np.ndarray, params: GateParams
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-frame tonality. frames has shape (n, n_fft). Returns (tonal, freq_hz, peak_to_median_db).

    This is the hot spectral path, kept free of state so it can be ported to C++.
    """
    n_bins = params.n_fft // 2 + 1
    window = np.hanning(params.n_fft + 1)[:-1].astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float64)
    bin_hz = params.sample_rate / params.n_fft
    # Search one bin beyond each band edge, then require the interpolated peak frequency to be in
    # the band. A tone sitting right on an edge has its true peak just outside the in-band bins.
    lo = max(1, int(np.floor(params.band_hz[0] / bin_hz)))
    hi = min(n_bins - 2, int(np.ceil(params.band_hz[1] / bin_hz)))
    band = spec[:, lo : hi + 1]
    median = np.median(band, axis=1)
    k = np.argmax(band, axis=1) + lo
    rows = np.arange(len(frames))
    peak = spec[rows, k]
    ppm_db = 20 * np.log10((peak + _EPS) / (median + _EPS))
    local_max = (peak >= spec[rows, k - 1]) & (peak >= spec[rows, k + 1])
    tonal = local_max & (ppm_db >= params.peak_to_median_db) & (peak > 1e-6)
    # Parabolic interpolation on log magnitude for a sub-bin frequency estimate.
    a = np.log(spec[rows, k - 1] + _EPS)
    b = np.log(peak + _EPS)
    c = np.log(spec[rows, k + 1] + _EPS)
    denom = a - 2 * b + c
    delta = np.where(np.abs(denom) > 1e-9, 0.5 * (a - c) / np.where(denom == 0, 1, denom), 0.0)
    freq = (k + np.clip(delta, -0.5, 0.5)) * bin_hz
    half = 0.5 * bin_hz
    in_band = (freq >= params.band_hz[0] - half) & (freq <= params.band_hz[1] + half)
    return tonal & in_band, freq, ppm_db


def _score(mean_ppm_db: float, params: GateParams) -> float:
    """Map mean peak-to-median dB to 0..1. The threshold maps to 0.5, twice the threshold to 1."""
    return float(np.clip(mean_ppm_db / (2 * params.peak_to_median_db), 0.0, 1.0))


def process(
    state: GateState, samples: np.ndarray, t_end: float, params: GateParams
) -> list[Detection]:
    """Feed a block of samples whose last sample is at time t_end. Returns new detections.

    Detection times are signal times derived from sample positions and t_end. t_detect is the end
    of the analysis frame that triggered the detection; a live caller may overwrite it with the
    wall clock to include compute time.
    """
    state.buf = np.concatenate([state.buf, np.asarray(samples, dtype=np.float32)])
    state.n_total += len(samples)
    if len(state.buf) < params.n_fft:
        return []
    n_frames = (len(state.buf) - params.n_fft) // params.hop + 1
    idx = np.arange(params.n_fft)[None, :] + params.hop * np.arange(n_frames)[:, None]
    tonal, freq, ppm = analyze_frames(state.buf[idx], params)

    def t_of(sample_index: int) -> float:
        return t_end - (state.n_total - sample_index) / params.sample_rate

    out: list[Detection] = []
    for i in range(n_frames):
        f_idx = state.next_frame + i
        _step(state, params, f_idx, bool(tonal[i]), float(freq[i]), float(ppm[i]), t_of, out)
    state.next_frame += n_frames
    state.buf = state.buf[n_frames * params.hop :]
    return out


def _step(
    state: GateState,
    p: GateParams,
    f_idx: int,
    tonal: bool,
    freq: float,
    ppm: float,
    t_of,
    out: list[Detection],
) -> None:
    run = state.run
    if tonal and run is not None and abs(freq - run.ref_freq) > p.freq_tolerance_hz:
        tonal = False  # a deviating frame counts as a gap, so sweeping tones never form a run
    if not tonal:
        state.quiet += 1
        if run is not None:
            run.gap += 1
            if run.gap > p.max_gap_frames:
                _end_run(state, p)
        return

    if run is None:
        run = _Run(f_idx, f_idx, freq, ppm_sum=ppm, ignore=state.quiet < p.lead_quiet_frames)
        state.run = run
    else:
        run.last = f_idx
        run.n_tonal += 1
        run.ppm_sum += ppm
        run.gap = 0
    state.quiet = 0
    if run.ignore or run.continuous:
        return
    length_s = (run.last - run.first + 1) * p.hop / p.sample_rate
    if length_s > p.burst_max_s:
        run.continuous = True
    elif not run.fired and length_s >= p.burst_min_s and run.n_tonal >= p.min_stable_frames:
        run.fired = True
        _fire(state, p, run, f_idx, t_of, out)


def _fire(
    state: GateState, p: GateParams, run: _Run, f_idx: int, t_of, out: list[Detection]
) -> None:
    onset_sample = run.first * p.hop
    prev = state.prev_burst
    confirmed = False
    if prev is not None:
        gap_s = (onset_sample - prev.onset_sample) / p.sample_rate
        same_freq = abs(run.ref_freq - prev.freq) <= p.freq_tolerance_hz
        confirmed = same_freq and p.pulse_period_s[0] <= gap_s <= p.pulse_period_s[1]
    out.append(
        Detection(
            alert_class=AlertClass.REVERSING_ALARM,
            stage="gate",
            score=_score(run.ppm_sum / run.n_tonal, p),
            provisional=not confirmed,
            t_onset=t_of(onset_sample),
            t_detect=t_of(f_idx * p.hop + p.n_fft),
        )
    )


def _end_run(state: GateState, p: GateParams) -> None:
    run = state.run
    state.run = None
    if run is None or run.ignore or run.continuous or not run.fired:
        return
    length_s = (run.last - run.first + 1) * p.hop / p.sample_rate
    if p.burst_min_s <= length_s <= p.burst_max_s:
        state.prev_burst = _Burst(run.first * p.hop, run.ref_freq)
