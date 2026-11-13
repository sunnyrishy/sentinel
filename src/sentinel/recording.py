"""Optional raw audio recording. Off by default; only the --record flag enables it."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import soundfile as sf

from sentinel.config import resolve_path
from sentinel.types import SAMPLE_RATE


class WavRecorder:
    """Writes the incoming 16 kHz mono stream to data/recordings/rec_<timestamp>.wav."""

    def __init__(self, directory: str | Path) -> None:
        d = resolve_path(directory)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"rec_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        self._file = sf.SoundFile(
            str(self.path), mode="w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16"
        )

    def write(self, samples: np.ndarray) -> None:
        self._file.write(samples)

    def close(self) -> None:
        self._file.close()
