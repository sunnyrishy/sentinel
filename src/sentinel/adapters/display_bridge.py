"""WebSocket publisher for a phone bridge (integration architecture A).

The core runs on the laptop. This adapter publishes the alert and clear messages from the spec
over the dashboard WebSocket (ws://HOST:PORT/ws). A small phone app or script subscribes and
forwards each alert to the glasses through whatever the SDK offers. tools/bridge_echo.py is the
reference subscriber.

Differences from the browser simulator adapter:
  * It uses the display capabilities exactly as configured. Text is truncated to them, and when
    the real display cannot blink, the pipeline emulates blinking with show and clear cycles and
    the payload says blink=false so the subscriber does not blink twice.
  * Payloads are tiny (pre-rendered text lines, no extras) to keep link delay low.
The browser page also subscribes to /ws, so the simulator shows exactly what the bridge sends.

To reach a phone on the same network set dashboard.host to 0.0.0.0. There is no authentication,
so only do that on a network you trust.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sentinel.adapters.base import fit_lines
from sentinel.types import Alert, DisplayCaps

Publish = Callable[[dict[str, Any]], None]


class BridgeDisplay:
    def __init__(self, publish: Publish, caps: DisplayCaps | None = None) -> None:
        self._publish = publish
        self._caps = caps or DisplayCaps()

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
                "blink": bool(alert.blink and self._caps.supports_blink),
                "ttl_ms": int(alert.ttl_s * 1000),
            }
        )

    def clear(self, event_id: str | None = None) -> None:
        self._publish({"type": "clear", "event_id": event_id})

    def close(self) -> None:
        pass
