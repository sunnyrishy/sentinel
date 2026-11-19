"""Shared start-up for the command line tools: event log, dashboard, display and listeners."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from sentinel.adapters import display_names, make_display
from sentinel.config import ConfigError, SentinelConfig
from sentinel.dashboard.server import DashboardServer, Hub, start_dashboard
from sentinel.eventlog import EventLog
from sentinel.pipeline import Pipeline
from sentinel.profile import ProfileStore
from sentinel.types import Alert, AlertClass, DisplayAdapter


@dataclass
class Runtime:
    eventlog: EventLog
    display: DisplayAdapter
    hub: Hub | None
    server: DashboardServer | None
    source_label: str
    profile: ProfileStore | None = None

    def attach(self, pipeline: Pipeline) -> None:
        if self.hub is not None:
            pipeline.add_listener(self.hub.publish)
        if self.profile is not None:
            pipeline.set_profile(self.profile)

    def send_test_alert(self) -> None:
        """Push a clearly labeled test alert through the real display adapter, then clear it.

        Use it to check the whole chain (bridge, phone, glasses) without waiting for an alarm.
        """
        alert = Alert("e_test", AlertClass.REVERSING_ALARM, 1, "TEST ALERT", "NEARBY", True, 3.0)
        self.display.show(alert)
        threading.Timer(3.0, lambda: self.display.clear("e_test")).start()

    @property
    def phone_urls(self) -> list[str]:
        """Addresses a phone on the same network can open (only when started with lan=True)."""
        if not self.server or self.server.host != "0.0.0.0":
            return []
        return [f"http://{ip}:{self.server.port}/phone" for ip in lan_addresses()]

    @property
    def url(self) -> str | None:
        """Address to open on this computer (0.0.0.0 is not an address you can browse to)."""
        return self.server.url.replace("//0.0.0.0:", "//127.0.0.1:") if self.server else None

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


def lan_addresses() -> list[str]:
    """This machine's non-loopback IPv4 addresses. Does not send any network traffic."""
    found: list[str] = []
    try:  # a UDP "connect" picks the outgoing interface without sending any packet
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            ip = probe.getsockname()[0]
            if not ip.startswith("127.") and ip != "0.0.0.0":
                found.append(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found or ["<this-computer-ip>"]


def start_runtime(
    cfg: SentinelConfig,
    source_label: str,
    display_name: str | None = None,
    db: str | None = None,
    serve: bool = False,
    use_dashboard: bool = True,
    extra_status: dict[str, Any] | None = None,
    lan: bool = False,
) -> Runtime:
    """Create everything a run needs. The dashboard starts when a sim or bridge display is chosen,
    or when serve is true (which adds the sim display so alerts appear in the browser)."""
    names = display_names(cfg, display_name)
    if serve and "sim" not in names:
        names.append("sim")
    needs_hub = any(n in ("sim", "bridge") for n in names)
    eventlog = EventLog(db or cfg.eventlog.path)
    profile = ProfileStore(None if (db or cfg.eventlog.path) == ":memory:" else "data/profile.json")
    if lan:
        cfg.dashboard.host = "0.0.0.0"  # reachable from phones on this network
    holder: dict[str, Runtime] = {}
    hub = server = None
    if needs_hub or (serve and use_dashboard):
        if not (cfg.dashboard.enabled and use_dashboard):
            raise ConfigError("this display needs the dashboard, but dashboard is disabled")

        def status() -> dict[str, Any]:
            return {
                "source": source_label,
                "recording_badge": source_label != "live",
                "version": "0.1.0",
                **(extra_status or {}),
            }

        hub, server = start_dashboard(
            cfg, eventlog, status, profile, lambda: holder["rt"].send_test_alert()
        )
    display = make_display(cfg, ",".join(names), hub.publish if hub else None)
    runtime = Runtime(eventlog, display, hub, server, source_label, profile)
    holder["rt"] = runtime
    return runtime
