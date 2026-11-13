"""Adapters: the only code that touches audio or display hardware."""

from __future__ import annotations

from sentinel.config import ConfigError, SentinelConfig
from sentinel.types import DisplayAdapter


def make_display(cfg: SentinelConfig, name: str | None = None) -> DisplayAdapter:
    """Build the configured display adapter. Imports are lazy so stubs never load unless used."""
    adapter = name or cfg.display.adapter
    caps = cfg.display.caps.to_caps()
    if adapter == "console":
        from sentinel.adapters.display_console import ConsoleDisplay

        return ConsoleDisplay(caps)
    raise ConfigError(f"display adapter '{adapter}' is not available yet")
