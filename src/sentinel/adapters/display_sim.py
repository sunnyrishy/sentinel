"""Browser simulator display: publishes alert and clear messages over the dashboard WebSocket.

The simulator page draws them the way the real glasses display is described: a small region in the
upper right, two lines of green text on black.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from sentinel.adapters.base import fit_lines
from sentinel.types import Alert, DisplayCaps

Publish = Callable[[dict[str, Any]], None]


class SimDisplay:
    def __init__(self, publish: Publish, caps: DisplayCaps | None = None) -> None:
        self._publish = publish
        # The browser blinks natively, so the pipeline does not need to emulate it.
        self._caps = replace(caps or DisplayCaps(), supports_blink=True)

    def capabilities(self) -> DisplayCaps:
        return self._caps

    def show(self, alert: Alert) -> None:
        lines = fit_lines(alert, self._caps)
        self._publish(
            {
                "type": "alert",
                "event_id": alert.event_id,
                "line1": lines[0] if lines else "",
                "line2": lines[1] if len(lines) > 1 else None,
                "tier": alert.tier,
                "blink": alert.blink,
                "ttl_ms": int(alert.ttl_s * 1000),
            }
        )

    def clear(self, event_id: str | None = None) -> None:
        self._publish({"type": "clear", "event_id": event_id})

    def close(self) -> None:
        pass
