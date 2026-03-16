"""State store — holds the latest known value for each SignalK path.

Tracks staleness so consumers can check if data is fresh enough.
Thread-safe for single-writer (ingest task) / multi-reader pattern.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config import Config
from models import InstantSample, SignalKUpdate
from paths import (
    ATTITUDE,
    COG_TRUE,
    CURRENT_DRIFT,
    CURRENT_SET_TRUE,
    DEPTH,
    HEADING_TRUE,
    MOTION_SEVERITY,
    POSITION,
    RATE_OF_TURN,
    RUDDER_ANGLE,
    SPEED_OVER_GROUND,
    SPEED_THROUGH_WATER,
    WAVE_HEIGHT,
    WAVE_PERIOD,
    WIND_ANGLE_APPARENT,
    WIND_ANGLE_TRUE_WATER,
    WIND_SPEED_APPARENT,
    WIND_SPEED_TRUE,
)

logger = logging.getLogger(__name__)


class StateStore:
    """Holds the latest SignalK values with staleness tracking."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._values: dict[str, Any] = {}
        self._timestamps: dict[str, float] = {}  # Unix epoch of last update
        self._update_count = 0

    @property
    def update_count(self) -> int:
        return self._update_count

    def apply(self, update: SignalKUpdate) -> None:
        """Apply a SignalK update to the store."""
        self._values[update.path] = update.value
        self._timestamps[update.path] = time.time()
        self._update_count += 1

        # Handle propulsion wildcard paths — store under canonical names
        if update.path.startswith("propulsion.") and update.path.endswith(".revolutions"):
            self._values["_propulsion_rpm"] = update.value
            self._timestamps["_propulsion_rpm"] = time.time()
        elif update.path.startswith("propulsion.") and update.path.endswith(".state"):
            self._values["_propulsion_state"] = update.value
            self._timestamps["_propulsion_state"] = time.time()

    def get(self, path: str) -> Any | None:
        """Get the latest value for a path, or None if not available."""
        return self._values.get(path)

    def is_fresh(self, path: str) -> bool:
        """Check if a path's value is fresh (within stale threshold)."""
        ts = self._timestamps.get(path)
        if ts is None:
            return False
        return (time.time() - ts) < self._config.stale_threshold_s

    def age(self, path: str) -> float | None:
        """Age of a path's value in seconds, or None if never received."""
        ts = self._timestamps.get(path)
        if ts is None:
            return None
        return time.time() - ts

    def snapshot(self) -> InstantSample:
        """Create an InstantSample from current state.

        Returns a snapshot with all available values. Missing values are None.
        """
        now = time.time()

        # Extract attitude components
        attitude = self._values.get(ATTITUDE)
        pitch = None
        roll = None
        if isinstance(attitude, dict):
            pitch = attitude.get("pitch")
            roll = attitude.get("roll")

        # Extract position components
        position = self._values.get(POSITION)
        lat = None
        lon = None
        if isinstance(position, dict):
            lat = position.get("latitude")
            lon = position.get("longitude")

        return InstantSample(
            timestamp=now,
            # Wind
            tws=self._fresh_val(WIND_SPEED_TRUE),
            twa=self._fresh_val(WIND_ANGLE_TRUE_WATER),
            aws=self._fresh_val(WIND_SPEED_APPARENT),
            awa=self._fresh_val(WIND_ANGLE_APPARENT),
            # Speed
            bsp=self._fresh_val(SPEED_THROUGH_WATER),
            sog=self._fresh_val(SPEED_OVER_GROUND),
            # Course/heading
            cog=self._fresh_val(COG_TRUE),
            heading=self._fresh_val(HEADING_TRUE),
            rot=self._fresh_val(RATE_OF_TURN),
            # Attitude
            pitch=pitch if self.is_fresh(ATTITUDE) else None,
            roll=roll if self.is_fresh(ATTITUDE) else None,
            # Position
            latitude=lat if self.is_fresh(POSITION) else None,
            longitude=lon if self.is_fresh(POSITION) else None,
            # Steering
            rudder_angle=self._fresh_val(RUDDER_ANGLE),
            # Environment
            current_drift=self._fresh_val(CURRENT_DRIFT),
            current_set=self._fresh_val(CURRENT_SET_TRUE),
            wave_height=self._fresh_val(WAVE_HEIGHT),
            wave_period=self._fresh_val(WAVE_PERIOD),
            motion_severity=self._fresh_val(MOTION_SEVERITY),
            depth=self._fresh_val(DEPTH),
            # Propulsion
            engine_rpm=self._fresh_val("_propulsion_rpm"),
            engine_state=self._fresh_val("_propulsion_state"),
        )

    def _fresh_val(self, path: str) -> Any | None:
        """Return value only if it's fresh enough."""
        if self.is_fresh(path):
            return self._values.get(path)
        return None
