"""Tests for PerformanceCalc — target BSP, VMG, optimal angles."""

from __future__ import annotations

import math

import numpy as np
import pytest

from config import Config
from models import InstantSample, PerformanceMetrics, SeaState, ValidSample
from performance_calc import PerformanceCalc
from polar_engine import PolarEngine

KT_TO_MS = 1 / 1.94384
DEG_TO_RAD = math.pi / 180.0
MS_TO_KT = 1.94384


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def engine(config: Config) -> PolarEngine:
    return PolarEngine(config)


@pytest.fixture
def perf(config: Config, engine: PolarEngine) -> PerformanceCalc:
    return PerformanceCalc(config, engine)


def fill_realistic_polar(engine: PolarEngine):
    """Fill the engine with realistic polar data for a cruising catamaran."""
    rng = np.random.RandomState(42)

    for tws_kt in [6, 8, 10, 12, 14, 16]:
        for twa_deg in range(30, 185, 5):
            tws_ms = tws_kt * KT_TO_MS
            twa_rad = twa_deg * DEG_TO_RAD

            # Catamaran polar shape:
            # - Low BSP close-hauled (30-50)
            # - Peak at broad reach (90-130)
            # - Slight decrease running (150-180)
            if twa_deg < 50:
                bsp_factor = 0.5 + (twa_deg - 30) / 60.0
            elif twa_deg < 130:
                bsp_factor = 0.8 + (twa_deg - 50) / 400.0
            else:
                bsp_factor = 1.0 - (twa_deg - 130) / 250.0

            base_bsp = tws_kt * bsp_factor * KT_TO_MS

            for _ in range(25):
                bsp = base_bsp + rng.uniform(-0.2, 0.2) * KT_TO_MS
                sample = ValidSample(
                    timestamp=1000.0,
                    tws=tws_ms,
                    twa_abs=twa_rad,
                    bsp=max(bsp, 0.1),
                    sea_state=SeaState.UNKNOWN,
                )
                engine.add_sample(sample)

    engine.recompute()


class TestPerformanceCalcBasics:
    def test_returns_none_without_data(self, perf: PerformanceCalc):
        sample = InstantSample(timestamp=1000.0)
        result = perf.compute(sample)
        assert result is None

    def test_returns_none_missing_instruments(self, perf: PerformanceCalc):
        sample = InstantSample(timestamp=1000.0, tws=5.0)
        result = perf.compute(sample)
        assert result is None

    def test_returns_metrics_with_polar_data(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=90.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert isinstance(metrics, PerformanceMetrics)

    def test_returns_metrics_without_polar(self, perf: PerformanceCalc):
        """Should still compute VMG even without polar data."""
        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=45.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.vmg is not None
        # No polar data means no polar speed
        assert metrics.polar_speed is None


class TestPolarSpeed:
    def test_polar_speed_lookup(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=90.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.polar_speed is not None
        assert metrics.polar_speed > 0

    def test_performance_ratio(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        # Create a sample at exactly target speed
        target_bsp = engine.interpolate_bsp(10, 90)
        assert target_bsp is not None

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=90.0 * DEG_TO_RAD,
            bsp=target_bsp,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.polar_speed_ratio is not None
        # At exactly target speed, ratio should be ~1.0
        assert abs(metrics.polar_speed_ratio - 1.0) < 0.05

    def test_over_performing(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        target_bsp = engine.interpolate_bsp(10, 90)
        assert target_bsp is not None

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=90.0 * DEG_TO_RAD,
            bsp=target_bsp * 1.2,  # 20% over target
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.polar_speed_ratio > 1.1


class TestVMG:
    def test_vmg_upwind(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=45.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.vmg is not None
        # Upwind VMG should be positive (toward wind)
        assert metrics.vmg > 0

    def test_vmg_beam_reach(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=90.0 * DEG_TO_RAD,
            bsp=6.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        # VMG at 90 degrees is ~0 (cos(90) = 0)
        assert abs(metrics.vmg) < 0.1

    def test_vmg_downwind(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=150.0 * DEG_TO_RAD,
            bsp=4.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.vmg is not None
        # Downwind VMG should be negative (away from wind)
        assert metrics.vmg < 0

    def test_vmg_formula(self, perf: PerformanceCalc, engine: PolarEngine):
        """VMG = BSP * cos(TWA)"""
        fill_realistic_polar(engine)

        bsp_ms = 5.0 * KT_TO_MS
        twa_rad = 60.0 * DEG_TO_RAD
        expected_vmg = bsp_ms * math.cos(twa_rad)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=twa_rad,
            bsp=bsp_ms,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert abs(metrics.vmg - expected_vmg) < 0.001


class TestOptimalAngles:
    def test_beat_angle_exists(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=45.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.beat_angle is not None
        # Beat angle for a catamaran is typically 35-55 degrees
        beat_deg = metrics.beat_angle * 180 / math.pi
        assert 30 <= beat_deg <= 70

    def test_gybe_angle_exists(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=150.0 * DEG_TO_RAD,
            bsp=4.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        assert metrics.gybe_angle is not None
        # Gybe angle typically 130-170 degrees
        gybe_deg = metrics.gybe_angle * 180 / math.pi
        assert 110 <= gybe_deg <= 180

    def test_target_angle_upwind(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=45.0 * DEG_TO_RAD,  # Upwind
            bsp=5.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        # When upwind, target should be beat angle
        assert metrics.target_angle == metrics.beat_angle

    def test_target_angle_downwind(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=150.0 * DEG_TO_RAD,  # Downwind
            bsp=4.0 * KT_TO_MS,
        )
        metrics = perf.compute(sample)
        assert metrics is not None
        # When downwind, target should be gybe angle
        assert metrics.target_angle == metrics.gybe_angle

    def test_cache_refreshes_on_version_change(self, perf: PerformanceCalc, engine: PolarEngine):
        fill_realistic_polar(engine)

        sample = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=45.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )
        metrics1 = perf.compute(sample)
        assert metrics1 is not None

        # Bump version
        engine.master.version += 1

        metrics2 = perf.compute(sample)
        assert metrics2 is not None
        # Cache should have been refreshed (test exercises the code path)


class TestNearestTWSBin:
    def test_exact_match(self, perf: PerformanceCalc):
        assert perf._nearest_tws_bin(10.0) == 10.0

    def test_closest_bin(self, perf: PerformanceCalc):
        assert perf._nearest_tws_bin(11.0) == 10.0  # Closer to 10 than 12

    def test_midpoint(self, perf: PerformanceCalc):
        # 11 is equidistant from 10 and 12, min() picks first
        result = perf._nearest_tws_bin(11.0)
        assert result in [10.0, 12.0]


class TestPortStarboardSymmetry:
    def test_negative_twa_uses_abs(self, perf: PerformanceCalc, engine: PolarEngine):
        """Negative TWA (port tack) should use absolute value for lookup."""
        fill_realistic_polar(engine)

        # Port tack (negative TWA)
        sample_port = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=-90.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )
        # Starboard tack (positive TWA)
        sample_stbd = InstantSample(
            timestamp=1000.0,
            tws=10.0 * KT_TO_MS,
            twa=90.0 * DEG_TO_RAD,
            bsp=5.0 * KT_TO_MS,
        )

        metrics_port = perf.compute(sample_port)
        metrics_stbd = perf.compute(sample_stbd)

        assert metrics_port is not None
        assert metrics_stbd is not None
        # Same polar speed on both tacks
        assert metrics_port.polar_speed == metrics_stbd.polar_speed
        # VMG magnitude same, sign same (cos is even function applied to abs)
        assert abs(metrics_port.vmg) == abs(metrics_stbd.vmg)
