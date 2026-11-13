"""Display on the MicroLumin glasses. STUB until the SDK is known.

Nothing here invents SDK calls. The default path until the facts arrive is the console display and
the browser simulator, or the WebSocket bridge (display_bridge.py) with a phone app that forwards
alerts to the glasses.

TODO when docs/SDK_NOTES.md has the answers (change only this file, plus DisplayCaps in config):
  1. Connection path: phone app, BLE, Wi-Fi, or direct to the laptop? Open it in __init__.
  2. capabilities(): lines, max_chars_per_line, color ('mono_green' or 'full'), supports_blink.
     Set the same values in config/default.yaml under display.caps.
  3. show(alert): draw fit_lines(alert, self.capabilities()). Truncate, never wrap.
  4. clear(event_id): remove the text. event_id may be None, which means clear everything.
  5. close(): release the connection.
  6. The pipeline wraps this adapter in AsyncDisplay, so a slow call never blocks detection. Keep
     each call short anyway, and measure it with tools/hw_smoke_test.py.
  7. Brightness: can it be set in code? Test readability in bright light on the first night.
"""

from __future__ import annotations

from sentinel.types import Alert, DisplayCaps

NOT_IMPLEMENTED_MESSAGE = (
    "The MicroLumin display adapter (adapters/display_microlumin.py) is not implemented: the SDK "
    "details are not known yet. Use --display console or --display sim, or the phone bridge "
    "(--display bridge). See docs/SDK_NOTES.md."
)


class MicroLuminDisplay:
    """Placeholder that satisfies the DisplayAdapter protocol and refuses to run."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)

    def capabilities(self) -> DisplayCaps:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)

    def show(self, alert: Alert) -> None:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)

    def clear(self, event_id: str | None = None) -> None:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)

    def close(self) -> None:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)
