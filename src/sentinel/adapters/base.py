"""Adapter protocols plus the shared pieces every display needs.

The protocols themselves live in sentinel.types (binding contracts) and are re-exported here.
AsyncDisplay is the wrapper the pipeline puts around any adapter so a slow display never blocks
detection: show and clear run on their own thread with a timeout.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable

from sentinel.types import Alert, AudioSource, DisplayAdapter, DisplayCaps

__all__ = ["Alert", "AsyncDisplay", "AudioSource", "DisplayAdapter", "DisplayCaps", "fit_lines"]

log = logging.getLogger("sentinel.display")


def fit_lines(alert: Alert, caps: DisplayCaps) -> list[str]:
    """Alert text truncated to the display capabilities: at most caps.lines lines of
    caps.max_chars_per_line characters. Adapters must call this before drawing."""
    lines = [alert.line1] + ([alert.line2] if alert.line2 else [])
    return [line[: caps.max_chars_per_line] for line in lines[: caps.lines]]


class AsyncDisplay:
    """Runs an adapter's show and clear on a worker thread, each call guarded by a timeout.

    If the adapter does not return within call_timeout_s the call is abandoned (its thread is a
    daemon), a timeout is counted and the next command proceeds. If the command queue is full the
    oldest command is dropped. When the adapter cannot blink and the alert asks for it, blinking is
    emulated with show and clear cycles.
    """

    def __init__(
        self,
        inner: DisplayAdapter,
        clock: Callable[[], float] = time.monotonic,
        call_timeout_s: float = 1.0,
        on_shown: Callable[[str, float], None] | None = None,
        on_timeout: Callable[[], None] | None = None,
        blink_period_s: float = 0.5,
        max_queue: int = 16,
    ) -> None:
        self._inner = inner
        self._clock = clock
        self._timeout = call_timeout_s
        self._on_shown = on_shown
        self._on_timeout = on_timeout
        self._blink_period = blink_period_s
        self._q: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._blink_alert: Alert | None = None
        self._blink_on = True
        self._thread = threading.Thread(target=self._run, name="display", daemon=True)
        self._thread.start()

    # DisplayAdapter protocol -------------------------------------------------------------------
    def capabilities(self) -> DisplayCaps:
        return self._inner.capabilities()

    def show(self, alert: Alert) -> None:
        self._put(("show", alert))

    def clear(self, event_id: str | None = None) -> None:
        self._put(("clear", event_id))

    def close(self) -> None:
        self._stop.set()
        self._put(("stop", None))
        self._thread.join(timeout=self._timeout + 0.5)
        self._guarded(self._inner.close)

    # internals -----------------------------------------------------------------------------------
    def _put(self, cmd: tuple[str, object]) -> None:
        while True:
            try:
                self._q.put_nowait(cmd)
                return
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self._q.get_nowait()  # drop the oldest command rather than block detection

    def _guarded(self, fn: Callable[[], None]) -> bool:
        """Run fn on a helper thread and wait up to the timeout. True if it finished in time."""
        done = threading.Event()
        err: list[BaseException] = []

        def target() -> None:
            try:
                fn()
            except BaseException as exc:  # adapter bugs must not kill the worker
                err.append(exc)
            finally:
                done.set()

        threading.Thread(target=target, name="display-call", daemon=True).start()
        if not done.wait(self._timeout):
            log.warning("display call timed out after %.2f s", self._timeout)
            if self._on_timeout:
                self._on_timeout()
            return False
        if err:
            log.error("display adapter raised: %r", err[0])
            return False
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            timeout = self._blink_period if self._blink_alert is not None else None
            try:
                kind, arg = self._q.get(timeout=timeout)
            except queue.Empty:
                self._blink_step()
                continue
            if kind == "stop":
                return
            if kind == "show":
                self._do_show(arg)  # type: ignore[arg-type]
            elif kind == "clear":
                self._do_clear(arg)  # type: ignore[arg-type]

    def _do_show(self, alert: Alert) -> None:
        ok = self._guarded(lambda: self._inner.show(alert))
        t_display = self._clock()
        if ok and self._on_shown:
            self._on_shown(alert.event_id, t_display)
        wants_blink = alert.blink and not self._inner.capabilities().supports_blink
        self._blink_alert = alert if wants_blink else None
        self._blink_on = True

    def _do_clear(self, event_id: str | None) -> None:
        if self._blink_alert is not None and event_id in (None, self._blink_alert.event_id):
            self._blink_alert = None
        self._guarded(lambda: self._inner.clear(event_id))

    def _blink_step(self) -> None:
        alert = self._blink_alert
        if alert is None:
            return
        self._blink_on = not self._blink_on
        if self._blink_on:
            self._guarded(lambda: self._inner.show(alert))
        else:
            self._guarded(lambda: self._inner.clear(alert.event_id))
