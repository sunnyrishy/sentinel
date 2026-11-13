"""Synthetic alarms, noise and SNR mixing. Synthetic audio is for tests and development only.

Nothing generated here may be used for reported performance numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import maximum_filter1d
from scipy.signal import butter, sosfilt

from sentinel.types import SAMPLE_RATE

FADE_S = 0.005
NoiseKind = str  # 'white' | 'pink' | 'brown' | 'babble'


def fade_gate(on_samples: int, fade_samples: int) -> np.ndarray:
    """A burst envelope of length on_samples with short raised-cosine fades."""
    env = np.ones(on_samples, dtype=np.float32)
    f = min(fade_samples, on_samples // 2)
    if f > 0:
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, f))).astype(np.float32)
        env[:f] = ramp
        env[-f:] = ramp[::-1]
    return env


def _pulse_train(
    carrier: np.ndarray, pulses_per_min: float, duration_s: float, duty: float
) -> np.ndarray:
    """Apply an on/off pulse envelope with the given pulse rate to a carrier signal."""
    n = len(carrier)
    period = round(SAMPLE_RATE * 60.0 / pulses_per_min)
    on = max(1, round(period * duty))
    env = np.zeros(n, dtype=np.float32)
    fade = int(FADE_S * SAMPLE_RATE)
    burst = fade_gate(on, fade)
    for start in range(0, n, period):
        seg = min(on, n - start)
        env[start : start + seg] = burst[:seg]
    return carrier * env


def tone(freq_hz: float, duration_s: float, harmonics: int = 0) -> np.ndarray:
    n = round(duration_s * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    sig = np.sin(2 * np.pi * freq_hz * t)
    for h in range(2, harmonics + 2):
        if freq_hz * h < SAMPLE_RATE / 2:
            sig = sig + np.sin(2 * np.pi * freq_hz * h * t) / (h * 1.5)
    return (0.5 * sig / np.max(np.abs(sig))).astype(np.float32)


def tonal_alarm(
    freq_hz: float = 1900.0,
    pulses_per_min: float = 70.0,
    duration_s: float = 6.0,
    duty: float = 0.5,
    harmonics: int = 0,
) -> np.ndarray:
    """Sine bursts with short fades to avoid clicks. Default 1900 Hz and 70 pulses per minute."""
    return _pulse_train(tone(freq_hz, duration_s, harmonics), pulses_per_min, duration_s, duty)


def _bandpass(x: np.ndarray, band_hz: tuple[float, float], order: int = 4) -> np.ndarray:
    nyq = SAMPLE_RATE / 2
    low = max(band_hz[0], 20.0) / nyq
    high = min(band_hz[1], nyq * 0.99) / nyq
    sos = butter(order, [low, high], btype="band", output="sos")
    return sosfilt(sos, x).astype(np.float32)


def broadband_alarm(
    pulses_per_min: float = 70.0,
    duration_s: float = 6.0,
    band_hz: tuple[float, float] = (300.0, 4000.0),
    seed: int = 0,
    duty: float = 0.5,
) -> np.ndarray:
    """Band-limited noise bursts, a stand-in for 'white noise' alarms."""
    rng = np.random.default_rng(seed)
    n = round(duration_s * SAMPLE_RATE)
    carrier = _bandpass(rng.standard_normal(n).astype(np.float32), band_hz)
    carrier = carrier / (np.max(np.abs(carrier)) + 1e-9) * 0.5
    return _pulse_train(carrier, pulses_per_min, duration_s, duty)


def horn(duration_s: float = 2.0, freqs_hz: tuple[float, float] = (400.0, 500.0)) -> np.ndarray:
    """A sustained two-tone signal with a few harmonics, a stand-in for a car horn."""
    a = tone(freqs_hz[0], duration_s, harmonics=3)
    b = tone(freqs_hz[1], duration_s, harmonics=3)
    sig = 0.5 * (a + b)
    fade = int(0.02 * SAMPLE_RATE)
    sig[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
    sig[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return sig.astype(np.float32)


def fire_alarm_t3(duration_s: float = 8.0, freq_hz: float = 3100.0) -> np.ndarray:
    """Three 0.5 s beeps with 0.5 s gaps, then a 1.5 s pause, repeated (temporal-3 pattern)."""
    n = round(duration_s * SAMPLE_RATE)
    out = np.zeros(n, dtype=np.float32)
    beep = tone(freq_hz, 0.5) * fade_gate(int(0.5 * SAMPLE_RATE), int(FADE_S * SAMPLE_RATE))
    cycle = 4.0  # 3 beeps + 2 gaps of 0.5 s + final 1.5 s pause total = 0.5*3 + 0.5*2 + 1.5
    t = 0.0
    while t < duration_s:
        for k in range(3):
            start = round((t + k * 1.0) * SAMPLE_RATE)
            if start >= n:
                break
            seg = min(len(beep), n - start)
            out[start : start + seg] = beep[:seg]
        t += cycle
    return out


def noise(
    kind: NoiseKind, duration_s: float, seed: int = 0, level_dbfs: float = -30.0
) -> np.ndarray:
    """White, pink, brown or crude babble noise scaled to an RMS level in dBFS."""
    rng = np.random.default_rng(seed)
    n = round(duration_s * SAMPLE_RATE)
    if kind == "white":
        x = rng.standard_normal(n)
    elif kind in ("pink", "brown"):
        x = _colored(rng, n, exponent=1.0 if kind == "pink" else 2.0)
    elif kind == "babble":
        x = _babble(rng, n)
    else:
        raise ValueError(f"unknown noise kind '{kind}'. Use white, pink, brown or babble")
    return set_rms_dbfs(x.astype(np.float32), level_dbfs)


def _colored(rng: np.random.Generator, n: int, exponent: float) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
    freqs[0] = freqs[1]
    spec = spec / freqs ** (exponent / 2)  # power ~ 1/f^exponent
    return np.fft.irfft(spec, n)


def _babble(rng: np.random.Generator, n: int, talkers: int = 6) -> np.ndarray:
    """Sum of band-limited noise with random syllable-rate envelopes. No harmonics, by design."""
    sos_env = butter(2, 6.0 / (SAMPLE_RATE / 2), output="sos")
    mix = np.zeros(n)
    for _ in range(talkers):
        carrier = _bandpass(rng.standard_normal(n), (150.0, 3800.0)).astype(np.float64)
        env = sosfilt(sos_env, rng.standard_normal(n))
        env = np.clip(env / (np.std(env) + 1e-9), 0, None) ** 1.5
        mix += carrier * env
    return mix


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def set_rms_dbfs(x: np.ndarray, level_dbfs: float) -> np.ndarray:
    cur = rms(x)
    if cur == 0:
        return x
    return (x * (10 ** (level_dbfs / 20) / cur)).astype(np.float32)


def active_mask(signal: np.ndarray, floor: float = 0.05) -> np.ndarray:
    """Samples belonging to the active segments of a bursty signal (5 ms smoothed envelope)."""
    env = maximum_filter1d(np.abs(signal), size=int(0.005 * SAMPLE_RATE))
    peak = float(env.max()) if len(env) else 0.0
    return env > floor * peak if peak > 0 else np.zeros(len(signal), dtype=bool)


@dataclass(frozen=True)
class MixResult:
    audio: np.ndarray
    onset_s: float  # first active sample of the signal, seconds from track start
    end_s: float  # last active sample of the signal


def scale_to_snr(signal: np.ndarray, noise_rms: float, snr_db: float) -> np.ndarray:
    """Scale a signal so its RMS over active segments sits snr_db above noise_rms."""
    mask = active_mask(signal)
    sig_rms = rms(signal[mask])
    if sig_rms == 0 or noise_rms == 0:
        return signal.astype(np.float32)
    gain = noise_rms * 10 ** (snr_db / 20) / sig_rms
    return (signal * gain).astype(np.float32)


def mix(signal: np.ndarray, noise_track: np.ndarray, snr_db: float) -> MixResult:
    """Mix signal into noise so the signal's active-segment RMS is at the requested SNR.

    The noise keeps its level. Signal and noise must be the same length. Returns the mixed audio
    plus the ground-truth onset and end of the signal.
    """
    if len(signal) != len(noise_track):
        raise ValueError("signal and noise must have the same length")
    scaled = scale_to_snr(signal, rms(noise_track), snr_db)
    mask = active_mask(signal)
    idx = np.flatnonzero(mask)
    onset = float(idx[0] / SAMPLE_RATE) if len(idx) else 0.0
    end = float((idx[-1] + 1) / SAMPLE_RATE) if len(idx) else 0.0
    out = np.clip(noise_track + scaled, -1.0, 1.0).astype(np.float32)
    return MixResult(out, onset, end)
