"""Stage 1 gate tests. All audio here is SYNTHETIC."""

from __future__ import annotations

import numpy as np
import pytest

from sentinel.config import load_config
from sentinel.gate.tonal import GateParams, GateState, process
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, Detection

PARAMS = GateParams.from_config(load_config().gate)
DUR = 20.0


def run_gate(audio: np.ndarray, block: int = 512, params: GateParams = PARAMS) -> list[Detection]:
    state = GateState()
    out: list[Detection] = []
    for pos in range(0, len(audio), block):
        chunk = audio[pos : pos + block]
        out.extend(process(state, chunk, (pos + len(chunk)) / SAMPLE_RATE, params))
    return out


def place(sig: np.ndarray, start_s: float, total_s: float = DUR) -> np.ndarray:
    out = np.zeros(int(total_s * SAMPLE_RATE), dtype=np.float32)
    s = int(start_s * SAMPLE_RATE)
    out[s : s + len(sig)] = sig[: len(out) - s]
    return out


def alarm_in_noise(kind: str, snr_db: float, seed: int) -> np.ndarray:
    sig = place(synth.tonal_alarm(1900, 70, 6), 5.0)
    bed = synth.noise(kind, DUR, seed=seed, level_dbfs=-30)
    return synth.mix(sig, bed, snr_db).audio


@pytest.mark.parametrize("noise_kind", ["white", "pink"])
def test_detects_70ppm_alarm_at_plus_10db_in_9_of_10_runs(noise_kind: str) -> None:
    hits = 0
    for seed in range(10):
        dets = run_gate(alarm_in_noise(noise_kind, 10, seed))
        provisional = [d for d in dets if d.provisional]
        confirmed = [d for d in dets if not d.provisional]
        if provisional and confirmed and provisional[0].t_detect < confirmed[0].t_detect:
            hits += 1
    assert hits >= 9


def test_detects_at_0db_snr_too_in_most_runs() -> None:
    hits = sum(
        any(not d.provisional for d in run_gate(alarm_in_noise("pink", 0, seed)))
        for seed in range(10)
    )
    assert hits >= 8


def test_provisional_comes_first_and_onset_is_close_to_truth() -> None:
    dets = run_gate(alarm_in_noise("pink", 10, 1))
    assert dets[0].provisional and all(not d.provisional for d in dets[1:])
    assert all(d.stage == "gate" and d.alert_class.value == "reversing_alarm" for d in dets)
    assert abs(dets[0].t_onset - 5.0) < 0.1
    assert 0.25 <= dets[0].t_detect - dets[0].t_onset < 0.45


def test_confirmed_detection_follows_second_pulse() -> None:
    dets = run_gate(alarm_in_noise("white", 10, 2))
    first_conf = next(d for d in dets if not d.provisional)
    assert 0.6 <= first_conf.t_onset - dets[0].t_onset <= 1.5


@pytest.mark.parametrize("ppm", [60, 80])
def test_detects_other_spec_pulse_rates(ppm: int) -> None:
    sig = place(synth.tonal_alarm(1900, ppm, 6), 5.0)
    audio = synth.mix(sig, synth.noise("pink", DUR, seed=3), 10).audio
    assert any(not d.provisional for d in run_gate(audio))


def test_block_size_does_not_change_detections() -> None:
    audio = alarm_in_noise("pink", 10, 4)
    a = run_gate(audio, block=512)
    b = run_gate(audio, block=1000)
    c = run_gate(audio, block=77)
    assert [(d.provisional, round(d.t_onset, 3)) for d in a] == [
        (d.provisional, round(d.t_onset, 3)) for d in b
    ]
    assert len(a) == len(c)


def test_silent_on_continuous_tone_from_stream_start() -> None:
    tone = synth.tone(1900, DUR) * 0.5
    audio = synth.mix(tone, synth.noise("pink", DUR, seed=1), 10).audio
    assert run_gate(audio) == []


def test_continuous_tone_mid_stream_never_confirms() -> None:
    """Known limit (DECISIONS D9): one provisional is allowed, a confirmation is not."""
    sig = place(synth.tone(1900, 8.0), 5.0)
    audio = synth.mix(sig, synth.noise("pink", DUR, seed=1), 10).audio
    dets = run_gate(audio)
    assert all(d.provisional for d in dets)
    assert len(dets) <= 1


@pytest.mark.parametrize("kind", ["white", "pink", "brown", "babble"])
def test_silent_on_noise(kind: str) -> None:
    for seed in range(3):
        assert run_gate(synth.noise(kind, 30.0, seed=seed, level_dbfs=-20)) == []


def test_silent_on_single_click_and_lone_short_beep() -> None:
    bed = synth.noise("pink", DUR, seed=5)
    click = np.zeros(160, dtype=np.float32)
    click[:32] = np.hanning(32)
    beep = synth.tone(1900, 0.1) * synth.fade_gate(1600, 80)
    audio = synth.mix(place(click, 4.0) + place(beep, 10.0), bed, 10).audio
    assert run_gate(audio) == []


def test_silent_on_digital_silence() -> None:
    assert run_gate(np.zeros(SAMPLE_RATE * 5, dtype=np.float32)) == []


def test_siren_sweep_never_confirms() -> None:
    """A slow wail is briefly stable at each turning point, so a provisional can fire (D9)."""
    t = np.arange(int(DUR * SAMPLE_RATE)) / SAMPLE_RATE
    freq = 1200 + 600 * np.sin(2 * np.pi * 0.5 * t)
    sweep = (0.5 * np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE)).astype(np.float32)
    dets = run_gate(synth.mix(sweep, synth.noise("pink", DUR, seed=2), 10).audio)
    assert all(d.provisional for d in dets)


def test_ignores_broadband_alarm() -> None:
    sig = place(synth.broadband_alarm(70, 6), 5.0)
    assert run_gate(synth.mix(sig, synth.noise("pink", DUR, seed=2), 10).audio) == []


def test_pulses_at_different_frequencies_do_not_confirm() -> None:
    a = place(synth.tonal_alarm(1200, 70, 0.45, duty=0.99), 5.0)
    b = place(synth.tonal_alarm(2400, 70, 0.45, duty=0.99), 5.9)
    audio = synth.mix(a + b, synth.noise("pink", DUR, seed=2), 10).audio
    assert all(d.provisional for d in run_gate(audio))


def test_out_of_band_tone_is_ignored() -> None:
    sig = place(synth.tonal_alarm(5000, 70, 6), 5.0)
    assert run_gate(synth.mix(sig, synth.noise("pink", DUR, seed=2), 10).audio) == []


def test_detection_times_are_in_stream_time() -> None:
    audio = alarm_in_noise("pink", 10, 7)
    t_offset = 1000.0
    state = GateState()
    out: list[Detection] = []
    for pos in range(0, len(audio), 512):
        chunk = audio[pos : pos + 512]
        out.extend(process(state, chunk, t_offset + (pos + len(chunk)) / SAMPLE_RATE, PARAMS))
    assert abs(out[0].t_onset - (t_offset + 5.0)) < 0.1


def test_synth_mix_hits_requested_snr() -> None:
    sig = place(synth.tonal_alarm(1900, 70, 6), 5.0)
    bed = synth.noise("white", DUR, seed=1, level_dbfs=-30)
    res = synth.mix(sig, bed, 10)
    scaled = res.audio - bed
    active = synth.active_mask(sig)
    snr = 20 * np.log10(synth.rms(scaled[active]) / synth.rms(bed))
    assert snr == pytest.approx(10, abs=0.3)
    assert res.onset_s == pytest.approx(5.0, abs=0.01)
    assert 10.5 < res.end_s < 11.5
