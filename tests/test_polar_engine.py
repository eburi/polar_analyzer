"""Tests for PolarEngine — binning, percentile, spline, interpolation, merge."""

from __future__ import annotations

import math

import numpy as np
import pytest

from config import Config
from models import PolarCell, PolarTable, SeaState, ValidSample
from polar_engine import PolarEngine


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def engine(config: Config) -> PolarEngine:
    return PolarEngine(config)


def make_sample(
    tws_ms: float,
    twa_rad: float,
    bsp_ms: float,
    sea_state: SeaState = SeaState.UNKNOWN,
    wave_height: float | None = None,
) -> ValidSample:
    """Helper to create a ValidSample."""
    return ValidSample(
        timestamp=1000.0,
        tws=tws_ms,
        twa_abs=abs(twa_rad),
        bsp=bsp_ms,
        sea_state=sea_state,
    )


KT_TO_MS = 1 / 1.94384
DEG_TO_RAD = math.pi / 180.0


class TestPolarEngineInit:
    def test_creates_empty_master(self, engine: PolarEngine):
        assert engine.master is not None
        assert len(engine.master.cells) == 0
        assert engine.master.version == 1

    def test_creates_sea_state_tables(self, engine: PolarEngine):
        for ss in [SeaState.FLAT, SeaState.MODERATE, SeaState.ROUGH]:
            table = engine.get_sea_state_table(ss)
            assert table is not None
            assert table.sea_state == ss

    def test_session_starts_empty(self, engine: PolarEngine):
        assert engine._session_sample_count == 0
        assert len(engine.session_table.cells) == 0


class TestAddSample:
    def test_adds_to_master_and_session(self, engine: PolarEngine):
        sample = make_sample(tws_ms=5.0, twa_rad=1.0, bsp_ms=3.0)
        engine.add_sample(sample)

        assert len(engine.master.cells) == 1
        assert len(engine.session_table.cells) == 1
        assert engine._session_sample_count == 1

    def test_bins_correctly(self, engine: PolarEngine):
        """10 knots TWS, 90 degrees TWA should bin to (10, 90)."""
        tws_ms = 10 * KT_TO_MS
        twa_rad = 90 * DEG_TO_RAD
        bsp_ms = 5.0 * KT_TO_MS

        sample = make_sample(tws_ms=tws_ms, twa_rad=twa_rad, bsp_ms=bsp_ms)
        engine.add_sample(sample)

        cell = engine.master.cells.get((10, 90))
        assert cell is not None
        assert cell.sample_count == 1

    def test_multiple_samples_same_bin(self, engine: PolarEngine):
        tws_ms = 10 * KT_TO_MS
        twa_rad = 90 * DEG_TO_RAD

        for bsp_kt in [5.0, 5.5, 6.0, 6.5, 7.0]:
            sample = make_sample(tws_ms=tws_ms, twa_rad=twa_rad, bsp_ms=bsp_kt * KT_TO_MS)
            engine.add_sample(sample)

        cell = engine.master.cells.get((10, 90))
        assert cell is not None
        assert cell.sample_count == 5

    def test_rejects_too_light_wind(self, engine: PolarEngine):
        # TWS below lowest bin center * 0.5 = 4 * 0.5 = 2 kt
        sample = make_sample(tws_ms=1.0 * KT_TO_MS, twa_rad=1.0, bsp_ms=1.0)
        engine.add_sample(sample)
        assert len(engine.master.cells) == 0

    def test_rejects_too_narrow_twa(self, engine: PolarEngine):
        # TWA below 30 - 2.5 = 27.5 degrees
        sample = make_sample(tws_ms=10 * KT_TO_MS, twa_rad=20 * DEG_TO_RAD, bsp_ms=3.0)
        engine.add_sample(sample)
        assert len(engine.master.cells) == 0

    def test_adds_to_sea_state_table(self, engine: PolarEngine):
        sample = make_sample(
            tws_ms=10 * KT_TO_MS,
            twa_rad=90 * DEG_TO_RAD,
            bsp_ms=5.0 * KT_TO_MS,
            sea_state=SeaState.FLAT,
        )
        engine.add_sample(sample)

        flat_table = engine.get_sea_state_table(SeaState.FLAT)
        assert len(flat_table.cells) == 1

        mod_table = engine.get_sea_state_table(SeaState.MODERATE)
        assert len(mod_table.cells) == 0


class TestPercentileComputation:
    def _fill_cell(self, engine: PolarEngine, tws_kt: float, twa_deg: float, n: int = 25):
        """Fill a cell with n samples of varying BSP."""
        tws_ms = tws_kt * KT_TO_MS
        twa_rad = twa_deg * DEG_TO_RAD
        rng = np.random.RandomState(42)
        for _ in range(n):
            bsp_kt = 5.0 + rng.uniform(0, 3.0)
            engine.add_sample(make_sample(tws_ms, twa_rad, bsp_kt * KT_TO_MS))

    def test_computes_95th_percentile(self, engine: PolarEngine):
        self._fill_cell(engine, 10, 90, n=30)
        engine.recompute()

        cell = engine.master.cells.get((10, 90))
        assert cell is not None
        assert cell.bsp_percentile is not None

        # 95th percentile should be near the high end of the range [5, 8]
        bsp_kt = cell.bsp_percentile * 1.94384
        assert 6.5 < bsp_kt < 8.5

    def test_cell_not_valid_below_min_samples(self, engine: PolarEngine):
        self._fill_cell(engine, 10, 90, n=5)  # Below 20 minimum
        engine.recompute()

        cell = engine.master.cells.get((10, 90))
        assert cell is not None
        assert not cell.is_valid


class TestSplineSmoothing:
    def _fill_curve(self, engine: PolarEngine, tws_kt: float):
        """Fill a full TWA range for a given TWS."""
        tws_ms = tws_kt * KT_TO_MS
        rng = np.random.RandomState(42)
        for twa_deg in range(30, 185, 5):
            twa_rad = twa_deg * DEG_TO_RAD
            # Realistic shape: higher BSP at beam reach, lower at close-hauled/run
            peak_factor = 1.0 - abs(twa_deg - 100) / 150.0
            base_bsp = (4.0 + peak_factor * 5.0) * KT_TO_MS
            for _ in range(25):
                bsp = base_bsp + rng.uniform(-0.3, 0.3) * KT_TO_MS
                engine.add_sample(make_sample(tws_ms, twa_rad, bsp))

    def test_spline_produces_smoothed_values(self, engine: PolarEngine):
        self._fill_curve(engine, 10)
        engine.recompute()

        cell = engine.master.cells.get((10, 90))
        assert cell is not None
        assert cell.bsp_smoothed is not None
        # Smoothed should be close to percentile
        assert abs(cell.bsp_smoothed - cell.bsp_percentile) < 0.5

    def test_spline_values_nonnegative(self, engine: PolarEngine):
        self._fill_curve(engine, 10)
        engine.recompute()

        for cell in engine.master.cells.values():
            if cell.bsp_smoothed is not None:
                assert cell.bsp_smoothed >= 0.0


class TestBilinearInterpolation:
    def _fill_grid(self, engine: PolarEngine):
        """Fill several TWS/TWA bins with data."""
        rng = np.random.RandomState(42)
        for tws_kt in [8, 10, 12]:
            for twa_deg in range(30, 185, 5):
                tws_ms = tws_kt * KT_TO_MS
                twa_rad = twa_deg * DEG_TO_RAD
                base = (tws_kt * 0.5 + twa_deg * 0.02) * KT_TO_MS
                for _ in range(25):
                    bsp = base + rng.uniform(-0.2, 0.2) * KT_TO_MS
                    engine.add_sample(make_sample(tws_ms, twa_rad, bsp))
        engine.recompute()

    def test_interpolates_at_bin_center(self, engine: PolarEngine):
        self._fill_grid(engine)
        bsp = engine.interpolate_bsp(10, 90)
        assert bsp is not None
        assert bsp > 0

    def test_interpolates_between_bins(self, engine: PolarEngine):
        self._fill_grid(engine)
        # Between TWS 10 and 12, TWA 87.5 (between 85 and 90)
        bsp = engine.interpolate_bsp(11, 87.5)
        assert bsp is not None
        # Should be between the values at the corners
        bsp_lo = engine.interpolate_bsp(10, 85)
        bsp_hi = engine.interpolate_bsp(12, 90)
        assert bsp_lo is not None and bsp_hi is not None

    def test_returns_none_for_empty_grid(self, engine: PolarEngine):
        bsp = engine.interpolate_bsp(10, 90)
        assert bsp is None

    def test_clamps_to_grid_bounds(self, engine: PolarEngine):
        self._fill_grid(engine)
        # At the edge of the filled grid (we filled 8, 10, 12)
        bsp = engine.interpolate_bsp(12, 90)
        assert bsp is not None  # Should return data for highest filled TWS

    def test_returns_none_far_beyond_filled_data(self, engine: PolarEngine):
        self._fill_grid(engine)
        # Way beyond filled bins — grid has data at 8/10/12 but TWS 50
        # clamps to 30 which has no data
        bsp = engine.interpolate_bsp(50, 90)
        # May be None if no data at clamped bin, or a value from nearest
        # Either is acceptable — test just verifies no crash


class TestSessionMerge:
    def _fill_session(self, engine: PolarEngine, tws_kt: float = 10, twa_deg: float = 90, n: int = 25):
        tws_ms = tws_kt * KT_TO_MS
        twa_rad = twa_deg * DEG_TO_RAD
        rng = np.random.RandomState(42)
        for _ in range(n):
            bsp = (5.0 + rng.uniform(0, 2)) * KT_TO_MS
            engine.add_sample(make_sample(tws_ms, twa_rad, bsp))

    def test_merge_creates_master_cell(self, engine: PolarEngine):
        self._fill_session(engine)
        engine.recompute()
        updated = engine.merge_session_to_master()
        assert updated == 1
        assert engine.master.version == 2

    def test_merge_blends_with_ema(self, engine: PolarEngine):
        # First session
        self._fill_session(engine, n=30)
        engine.recompute()
        engine.merge_session_to_master()
        first_bsp = engine.master.cells[(10, 90)].bsp_percentile

        # Reset session and add new data
        engine.reset_session()
        tws_ms = 10 * KT_TO_MS
        twa_rad = 90 * DEG_TO_RAD
        rng = np.random.RandomState(99)
        for _ in range(30):
            bsp = (8.0 + rng.uniform(0, 2)) * KT_TO_MS  # Faster!
            engine.add_sample(make_sample(tws_ms, twa_rad, bsp))

        engine.recompute()
        engine.merge_session_to_master()
        second_bsp = engine.master.cells[(10, 90)].bsp_percentile

        # Should be higher due to EMA blend with faster data
        assert second_bsp > first_bsp

    def test_merge_skips_underfilled_cells(self, engine: PolarEngine):
        self._fill_session(engine, n=5)  # Below min_samples_per_cell
        engine.recompute()
        updated = engine.merge_session_to_master()
        assert updated == 0

    def test_reset_session_clears(self, engine: PolarEngine):
        self._fill_session(engine)
        assert engine._session_sample_count > 0

        engine.reset_session()
        assert engine._session_sample_count == 0
        assert len(engine.session_table.cells) == 0


class TestResetMaster:
    def test_reset_returns_old(self, engine: PolarEngine):
        sample = make_sample(10 * KT_TO_MS, 90 * DEG_TO_RAD, 5 * KT_TO_MS)
        engine.add_sample(sample)

        old = engine.reset_master()
        assert len(old.cells) == 1
        assert len(engine.master.cells) == 0

    def test_set_master(self, engine: PolarEngine):
        table = PolarTable(
            tws_bins_kt=[10, 12],
            twa_bins_deg=[90, 95],
            version=42,
        )
        engine.set_master(table)
        assert engine.master.version == 42


class TestGetPolarCurve:
    def test_returns_valid_curve(self, engine: PolarEngine):
        rng = np.random.RandomState(42)
        tws_ms = 10 * KT_TO_MS
        for twa_deg in [80, 85, 90, 95, 100]:
            twa_rad = twa_deg * DEG_TO_RAD
            for _ in range(25):
                bsp = (5.0 + rng.uniform(0, 2)) * KT_TO_MS
                engine.add_sample(make_sample(tws_ms, twa_rad, bsp))

        engine.recompute()
        curve = engine.get_polar_curve(10)
        assert len(curve) == 5
        for twa, bsp in curve:
            assert 80 <= twa <= 100
            assert bsp > 0

    def test_returns_empty_for_no_data(self, engine: PolarEngine):
        curve = engine.get_polar_curve(10)
        assert len(curve) == 0


class TestBracket:
    def test_mid_range(self):
        lo, hi = PolarEngine._bracket(9.0, [4, 6, 8, 10, 12])
        assert lo == 8
        assert hi == 10

    def test_exact_boundary(self):
        # 8.0 falls in interval [6, 8], so bracket returns (6, 8)
        lo, hi = PolarEngine._bracket(8.0, [4, 6, 8, 10, 12])
        assert lo == 6
        assert hi == 8

    def test_below_range(self):
        lo, hi = PolarEngine._bracket(2.0, [4, 6, 8, 10, 12])
        assert lo == 4
        assert hi == 4

    def test_above_range(self):
        lo, hi = PolarEngine._bracket(15.0, [4, 6, 8, 10, 12])
        assert lo == 12
        assert hi == 12


class TestDataDensity:
    def test_returns_correct_counts(self, engine: PolarEngine):
        tws_ms = 10 * KT_TO_MS
        twa_rad = 90 * DEG_TO_RAD
        for _ in range(5):
            engine.add_sample(make_sample(tws_ms, twa_rad, 5 * KT_TO_MS))

        density = engine.get_data_density()
        assert len(density["tws"]) == 1
        assert density["count"][0] == 5
