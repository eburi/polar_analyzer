"""Sample filter — steady-state validation and propulsion detection.

Implements a multi-layered filter pipeline:
1. Propulsion filter (motoring detection)
2. Steady-state filter (wind/speed stability, tack detection)

Only samples that pass ALL filters are emitted as ValidSamples.
"""

from __future__ import annotations

import collections
import logging
import math

import numpy as np

from config import Config
from models import (
    DEG_TO_RAD,
    RAD_TO_DEG,
    FilterRejectReason,
    FilterResult,
    InstantSample,
    PropulsionMode,
    PropulsionOverride,
    SeaState,
    ValidSample,
)

logger = logging.getLogger(__name__)


class SampleFilter:
    """Validates samples for steady-state sailing conditions."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._propulsion_override = PropulsionOverride.AUTO

        # Rolling window buffers (deques of (timestamp, value))
        window = int(config.filter_window_s)
        self._tws_history: collections.deque[tuple[float, float]] = collections.deque(
            maxlen=window * 2
        )
        self._twd_history: collections.deque[tuple[float, float]] = collections.deque(
            maxlen=window * 2
        )
        self._bsp_history: collections.deque[tuple[float, float]] = collections.deque(
            maxlen=window * 2
        )
        self._heading_history: collections.deque[tuple[float, float]] = collections.deque(
            maxlen=window * 2
        )
        self._sog_history: collections.deque[tuple[float, float]] = collections.deque(
            maxlen=window * 2
        )

        # Tack/gybe detection
        self._last_tack_time: float = 0.0

        # Engine state tracking
        self._engine_rpm_available = False

    @property
    def propulsion_override(self) -> PropulsionOverride:
        return self._propulsion_override

    @propulsion_override.setter
    def propulsion_override(self, value: PropulsionOverride) -> None:
        self._propulsion_override = value
        logger.info("Propulsion override set to: %s", value.value)

    def process(self, sample: InstantSample) -> FilterResult:
        """Run all filters on a sample. Returns FilterResult."""
        reject_reasons: list[FilterRejectReason] = []
        now = sample.timestamp

        # Update rolling histories
        self._update_histories(sample)

        # --- Check minimum required data ---
        if not sample.has_required_fields():
            return FilterResult(
                passed=False,
                reject_reasons=[FilterRejectReason.STALE_DATA],
            )

        # --- Layer 0: Propulsion filter ---
        propulsion_mode = self._detect_propulsion(sample)

        if propulsion_mode == PropulsionMode.MOTORING:
            return FilterResult(
                passed=False,
                propulsion_mode=propulsion_mode,
                reject_reasons=[FilterRejectReason.MOTORING],
            )

        # --- Minimum thresholds ---
        assert sample.tws is not None  # guaranteed by has_required_fields
        assert sample.twa is not None
        assert sample.bsp is not None

        if sample.tws < self._config.min_tws_ms:
            reject_reasons.append(FilterRejectReason.TWS_TOO_LOW)

        if sample.bsp < self._config.min_bsp_ms:
            reject_reasons.append(FilterRejectReason.BSP_TOO_LOW)

        # --- Rolling window checks (need enough history) ---
        window_s = self._config.filter_window_s
        tws_vals = self._window_values(self._tws_history, now, window_s)
        bsp_vals = self._window_values(self._bsp_history, now, window_s)
        twd_vals = self._window_values(self._twd_history, now, window_s)
        heading_vals = self._window_values(self._heading_history, now, window_s)

        min_points = int(window_s * 0.5)  # Need at least half the window filled
        if len(tws_vals) < min_points or len(bsp_vals) < min_points:
            reject_reasons.append(FilterRejectReason.INSUFFICIENT_DATA)
        else:
            # TWS stability
            tws_arr = np.array(tws_vals)
            tws_mean = np.mean(tws_arr)
            if tws_mean > 0:
                tws_cv = float(np.std(tws_arr) / tws_mean)
                if tws_cv > self._config.tws_cv_max:
                    reject_reasons.append(FilterRejectReason.TWS_UNSTABLE)

            # BSP stability
            bsp_arr = np.array(bsp_vals)
            bsp_mean = np.mean(bsp_arr)
            if bsp_mean > 0:
                bsp_cv = float(np.std(bsp_arr) / bsp_mean)
                if bsp_cv > self._config.bsp_cv_max:
                    reject_reasons.append(FilterRejectReason.BSP_UNSTABLE)

            # TWD stability (circular std dev)
            if len(twd_vals) >= min_points:
                twd_std = self._circular_std(twd_vals)
                if twd_std > self._config.twd_std_max_deg * DEG_TO_RAD:
                    reject_reasons.append(FilterRejectReason.TWD_UNSTABLE)

        # --- Rate of turn check ---
        if sample.rot is not None:
            rot_deg_s = abs(sample.rot) * RAD_TO_DEG
            if rot_deg_s > self._config.rot_max_deg_s:
                reject_reasons.append(FilterRejectReason.TURNING)

        # --- Tack/gybe detection ---
        if len(heading_vals) >= 2 and self._detect_tack(heading_vals, now):
            self._last_tack_time = now

        if (now - self._last_tack_time) < self._config.tack_exclusion_s:
            reject_reasons.append(FilterRejectReason.TACK_GYBE)

        # --- Result ---
        passed = len(reject_reasons) == 0

        valid_sample = None
        if passed:
            sea_state = self._classify_sea_state(sample.wave_height)
            valid_sample = ValidSample(
                timestamp=sample.timestamp,
                tws=sample.tws,
                twa_abs=abs(sample.twa),
                bsp=sample.bsp,
                sea_state=sea_state,
                sog=sample.sog,
                current_drift=sample.current_drift,
                wave_height=sample.wave_height,
                latitude=sample.latitude,
                longitude=sample.longitude,
            )

        return FilterResult(
            passed=passed,
            propulsion_mode=propulsion_mode,
            reject_reasons=reject_reasons,
            valid_sample=valid_sample,
        )

    def _detect_propulsion(self, sample: InstantSample) -> PropulsionMode:
        """Multi-layered propulsion detection.

        Layer 1: Direct engine data (if available)
        Layer 2: Heuristic detection (BSP/TWS decorrelation)
        Layer 3: Manual override
        """
        # --- Layer 3: Manual override takes precedence ---
        if self._propulsion_override == PropulsionOverride.SAILING:
            return PropulsionMode.SAILING
        if self._propulsion_override == PropulsionOverride.MOTORING:
            return PropulsionMode.MOTORING

        # --- Layer 1: Direct engine data ---
        if sample.engine_rpm is not None:
            self._engine_rpm_available = True
            engine_running = sample.engine_rpm > 0
            engine_state_running = (
                sample.engine_state is not None and sample.engine_state != "stopped"
            )

            if engine_running or engine_state_running:
                # Engine is running — but is the propeller engaged?
                # Battery charging exception: engine RPM > 0 but sailing correlation intact
                if self._is_sailing_with_engine(sample):
                    logger.debug(
                        "Engine running (RPM=%.1f) but sailing correlation detected "
                        "— likely battery charging, treating as SAILING",
                        sample.engine_rpm or 0,
                    )
                    return PropulsionMode.SAILING
                return PropulsionMode.MOTORING
            return PropulsionMode.SAILING

        # --- Layer 2: Heuristic detection ---
        return self._heuristic_propulsion_check(sample)

    def _is_sailing_with_engine(self, sample: InstantSample) -> bool:
        """Detect battery-charging scenario: engine on but under sail.

        When the engine runs in neutral for charging, BSP still correlates
        with TWS and there's no SOG/STW divergence from motor thrust.
        """
        now = sample.timestamp
        window_s = self._config.motoring_detection_window_s

        bsp_vals = self._window_values(self._bsp_history, now, window_s)
        tws_vals = self._window_values(self._tws_history, now, window_s)

        if len(bsp_vals) < 10 or len(tws_vals) < 10:
            return False  # Not enough data — can't confirm sailing

        # Check BSP/TWS correlation — under sail they move together
        bsp_arr = (
            np.array(bsp_vals[-len(tws_vals) :])
            if len(bsp_vals) > len(tws_vals)
            else np.array(bsp_vals)
        )
        tws_arr = (
            np.array(tws_vals[-len(bsp_vals) :])
            if len(tws_vals) > len(bsp_vals)
            else np.array(tws_vals)
        )

        # Align lengths
        min_len = min(len(bsp_arr), len(tws_arr))
        bsp_arr = bsp_arr[-min_len:]
        tws_arr = tws_arr[-min_len:]

        if np.std(tws_arr) < 0.1:
            return False  # Can't determine correlation with constant wind

        # Correlation coefficient
        try:
            corr = float(np.corrcoef(bsp_arr, tws_arr)[0, 1])
        except (ValueError, FloatingPointError):
            return False

        # High correlation (>0.5) suggests sailing, not motoring
        if corr > 0.5 and sample.bsp is not None and sample.sog is not None:
            # Additional check: STW/SOG should not diverge abnormally
            speed_diff = abs(sample.bsp - sample.sog)
            # If STW and SOG are close (accounting for current),
            # no motor thrust detected
            current = sample.current_drift or 0.0
            if speed_diff < (current + 0.5):  # 0.5 m/s tolerance
                return True

        return False

    def _heuristic_propulsion_check(self, sample: InstantSample) -> PropulsionMode:
        """Layer 2: Detect motoring from BSP/TWS decorrelation.

        Under sail, BSP tracks TWS. Under motor, BSP is independent.
        Key heuristic: constant BSP in variable TWS → motoring.
        """
        now = sample.timestamp
        window_s = self._config.motoring_detection_window_s

        bsp_vals = self._window_values(self._bsp_history, now, window_s)
        tws_vals = self._window_values(self._tws_history, now, window_s)

        if len(bsp_vals) < 10 or len(tws_vals) < 10:
            return PropulsionMode.UNKNOWN

        bsp_arr = np.array(bsp_vals)
        tws_arr = np.array(tws_vals)

        bsp_mean = np.mean(bsp_arr)
        tws_mean = np.mean(tws_arr)

        if bsp_mean < 0.1 or tws_mean < 0.1:
            return PropulsionMode.UNKNOWN

        bsp_cv = float(np.std(bsp_arr) / bsp_mean)
        tws_cv = float(np.std(tws_arr) / tws_mean)

        # Constant BSP in variable TWS → motoring
        if bsp_cv < self._config.motoring_bsp_cv_max and tws_cv > self._config.motoring_tws_cv_min:
            logger.debug(
                "Heuristic motoring detection: BSP CV=%.3f (<%s), TWS CV=%.3f (>%s)",
                bsp_cv,
                self._config.motoring_bsp_cv_max,
                tws_cv,
                self._config.motoring_tws_cv_min,
            )
            return PropulsionMode.MOTORING

        return PropulsionMode.SAILING

    def _detect_tack(self, heading_vals: list[float], now: float) -> bool:
        """Detect tack/gybe from heading change within detection window."""
        window_s = self._config.tack_detection_window_s
        recent = [v for t, v in self._heading_history if (now - t) < window_s]

        if len(recent) < 2:
            return False

        # Compute heading change (handle wraparound)
        heading_change = self._angle_diff(recent[0], recent[-1])
        threshold = self._config.tack_heading_change_deg * DEG_TO_RAD

        return abs(heading_change) > threshold

    def _update_histories(self, sample: InstantSample) -> None:
        """Add sample values to rolling histories."""
        now = sample.timestamp

        if sample.tws is not None:
            self._tws_history.append((now, sample.tws))
        if sample.bsp is not None:
            self._bsp_history.append((now, sample.bsp))
        if sample.heading is not None:
            self._heading_history.append((now, sample.heading))
        if sample.sog is not None:
            self._sog_history.append((now, sample.sog))

        # Compute true wind direction from heading + TWA for TWD history
        if sample.heading is not None and sample.twa is not None:
            twd = (sample.heading + sample.twa) % (2 * math.pi)
            self._twd_history.append((now, twd))

    def _window_values(
        self,
        history: collections.deque[tuple[float, float]],
        now: float,
        window_s: float,
    ) -> list[float]:
        """Extract values within the time window."""
        cutoff = now - window_s
        return [v for t, v in history if t >= cutoff]

    @staticmethod
    def _circular_std(angles_rad: list[float]) -> float:
        """Circular standard deviation for angles in radians."""
        if not angles_rad:
            return 0.0
        sins = [math.sin(a) for a in angles_rad]
        coss = [math.cos(a) for a in angles_rad]
        mean_sin = sum(sins) / len(sins)
        mean_cos = sum(coss) / len(coss)
        r = math.sqrt(mean_sin**2 + mean_cos**2)
        # Circular std dev: sqrt(-2 * ln(R))
        if r < 1e-10:
            return math.pi  # Maximum dispersion
        return math.sqrt(-2.0 * math.log(min(r, 1.0)))

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        """Signed angular difference b - a, handling wraparound."""
        diff = b - a
        while diff > math.pi:
            diff -= 2 * math.pi
        while diff < -math.pi:
            diff += 2 * math.pi
        return diff

    def _classify_sea_state(self, wave_height: float | None) -> SeaState:
        """Classify sea state from significant wave height."""
        if wave_height is None:
            return SeaState.UNKNOWN
        if wave_height < self._config.sea_state_flat_max_m:
            return SeaState.FLAT
        if wave_height < self._config.sea_state_moderate_max_m:
            return SeaState.MODERATE
        return SeaState.ROUGH
