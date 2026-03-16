"""Performance calculator — real-time target BSP, VMG, performance ratio.

Computes performance metrics from the current polar table and live
instrument data. Updates at 1Hz, results are published to SignalK.
"""

from __future__ import annotations

import math
import logging

from config import Config
from models import (
    DEG_TO_RAD,
    InstantSample,
    KT_TO_MS,
    MS_TO_KT,
    PerformanceMetrics,
    RAD_TO_DEG,
)
from polar_engine import PolarEngine

logger = logging.getLogger(__name__)


class PerformanceCalc:
    """Computes real-time performance metrics from polar + live data."""

    def __init__(self, config: Config, engine: PolarEngine) -> None:
        self._config = config
        self._engine = engine
        # Cache optimal angles per TWS (recomputed when polar updates)
        self._optimal_cache: dict[float, dict] = {}
        self._cache_version: int = -1

    def compute(self, sample: InstantSample) -> PerformanceMetrics | None:
        """Compute performance metrics for the current instant.

        Returns None if insufficient data (no polar or no instruments).
        """
        if sample.tws is None or sample.twa is None or sample.bsp is None:
            return None

        tws_kt = sample.tws * MS_TO_KT
        twa_deg = abs(sample.twa) * RAD_TO_DEG
        bsp_ms = sample.bsp

        # Refresh optimal angle cache if polar version changed
        master = self._engine.master
        if master.version != self._cache_version:
            self._recompute_optimal_angles()
            self._cache_version = master.version

        metrics = PerformanceMetrics(timestamp=sample.timestamp)

        # --- Target BSP from polar ---
        polar_bsp = self._engine.interpolate_bsp(tws_kt, twa_deg)
        if polar_bsp is not None:
            metrics.polar_speed = polar_bsp
            if polar_bsp > 0:
                metrics.polar_speed_ratio = bsp_ms / polar_bsp

        # --- VMG to wind ---
        # VMG = BSP * cos(TWA)  (positive = toward wind)
        twa_rad = abs(sample.twa)
        metrics.vmg = bsp_ms * math.cos(twa_rad)

        # --- Optimal angles from cache ---
        tws_bin = self._nearest_tws_bin(tws_kt)
        optimal = self._optimal_cache.get(tws_bin)

        if optimal is not None:
            # Upwind
            if optimal.get("beat_angle_rad") is not None:
                metrics.beat_angle = optimal["beat_angle_rad"]
                metrics.beat_angle_vmg = optimal["beat_vmg_ms"]
                metrics.beat_angle_target_speed = optimal["beat_bsp_ms"]

            # Downwind
            if optimal.get("gybe_angle_rad") is not None:
                metrics.gybe_angle = optimal["gybe_angle_rad"]
                metrics.gybe_angle_vmg = optimal["gybe_vmg_ms"]
                metrics.gybe_angle_target_speed = optimal["gybe_bsp_ms"]

            # Current applicable target
            if twa_deg < 90:
                # Upwind — target is beat angle
                metrics.target_angle = optimal.get("beat_angle_rad")
                metrics.target_speed = optimal.get("beat_bsp_ms")
            else:
                # Downwind — target is gybe angle
                metrics.target_angle = optimal.get("gybe_angle_rad")
                metrics.target_speed = optimal.get("gybe_bsp_ms")

        return metrics

    def _recompute_optimal_angles(self) -> None:
        """Recompute optimal beat/gybe angles for each TWS bin."""
        self._optimal_cache.clear()
        master = self._engine.master

        for tws_kt in master.tws_bins_kt:
            curve = self._engine.get_polar_curve(tws_kt)
            if len(curve) < 3:
                continue

            best_upwind_vmg = 0.0
            best_upwind_angle = None
            best_upwind_bsp = None

            best_downwind_vmg = 0.0
            best_downwind_angle = None
            best_downwind_bsp = None

            for twa_deg, bsp_kt in curve:
                twa_rad = twa_deg * DEG_TO_RAD
                bsp_ms = bsp_kt * KT_TO_MS
                vmg = bsp_ms * math.cos(twa_rad)

                # Upwind VMG (TWA < 90°, VMG positive = toward wind)
                if twa_deg < 90 and vmg > best_upwind_vmg:
                    best_upwind_vmg = vmg
                    best_upwind_angle = twa_rad
                    best_upwind_bsp = bsp_ms

                # Downwind VMG (TWA > 90°, VMG negative = away from wind)
                # We want the most negative VMG (fastest away from wind)
                if twa_deg > 90 and vmg < -best_downwind_vmg:
                    best_downwind_vmg = -vmg  # Store as positive magnitude
                    best_downwind_angle = twa_rad
                    best_downwind_bsp = bsp_ms

            entry: dict = {}
            if best_upwind_angle is not None:
                entry["beat_angle_rad"] = best_upwind_angle
                entry["beat_vmg_ms"] = best_upwind_vmg
                entry["beat_bsp_ms"] = best_upwind_bsp

            if best_downwind_angle is not None:
                entry["gybe_angle_rad"] = best_downwind_angle
                entry["gybe_vmg_ms"] = -best_downwind_vmg  # Negative = downwind
                entry["gybe_bsp_ms"] = best_downwind_bsp

            if entry:
                self._optimal_cache[tws_kt] = entry

        if self._optimal_cache:
            logger.info(
                "Recomputed optimal angles for %d TWS bins",
                len(self._optimal_cache),
            )

    def _nearest_tws_bin(self, tws_kt: float) -> float:
        """Find nearest TWS bin center."""
        bins = self._engine.master.tws_bins_kt
        return min(bins, key=lambda b: abs(b - tws_kt))
