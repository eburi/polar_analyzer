"""Tests for SampleFilter — propulsion detection and steady-state validation."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from config import Config
from models import (
    FilterRejectReason,
    InstantSample,
    PropulsionMode,
    PropulsionOverride,
    SeaState,
)
from sample_filter import SampleFilter


DEG_TO_RAD = math.pi / 180.0
KT_TO_MS = 1 / 1.94384


@pytest.fixture
def config() -> Config:
    cfg = Config()
    # Shorter windows for faster tests
    cfg.filter_window_s = 10.0
    cfg.motoring_detection_window_s = 10.0
    cfg.tack_detection_window_s = 5.0
    cfg.tack_exclusion_s = 10.0
    return cfg


@pytest.fixture
def filt(config: Config) -> SampleFilter:
    return SampleFilter(config)


def make_sample(
    ts: float,
    tws_ms: float = 5.0,
    twa_rad: float = 1.5,
    bsp_ms: float = 3.0,
    heading: float = 0.0,
    sog: float | None = None,
    rot: float | None = None,
    engine_rpm: float | None = None,
    engine_state: str | None = None,
    wave_height: float | None = None,
) -> InstantSample:
    return InstantSample(
        timestamp=ts,
        tws=tws_ms,
        twa=twa_rad,
        bsp=bsp_ms,
        heading=heading,
        sog=sog or bsp_ms,
        rot=rot,
        engine_rpm=engine_rpm,
        engine_state=engine_state,
        wave_height=wave_height,
    )


class TestFilterBasics:
    def test_rejects_missing_fields(self, filt: SampleFilter):
        sample = InstantSample(timestamp=1000.0)
        result = filt.process(sample)
        assert not result.passed
        assert FilterRejectReason.STALE_DATA in result.reject_reasons

    def test_rejects_low_tws(self, filt: SampleFilter):
        """TWS below 3 knots (1.54 m/s) should be rejected."""
        # Feed enough history first
        for i in range(15):
            s = make_sample(1000.0 + i, tws_ms=1.0, bsp_ms=0.5)
            filt.process(s)

        result = filt.process(make_sample(1015.0, tws_ms=1.0, bsp_ms=0.5))
        assert not result.passed
        assert FilterRejectReason.TWS_TOO_LOW in result.reject_reasons

    def test_rejects_low_bsp(self, filt: SampleFilter):
        """BSP below 0.5 knots (0.26 m/s) should be rejected."""
        for i in range(15):
            s = make_sample(1000.0 + i, tws_ms=5.0, bsp_ms=0.1)
            filt.process(s)

        result = filt.process(make_sample(1015.0, tws_ms=5.0, bsp_ms=0.1))
        assert not result.passed
        assert FilterRejectReason.BSP_TOO_LOW in result.reject_reasons

    def test_passes_steady_state(self, filt: SampleFilter):
        """Steady conditions should pass all filters."""
        # Feed consistent history
        for i in range(15):
            s = make_sample(
                1000.0 + i,
                tws_ms=5.0,
                twa_rad=1.5,
                bsp_ms=3.0,
                heading=0.5,
            )
            filt.process(s)

        result = filt.process(make_sample(
            1015.0, tws_ms=5.0, twa_rad=1.5, bsp_ms=3.0, heading=0.5,
        ))
        assert result.passed
        assert result.valid_sample is not None

    def test_valid_sample_has_correct_fields(self, filt: SampleFilter):
        for i in range(15):
            s = make_sample(
                1000.0 + i,
                tws_ms=5.0,
                twa_rad=-1.5,  # Port tack (negative)
                bsp_ms=3.0,
                heading=0.5,
                wave_height=0.3,
            )
            filt.process(s)

        result = filt.process(make_sample(
            1015.0, tws_ms=5.0, twa_rad=-1.5, bsp_ms=3.0, heading=0.5, wave_height=0.3,
        ))
        assert result.passed
        vs = result.valid_sample
        assert vs is not None
        assert vs.tws == 5.0
        assert vs.twa_abs == abs(-1.5)  # Absolute value
        assert vs.bsp == 3.0
        assert vs.sea_state == SeaState.FLAT  # 0.3m < 0.5m


class TestTWSInstability:
    def test_rejects_unstable_tws(self, filt: SampleFilter, config: Config):
        """Variable TWS (CV > 15%) should be rejected.

        BSP must also vary (CV > 5%) so the heuristic motoring filter
        does not fire first (constant BSP + variable TWS = motoring).
        """
        rng = np.random.RandomState(42)
        for i in range(15):
            tws = 5.0 + rng.uniform(-3.0, 3.0)  # High variance (CV > 15%)
            bsp = 3.0 + rng.uniform(-0.5, 0.5)   # Moderate variance (CV > 5%)
            s = make_sample(1000.0 + i, tws_ms=max(tws, 2.0), bsp_ms=bsp, heading=0.5)
            filt.process(s)

        result = filt.process(make_sample(1015.0, tws_ms=7.0, bsp_ms=3.2, heading=0.5))
        assert not result.passed
        assert FilterRejectReason.TWS_UNSTABLE in result.reject_reasons


class TestBSPInstability:
    def test_rejects_unstable_bsp(self, filt: SampleFilter):
        """Variable BSP (CV > 15%) should be rejected."""
        rng = np.random.RandomState(42)
        for i in range(15):
            bsp = 3.0 + rng.uniform(-2.0, 2.0)  # High variance
            s = make_sample(1000.0 + i, tws_ms=5.0, bsp_ms=max(bsp, 0.5), heading=0.5)
            filt.process(s)

        result = filt.process(make_sample(1015.0, tws_ms=5.0, bsp_ms=4.0, heading=0.5))
        assert not result.passed
        assert FilterRejectReason.BSP_UNSTABLE in result.reject_reasons


class TestRateOfTurn:
    def test_rejects_high_rot(self, filt: SampleFilter):
        """Rate of turn > 2 deg/s should be rejected."""
        for i in range(15):
            s = make_sample(1000.0 + i, rot=0.0, heading=0.5)
            filt.process(s)

        # 5 deg/s = 0.087 rad/s
        result = filt.process(make_sample(1015.0, rot=0.087, heading=0.5))
        assert not result.passed
        assert FilterRejectReason.TURNING in result.reject_reasons


class TestTackDetection:
    def test_detects_tack(self, filt: SampleFilter):
        """Large heading change within detection window triggers tack exclusion."""
        # Build history with gradual heading, then sudden change
        for i in range(10):
            s = make_sample(1000.0 + i, heading=0.5)
            filt.process(s)

        # Sudden heading change > 25 degrees (0.436 rad)
        for i in range(5):
            s = make_sample(1010.0 + i, heading=1.5)  # ~57 degree change
            filt.process(s)

        result = filt.process(make_sample(1015.0, heading=1.5))
        assert not result.passed
        assert FilterRejectReason.TACK_GYBE in result.reject_reasons


class TestPropulsionOverride:
    def test_sailing_override(self, filt: SampleFilter):
        filt.propulsion_override = PropulsionOverride.SAILING
        for i in range(15):
            filt.process(make_sample(1000.0 + i, heading=0.5))

        result = filt.process(make_sample(1015.0, heading=0.5))
        assert result.propulsion_mode == PropulsionMode.SAILING

    def test_motoring_override(self, filt: SampleFilter):
        filt.propulsion_override = PropulsionOverride.MOTORING
        result = filt.process(make_sample(1000.0))
        assert not result.passed
        assert result.propulsion_mode == PropulsionMode.MOTORING
        assert FilterRejectReason.MOTORING in result.reject_reasons


class TestDirectEngineDetection:
    def test_engine_rpm_positive_is_motoring(self, filt: SampleFilter):
        """Direct engine RPM > 0 should detect motoring (when no sailing correlation)."""
        # Fill history with uncorrelated BSP/TWS (so _is_sailing_with_engine returns False)
        for i in range(15):
            s = make_sample(
                1000.0 + i,
                tws_ms=5.0 + i * 0.3,  # varying TWS
                bsp_ms=3.0,  # constant BSP (uncorrelated)
                heading=0.5,
                engine_rpm=2.0,
            )
            filt.process(s)

        result = filt.process(make_sample(1015.0, engine_rpm=2.0, heading=0.5))
        assert not result.passed
        assert result.propulsion_mode == PropulsionMode.MOTORING

    def test_engine_stopped_is_sailing(self, filt: SampleFilter):
        """Engine RPM = 0 should be detected as sailing."""
        for i in range(15):
            s = make_sample(1000.0 + i, engine_rpm=0.0, heading=0.5)
            filt.process(s)

        result = filt.process(make_sample(1015.0, engine_rpm=0.0, heading=0.5))
        assert result.propulsion_mode == PropulsionMode.SAILING


class TestHeuristicPropulsion:
    def test_constant_bsp_variable_tws_is_motoring(self, filt: SampleFilter):
        """Constant BSP in variable TWS → heuristic motoring detection."""
        rng = np.random.RandomState(42)
        for i in range(15):
            # Constant BSP (cv < 5%)
            bsp = 3.0 + rng.uniform(-0.05, 0.05)
            # Variable TWS (cv > 20%)
            tws = 5.0 + rng.uniform(-2.0, 2.0)
            s = make_sample(1000.0 + i, tws_ms=max(tws, 2.0), bsp_ms=bsp, heading=0.5)
            filt.process(s)

        result = filt.process(make_sample(1015.0, tws_ms=5.0, bsp_ms=3.0, heading=0.5))
        assert result.propulsion_mode == PropulsionMode.MOTORING

    def test_correlated_bsp_tws_is_sailing(self, filt: SampleFilter):
        """BSP that tracks TWS → sailing."""
        for i in range(15):
            tws = 5.0 + i * 0.1
            bsp = 3.0 + i * 0.05  # BSP tracks TWS proportionally
            s = make_sample(1000.0 + i, tws_ms=tws, bsp_ms=bsp, heading=0.5)
            filt.process(s)

        result = filt.process(make_sample(1015.0, tws_ms=6.5, bsp_ms=3.75, heading=0.5))
        assert result.propulsion_mode == PropulsionMode.SAILING


class TestSeaStateClassification:
    def test_flat(self, filt: SampleFilter):
        for i in range(15):
            filt.process(make_sample(1000.0 + i, wave_height=0.3, heading=0.5))

        result = filt.process(make_sample(1015.0, wave_height=0.3, heading=0.5))
        if result.passed:
            assert result.valid_sample.sea_state == SeaState.FLAT

    def test_moderate(self, filt: SampleFilter):
        for i in range(15):
            filt.process(make_sample(1000.0 + i, wave_height=1.0, heading=0.5))

        result = filt.process(make_sample(1015.0, wave_height=1.0, heading=0.5))
        if result.passed:
            assert result.valid_sample.sea_state == SeaState.MODERATE

    def test_rough(self, filt: SampleFilter):
        for i in range(15):
            filt.process(make_sample(1000.0 + i, wave_height=2.0, heading=0.5))

        result = filt.process(make_sample(1015.0, wave_height=2.0, heading=0.5))
        if result.passed:
            assert result.valid_sample.sea_state == SeaState.ROUGH

    def test_unknown_when_no_wave_data(self, filt: SampleFilter):
        for i in range(15):
            filt.process(make_sample(1000.0 + i, heading=0.5))

        result = filt.process(make_sample(1015.0, heading=0.5))
        if result.passed:
            assert result.valid_sample.sea_state == SeaState.UNKNOWN


class TestCircularStdDev:
    def test_zero_dispersion(self):
        angles = [1.0, 1.0, 1.0, 1.0]
        std = SampleFilter._circular_std(angles)
        assert std < 0.01

    def test_high_dispersion(self):
        # Angles spread around the circle
        angles = [0, math.pi / 2, math.pi, 3 * math.pi / 2]
        std = SampleFilter._circular_std(angles)
        assert std > 1.0

    def test_empty_returns_zero(self):
        assert SampleFilter._circular_std([]) == 0.0


class TestAngleDiff:
    def test_simple_diff(self):
        diff = SampleFilter._angle_diff(0.1, 0.3)
        assert abs(diff - 0.2) < 1e-10

    def test_wraparound(self):
        # Going from 350 to 10 degrees
        a = 350 * DEG_TO_RAD
        b = 10 * DEG_TO_RAD
        diff = SampleFilter._angle_diff(a, b)
        assert abs(diff - 20 * DEG_TO_RAD) < 0.01
