"""FastAPI dashboard: static page, JSON endpoints and the WebSocket feed.

The WebSocket protocol (JSON text frames) is the contract from the spec. The browser simulator and
any future phone bridge both subscribe to /ws.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

from sentinel.config import SentinelConfig
from sentinel.eventlog import EventLog

STATIC_DIR = Path(__file__).parent / "static"
Message = dict[str, Any]


class Hub:
    """Fan-out of WebSocket messages. publish() is thread-safe and may be called from any thread."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue[Message]] = set()
        self._lock = threading.Lock()
        self._active: tuple[Message, float] | None = None  # (alert message, monotonic deadline)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, message: Message) -> None:
        self._track(message)
        loop = self._loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._fanout, message)

    def _track(self, message: Message) -> None:
        """Remember the alert currently on the simulator so a page opened later still shows it."""
        with self._lock:
            kind = message.get("type")
            if kind == "alert":
                ttl = float(message.get("ttl_ms", 3000)) / 1000.0
                self._active = (message, time.monotonic() + ttl)
            elif kind == "clear" and self._active:
                target = message.get("event_id")
                if target is None or target == self._active[0].get("event_id"):
                    self._active = None

    def snapshot(self) -> list[Message]:
        with self._lock:
            if self._active and time.monotonic() < self._active[1]:
                msg, deadline = self._active
                return [{**msg, "ttl_ms": int((deadline - time.monotonic()) * 1000)}]
        return []

    def _fanout(self, message: Message) -> None:
        for q in list(self._subs):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()  # a slow browser loses the oldest message, never blocks us
            q.put_nowait(message)

    def subscribe(self) -> asyncio.Queue[Message]:
        q: asyncio.Queue[Message] = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Message]) -> None:
        self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


def stats_message(log: EventLog) -> Message:
    s = log.stats()
    return {
        "type": "stats",
        "events_total": s["events_total"],
        "p50_ms": None if s["p50_ms"] is None else round(s["p50_ms"]),
        "p95_ms": None if s["p95_ms"] is None else round(s["p95_ms"]),
    }


def default_status() -> dict[str, Any]:
    return {"source": "live", "recording_badge": False}


def create_app(
    eventlog: EventLog,
    hub: Hub,
    status: Callable[[], dict[str, Any]] = default_status,
    stats_interval_s: float = 2.0,
) -> FastAPI:
    async def stats_loop() -> None:
        while True:
            await asyncio.sleep(stats_interval_s)
            hub._fanout(stats_message(eventlog))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.attach_loop(asyncio.get_running_loop())
        task = asyncio.create_task(stats_loop())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="Sentinel", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/events")
    def events(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return eventlog.list(limit=limit)

    @app.get("/api/events.csv")
    def events_csv() -> Response:
        return Response(
            eventlog.to_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="sentinel_events.csv"'},
        )

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return eventlog.stats()

    @app.get("/api/per_hour")
    def per_hour() -> list[dict[str, Any]]:
        return eventlog.per_hour()

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        return status()

    @app.post("/api/events/{event_id}/ack")
    def acknowledge(event_id: str) -> dict[str, Any]:
        if not eventlog.acknowledge(event_id):
            raise HTTPException(status_code=404, detail=f"no event {event_id}")
        hub.publish({"type": "event", "event": {"id": event_id, "acknowledged": True}})
        return {"id": event_id, "acknowledged": True}

    @app.websocket("/ws")
    async def ws_feed(ws: WebSocket) -> None:
        await ws.accept()
        q = hub.subscribe()
        try:
            for msg in hub.snapshot():
                await ws.send_json(msg)
            await ws.send_json(stats_message(eventlog))

            async def pump() -> None:
                while True:
                    await ws.send_json(await q.get())

            sender = asyncio.create_task(pump())
            try:
                while True:
                    await ws.receive_text()  # clients send nothing we need; this detects close
            except WebSocketDisconnect:
                pass
            finally:
                sender.cancel()
        finally:
            hub.unsubscribe(q)

    return app


class DashboardServer:
    """Runs uvicorn on a background thread so the pipeline process can serve the dashboard."""

    def __init__(self, app: FastAPI, host: str, port: int) -> None:
        self.host, self.port = host, port
        self._server = uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, log_level="warning", ws="auto")
        )
        self._thread = threading.Thread(target=self._run, name="dashboard", daemon=True)
        self._error: BaseException | None = None

    def _run(self) -> None:
        try:
            self._server.run()
        except BaseException as exc:  # uvicorn exits via SystemExit on bind failure
            self._error = exc

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self, timeout_s: float = 8.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._server.started:
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.02)
        raise RuntimeError(
            f"Could not start the dashboard on {self.host}:{self.port}. "
            "Is the port already in use? Change dashboard.port in the config."
        )

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def start_dashboard(
    cfg: SentinelConfig,
    eventlog: EventLog,
    status: Callable[[], dict[str, Any]] = default_status,
) -> tuple[Hub, DashboardServer]:
    """Create the hub and app, start serving, and return both."""
    hub = Hub()
    server = DashboardServer(
        create_app(eventlog, hub, status), cfg.dashboard.host, cfg.dashboard.port
    )
    server.start()
    return hub, server
