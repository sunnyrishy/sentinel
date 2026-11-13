"""Shared start-up for the command line tools: event log, dashboard, display and listeners."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sentinel.adapters import display_names, make_display
from sentinel.config import ConfigError, SentinelConfig
from sentinel.dashboard.server import DashboardServer, Hub, start_dashboard
from sentinel.eventlog import EventLog
from sentinel.pipeline import Pipeline
from sentinel.types import DisplayAdapter


@dataclass
class Runtime:
    eventlog: EventLog
    display: DisplayAdapter
    hub: Hub | None
    server: DashboardServer | None
    source_label: str

    def attach(self, pipeline: Pipeline) -> None:
        if self.hub is not None:
            pipeline.add_listener(self.hub.publish)

    @property
    def url(self) -> str | None:
        return self.server.url if self.server else None

    def hold(self) -> None:
        """Keep serving the dashboard until Ctrl+C."""
        if not self.server:
            return
        print(f"Dashboard still running at {self.server.url} (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    def close(self) -> None:
        if self.server:
            self.server.stop()
        self.eventlog.close()


def start_runtime(
    cfg: SentinelConfig,
    source_label: str,
    display_name: str | None = None,
    db: str | None = None,
    serve: bool = False,
    use_dashboard: bool = True,
) -> Runtime:
    """Create everything a run needs. The dashboard starts when a sim or bridge display is chosen,
    or when serve is true (which adds the sim display so alerts appear in the browser)."""
    names = display_names(cfg, display_name)
    if serve and "sim" not in names:
        names.append("sim")
    needs_hub = any(n in ("sim", "bridge") for n in names)
    eventlog = EventLog(db or cfg.eventlog.path)
    hub = server = None
    if needs_hub or (serve and use_dashboard):
        if not (cfg.dashboard.enabled and use_dashboard):
            raise ConfigError("this display needs the dashboard, but dashboard is disabled")

        def status() -> dict[str, Any]:
            return {
                "source": source_label,
                "recording_badge": source_label != "live",
                "version": "0.1.0",
            }

        hub, server = start_dashboard(cfg, eventlog, status)
    display = make_display(cfg, ",".join(names), hub.publish if hub else None)
    return Runtime(eventlog, display, hub, server, source_label)
