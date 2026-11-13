"""Console display: prints alerts to a text stream. The default, simplest adapter."""

from __future__ import annotations

import sys
import time
from typing import TextIO

from sentinel.adapters.base import fit_lines
from sentinel.types import Alert, DisplayCaps


class ConsoleDisplay:
    def __init__(self, caps: DisplayCaps | None = None, stream: TextIO | None = None) -> None:
        self._caps = caps or DisplayCaps(supports_blink=True)
        self._stream = stream or sys.stdout

    def capabilities(self) -> DisplayCaps:
        return self._caps

    def show(self, alert: Alert) -> None:
        text = " | ".join(fit_lines(alert, self._caps))
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] ALERT tier {alert.tier}: {text}  ({alert.event_id})", file=self._stream)
        self._stream.flush()

    def clear(self, event_id: str | None = None) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] clear {event_id or 'all'}", file=self._stream)
        self._stream.flush()

    def close(self) -> None:
        self._stream.flush()
