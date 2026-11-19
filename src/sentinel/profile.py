"""Per-person profile: which sounds matter to this wearer and how sensitive Sentinel should be.

Changes apply live, without a restart. The profile is saved to a small JSON file so it survives
restarts. It holds settings only, never audio.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.config import resolve_path
from sentinel.types import AlertClass

SENSITIVITY_MIN = 0.5
SENSITIVITY_MAX = 1.5


class ProfileError(ValueError):
    """An invalid profile update, with a message meant for a human."""


@dataclass
class Profile:
    enabled: dict[str, bool] = field(default_factory=lambda: {c.value: True for c in AlertClass})
    # 1.0 is the configured behaviour. Higher is more sensitive (lower thresholds), lower is
    # stricter (fewer false alerts).
    sensitivity: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": dict(self.enabled), "sensitivity": self.sensitivity}


class ProfileStore:
    """Thread-safe holder read by the pipeline and written by the phone page."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._path = resolve_path(path) if path else None
        self._profile = Profile()
        self._version = 0
        if self._path and self._path.exists():
            try:
                self._profile = self._validated(json.loads(self._path.read_text()))
            except (ValueError, OSError):
                self._profile = Profile()  # a damaged file must never stop Sentinel starting

    @staticmethod
    def _validated(data: dict[str, Any], base: Profile | None = None) -> Profile:
        prof = Profile(dict((base or Profile()).enabled), (base or Profile()).sensitivity)
        if not isinstance(data, dict):
            raise ProfileError("profile must be a JSON object")
        unknown = set(data) - {"enabled", "sensitivity"}
        if unknown:
            raise ProfileError(f"unknown profile keys {sorted(unknown)}")
        for name, on in (data.get("enabled") or {}).items():
            if name not in prof.enabled:
                raise ProfileError(f"unknown alert class '{name}'. Valid: {sorted(prof.enabled)}")
            if not isinstance(on, bool):
                raise ProfileError(f"enabled.{name} must be true or false")
            prof.enabled[name] = on
        if "sensitivity" in data:
            s = data["sensitivity"]
            if isinstance(s, bool) or not isinstance(s, int | float):
                raise ProfileError("sensitivity must be a number")
            if not SENSITIVITY_MIN <= s <= SENSITIVITY_MAX:
                raise ProfileError(
                    f"sensitivity must be between {SENSITIVITY_MIN} and {SENSITIVITY_MAX}"
                )
            prof.sensitivity = float(s)
        return prof

    def get(self) -> dict[str, Any]:
        with self._lock:
            return self._profile.to_dict()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def is_enabled(self, alert_class: AlertClass) -> bool:
        with self._lock:
            return self._profile.enabled.get(alert_class.value, True)

    @property
    def sensitivity(self) -> float:
        with self._lock:
            return self._profile.sensitivity

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        """Merge a partial update. Raises ProfileError and changes nothing if it is invalid."""
        with self._lock:
            self._profile = self._validated(data, self._profile)
            self._version += 1
            snapshot = self._profile.to_dict()
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(snapshot, indent=2) + "\n")
        return snapshot
