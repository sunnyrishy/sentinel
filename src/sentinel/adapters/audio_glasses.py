"""Audio from the glasses microphone (fallback ladder level L0). STUB until the SDK is known.

Whether developers can read the glasses' microphone at all is unknown (spec, Hardware and SDK).
Nothing here invents SDK calls. Until the facts arrive, use an external microphone (L1) through
sentinel.adapters.audio_mic.AudioMicSource.

TODO when docs/SDK_NOTES.md has the answers (change only this file):
  1. Does the app receive the glasses' microphone stream? Raw PCM? Which sample rate and channels?
  2. How is it delivered: callback, socket, BLE characteristic, phone app forwarding?
  3. Convert to mono, 16 kHz, float32 in [-1, 1] (use StreamResampler from audio_mic for the rate).
  4. Stamp every AudioFrame with time.monotonic() at its last sample.
  5. frames() must be a generator that ends when close() is called.
  6. Record the input latency of the path, which software timestamps cannot see.
"""

from __future__ import annotations

from collections.abc import Iterator

from sentinel.types import SAMPLE_RATE, AudioFrame

NOT_IMPLEMENTED_MESSAGE = (
    "The glasses microphone input (adapters/audio_glasses.py) is not implemented: the MicroLumin "
    "SDK details are not known yet. Use the external microphone instead (--input mic). "
    "See docs/SDK_NOTES.md."
)


class AudioGlassesSource:
    """Placeholder that satisfies the AudioSource protocol and refuses to run."""

    sample_rate = SAMPLE_RATE

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)

    def frames(self) -> Iterator[AudioFrame]:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)

    def close(self) -> None:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)
