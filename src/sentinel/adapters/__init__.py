"""Adapters: the only code that touches audio or display hardware."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
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

            # A text console cannot blink and must not emulate it with show and clear cycles.
            children.append(ConsoleDisplay(replace(caps, supports_blink=True)))
        elif n in ("sim", "bridge"):
            if publish is None:
                raise ConfigError(f"display '{n}' needs the dashboard. Enable dashboard in config.")
            if n == "sim":
                from sentinel.adapters.display_sim import SimDisplay

                children.append(SimDisplay(publish, caps))
            else:
                from sentinel.adapters.display_bridge import BridgeDisplay

                children.append(BridgeDisplay(publish, caps))
        else:  # microlumin: a stub that raises NotImplementedError with a clear message
            from sentinel.adapters.display_microlumin import MicroLuminDisplay

            children.append(MicroLuminDisplay(caps))
    return children[0] if len(children) == 1 else MultiDisplay(children)


def make_audio_source(
    cfg: SentinelConfig,
    kind: str = "mic",
    device: str | int | None = None,
    metrics: Any = None,
) -> Any:
    """Build the audio input. mic is the external microphone (L1). glasses is the L0 stub."""
    if kind == "mic":
        from sentinel.adapters.audio_mic import AudioMicSource

        return AudioMicSource(
            device if device is not None else cfg.audio.device, cfg.audio.frame_ms, metrics=metrics
        )
    if kind == "glasses":
        from sentinel.adapters.audio_glasses import AudioGlassesSource

        return AudioGlassesSource()
    raise ConfigError(f"unknown input '{kind}'. Choose mic or glasses")
