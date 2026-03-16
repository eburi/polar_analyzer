"""Sea state classifier — wave height to category mapping.

Simple classification based on significant wave height thresholds.
Used to stratify polar data by sea conditions.
"""

from __future__ import annotations

from config import Config
from models import SeaState


class SeaStateClassifier:
    """Classifies sea state from significant wave height."""

    def __init__(self, config: Config) -> None:
        self._flat_max = config.sea_state_flat_max_m
        self._moderate_max = config.sea_state_moderate_max_m

    def classify(self, wave_height_m: float | None) -> SeaState:
        """Classify sea state from significant wave height in meters."""
        if wave_height_m is None:
            return SeaState.UNKNOWN
        if wave_height_m < self._flat_max:
            return SeaState.FLAT
        if wave_height_m < self._moderate_max:
            return SeaState.MODERATE
        return SeaState.ROUGH

    @staticmethod
    def label(sea_state: SeaState) -> str:
        """Human-readable label for a sea state."""
        return {
            SeaState.FLAT: "Flat (< 0.5m)",
            SeaState.MODERATE: "Moderate (0.5-1.5m)",
            SeaState.ROUGH: "Rough (> 1.5m)",
            SeaState.UNKNOWN: "Unknown",
        }.get(sea_state, "Unknown")
