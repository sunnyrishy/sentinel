"""Adapters: the only code that touches audio or display hardware."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sentinel.adapters.base import MultiDisplay
from sentinel.config import ConfigError, SentinelConfig
from sentinel.types import DisplayAdapter

Publish = Callable[[dict[str, Any]], None]
KNOWN_DISPLAYS = ("console", "sim", "bridge", "microlumin")


def display_names(cfg: SentinelConfig, name: str | None = None) -> list[str]:
    """Adapter names from an override like 'console,sim' or from the config."""
    names = [n.strip() for n in (name or cfg.display.adapter).split(",") if n.strip()]
    for n in names:
        if n not in KNOWN_DISPLAYS:
            raise ConfigError(f"unknown display adapter '{n}'. Choose from {list(KNOWN_DISPLAYS)}")
    return names


def make_display(
    cfg: SentinelConfig, name: str | None = None, publish: Publish | None = None
) -> DisplayAdapter:
    """Build the configured display adapter, or several joined by commas.

    sim and bridge publish over the dashboard WebSocket, so they need publish (hub.publish).
    Imports are lazy so stubs never load unless used.
    """
    caps = cfg.display.caps.to_caps()
    children: list[DisplayAdapter] = []
    for n in display_names(cfg, name):
        if n == "console":
            from sentinel.adapters.display_console import ConsoleDisplay

            children.append(ConsoleDisplay(caps))
        elif n == "sim":
            if publish is None:
                raise ConfigError("display 'sim' needs the dashboard. Enable dashboard in config.")
            from sentinel.adapters.display_sim import SimDisplay

            children.append(SimDisplay(publish, caps))
        else:
            raise ConfigError(f"display adapter '{n}' is not available yet")
    return children[0] if len(children) == 1 else MultiDisplay(children)
