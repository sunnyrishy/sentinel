"""Binding data contracts from the spec. Audio in the core is mono, 16 kHz, float32 in [-1, 1]."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000


class AlertClass(str, Enum):
    REVERSING_ALARM = "reversing_alarm"
    VEHICLE_HORN = "vehicle_horn"
    FIRE_ALARM = "fire_alarm"


@dataclass(frozen=True)
class AudioFrame:
    samples: np.ndarray  # shape (n,), float32
    t_end: float  # time.monotonic() at the last sample


class AudioSource(Protocol):
    sample_rate: int  # equals SAMPLE_RATE after resampling

    def frames(self) -> Iterator[AudioFrame]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Detection:
    alert_class: AlertClass
    stage: str  # 'gate' or 'classifier'
    score: float  # 0 to 1
    provisional: bool  # True for the gate's first, unconfirmed hit
    t_onset: float  # monotonic time of the estimated sound onset
    t_detect: float  # monotonic time the detector fired


@dataclass(frozen=True)
class Alert:
    event_id: str
    alert_class: AlertClass
    tier: int  # 1 urgent, 2 notice
    line1: str  # for example 'BACKUP ALARM'
    line2: str | None  # 'NEARBY', 'LEFT', 'RIGHT', 'BEHIND' or None
    blink: bool
    ttl_s: float


@dataclass(frozen=True)
class DisplayCaps:
    lines: int = 2
    max_chars_per_line: int = 12  # ASSUMPTION until the SDK docs confirm
    color: str = "mono_green"  # ASSUMPTION: 'mono_green' or 'full'
    supports_blink: bool = False  # emulate with show/clear cycles if False


class DisplayAdapter(Protocol):
    def capabilities(self) -> DisplayCaps: ...

    def show(self, alert: Alert) -> None: ...

    def clear(self, event_id: str | None = None) -> None: ...

    def close(self) -> None: ...


# Alert wording table from the spec. Direction is unknown in v1, so line 2 is always NEARBY.
ALERT_WORDING: dict[AlertClass, tuple[int, str, str]] = {
    AlertClass.REVERSING_ALARM: (1, "BACKUP ALARM", "NEARBY"),
    AlertClass.VEHICLE_HORN: (1, "VEHICLE HORN", "NEARBY"),
    AlertClass.FIRE_ALARM: (1, "FIRE ALARM", "NEARBY"),
}
