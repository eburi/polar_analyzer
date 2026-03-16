"""Polar engine — binning, percentile computation, spline smoothing.

Accepts ValidSamples, assigns them to (TWS, TWA) bins, computes
95th-percentile BSP per cell, and applies cubic spline smoothing
along the TWA axis for each TWS column.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from scipy.interpolate import CubicSpline

from config import Config
from models import (
    MS_TO_KT,
    PolarTable,
    SeaState,
    ValidSample,
)

logger = logging.getLogger(__name__)


class PolarEngine:
    """Manages polar tables: binning, computation, and incremental updates."""

    def __init__(self, config: Config) -> None:
        self._config = config
        # Master polar (aggregated across all time, sea_state=UNKNOWN means "all")
        self._master = self._create_empty_table(SeaState.UNKNOWN)
        # Per-sea-state polars
        self._by_sea_state: dict[SeaState, PolarTable] = {
            SeaState.FLAT: self._create_empty_table(SeaState.FLAT),
            SeaState.MODERATE: self._create_empty_table(SeaState.MODERATE),
            SeaState.ROUGH: self._create_empty_table(SeaState.ROUGH),
        }
        # Session buffer for incremental update
        self._session_table = self._create_empty_table(SeaState.UNKNOWN)
        self._session_sample_count = 0

    @property
    def master(self) -> PolarTable:
        return self._master

    @property
    def session_table(self) -> PolarTable:
        return self._session_table

    def get_sea_state_table(self, sea_state: SeaState) -> PolarTable:
        """Get the polar table for a specific sea state."""
        return self._by_sea_state.get(sea_state, self._master)

    def add_sample(self, sample: ValidSample) -> None:
        """Add a validated sample to the appropriate polar bins."""
        tws_kt = sample.tws_kt
        twa_deg = sample.twa_deg
        bsp_ms = sample.bsp

        # Bounds check
        if tws_kt < self._config.tws_bin_centers_kt[0] * 0.5:
            return  # Too light
        if tws_kt > self._config.tws_bin_centers_kt[-1] * 1.5:
            return  # Off-scale high
        if twa_deg < self._config.twa_bin_centers_deg[0] - 2.5:
            return  # Too close to head-to-wind
        if twa_deg > self._config.twa_bin_centers_deg[-1] + 2.5:
            return  # Past dead downwind

        # Add to master table
        cell = self._master.get_cell(tws_kt, twa_deg)
        cell.add_sample(bsp_ms)

        # Add to session table
        session_cell = self._session_table.get_cell(tws_kt, twa_deg)
        session_cell.add_sample(bsp_ms)
        self._session_sample_count += 1

        # Add to sea-state-specific table
        if sample.sea_state in self._by_sea_state:
            ss_cell = self._by_sea_state[sample.sea_state].get_cell(tws_kt, twa_deg)
            ss_cell.add_sample(bsp_ms)

    def recompute(self) -> None:
        """Recompute percentiles and spline smoothing for all tables."""
        self._compute_table(self._master)
        self._compute_table(self._session_table)
        for table in self._by_sea_state.values():
            self._compute_table(table)

    def merge_session_to_master(self) -> int:
        """Merge session data into master using EMA blending.

        Returns the number of cells updated.
        """
        alpha = self._config.ema_alpha
        min_samples = self._config.min_samples_per_cell
        updated = 0

        for key, session_cell in self._session_table.cells.items():
            if session_cell.sample_count < min_samples:
                continue

            # Compute session percentile
            session_bsp = float(np.percentile(session_cell.samples, self._config.polar_percentile))

            master_cell = self._master.cells.get(key)
            if master_cell is None:
                # New cell — adopt session value directly
                master_cell = self._master.get_cell(
                    session_cell.tws_center_kt, session_cell.twa_center_deg
                )
                master_cell.samples.extend(session_cell.samples)
                master_cell.sample_count = len(master_cell.samples)
                master_cell.bsp_percentile = session_bsp
            else:
                # Existing cell — EMA blend
                if master_cell.bsp_percentile is not None:
                    master_cell.bsp_percentile = (
                        alpha * session_bsp + (1 - alpha) * master_cell.bsp_percentile
                    )
                else:
                    master_cell.bsp_percentile = session_bsp
                # Append samples (capped to prevent unbounded growth)
                max_samples = min_samples * 50
                master_cell.samples.extend(session_cell.samples)
                if len(master_cell.samples) > max_samples:
                    master_cell.samples = master_cell.samples[-max_samples:]
                master_cell.sample_count = len(master_cell.samples)

            updated += 1

        if updated > 0:
            self._master.updated_at = time.time()
            self._master.version += 1
            # Re-smooth the master after merge
            self._smooth_table(self._master)
            logger.info("Merged session into master: %d cells updated", updated)

        return updated

    def reset_session(self) -> None:
        """Clear the session buffer."""
        self._session_table = self._create_empty_table(SeaState.UNKNOWN)
        self._session_sample_count = 0

    def reset_master(self) -> PolarTable:
        """Archive and reset the master polar. Returns the old master."""
        old = self._master
        self._master = self._create_empty_table(SeaState.UNKNOWN)
        for ss in self._by_sea_state:
            self._by_sea_state[ss] = self._create_empty_table(ss)
        logger.info("Master polar reset (old had %d cells)", len(old.cells))
        return old

    def set_master(self, table: PolarTable) -> None:
        """Replace the master polar (e.g. from loaded file)."""
        self._master = table
        logger.info(
            "Master polar loaded: %d cells, version %d",
            len(table.cells),
            table.version,
        )

    def interpolate_bsp(self, tws_kt: float, twa_deg: float) -> float | None:
        """Bilinear interpolation of BSP from the master polar.

        Returns BSP in m/s, or None if insufficient data.
        """
        return self._bilinear_interpolate(self._master, tws_kt, twa_deg)

    def get_polar_curve(
        self, tws_kt: float, table: PolarTable | None = None
    ) -> list[tuple[float, float]]:
        """Get the polar curve for a given TWS as [(twa_deg, bsp_kt), ...].

        Uses smoothed values where available.
        """
        if table is None:
            table = self._master
        tws_bin = PolarTable._nearest_bin(tws_kt, table.tws_bins_kt)
        curve = []
        for twa_deg in table.twa_bins_deg:
            cell = table.cells.get((tws_bin, twa_deg))
            if cell is not None and cell.is_valid:
                bsp = cell.bsp_smoothed if cell.bsp_smoothed is not None else cell.bsp_percentile
                if bsp is not None:
                    curve.append((twa_deg, bsp * MS_TO_KT))
        return curve

    def get_data_density(self, table: PolarTable | None = None) -> dict[str, list]:
        """Get sample count per cell for heatmap display.

        Returns {"tws": [...], "twa": [...], "count": [...]}.
        """
        if table is None:
            table = self._master
        tws_list = []
        twa_list = []
        counts = []
        for (tws, twa), cell in table.cells.items():
            tws_list.append(tws)
            twa_list.append(twa)
            counts.append(cell.sample_count)
        return {"tws": tws_list, "twa": twa_list, "count": counts}

    # --- Internal computation ---

    def _create_empty_table(self, sea_state: SeaState) -> PolarTable:
        return PolarTable(
            tws_bins_kt=list(self._config.tws_bin_centers_kt),
            twa_bins_deg=list(self._config.twa_bin_centers_deg),
            sea_state=sea_state,
        )

    def _compute_table(self, table: PolarTable) -> None:
        """Compute percentiles then smooth for a table."""
        self._compute_percentiles(table)
        self._smooth_table(table)
        table.updated_at = time.time()

    def _compute_percentiles(self, table: PolarTable) -> None:
        """Compute the configured percentile BSP for each valid cell."""
        pctl = self._config.polar_percentile
        for cell in table.cells.values():
            if cell.is_valid:
                cell.bsp_percentile = float(np.percentile(cell.samples, pctl))
            else:
                cell.bsp_percentile = None

    def _smooth_table(self, table: PolarTable) -> None:
        """Apply cubic spline smoothing along TWA axis for each TWS column.

        This produces physically smoother polar curves and eliminates
        statistical noise from the percentile values.
        """
        for tws_bin in table.tws_bins_kt:
            # Collect valid cells for this TWS
            twa_vals = []
            bsp_vals = []
            for twa_deg in table.twa_bins_deg:
                cell = table.cells.get((tws_bin, twa_deg))
                if cell is not None and cell.bsp_percentile is not None and cell.is_valid:
                    twa_vals.append(twa_deg)
                    bsp_vals.append(cell.bsp_percentile)

            if len(twa_vals) < 4:
                # Not enough points for cubic spline
                for twa_deg in table.twa_bins_deg:
                    cell = table.cells.get((tws_bin, twa_deg))
                    if cell is not None:
                        cell.bsp_smoothed = cell.bsp_percentile
                continue

            # Fit cubic spline
            try:
                twa_arr = np.array(twa_vals)
                bsp_arr = np.array(bsp_vals)
                cs = CubicSpline(twa_arr, bsp_arr, bc_type="natural")

                # Evaluate at all bin centers within the data range
                for twa_deg in table.twa_bins_deg:
                    cell = table.cells.get((tws_bin, twa_deg))
                    if cell is None:
                        continue
                    if twa_vals[0] <= twa_deg <= twa_vals[-1]:
                        smoothed = float(cs(twa_deg))
                        # Ensure non-negative and physically reasonable
                        cell.bsp_smoothed = max(0.0, smoothed)
                    else:
                        cell.bsp_smoothed = cell.bsp_percentile
            except (ValueError, np.linalg.LinAlgError) as exc:
                logger.debug("Spline smoothing failed for TWS=%s: %s", tws_bin, exc)
                for twa_deg in table.twa_bins_deg:
                    cell = table.cells.get((tws_bin, twa_deg))
                    if cell is not None:
                        cell.bsp_smoothed = cell.bsp_percentile

    def _bilinear_interpolate(
        self,
        table: PolarTable,
        tws_kt: float,
        twa_deg: float,
    ) -> float | None:
        """Bilinear interpolation of BSP from the polar grid.

        Finds the four surrounding cells and interpolates between them.
        Returns BSP in m/s, or None if insufficient data.
        """
        tws_bins = table.tws_bins_kt
        twa_bins = table.twa_bins_deg

        # Clamp to grid bounds
        tws_kt = max(tws_bins[0], min(tws_kt, tws_bins[-1]))
        twa_deg = max(twa_bins[0], min(twa_deg, twa_bins[-1]))

        # Find surrounding TWS bins
        tws_lo, tws_hi = self._bracket(tws_kt, tws_bins)
        twa_lo, twa_hi = self._bracket(twa_deg, twa_bins)

        # Look up the four corner values
        def _val(tw: float, ta: float) -> float | None:
            cell = table.cells.get((tw, ta))
            if cell is None or not cell.is_valid:
                return None
            return cell.bsp_smoothed if cell.bsp_smoothed is not None else cell.bsp_percentile

        v00 = _val(tws_lo, twa_lo)
        v01 = _val(tws_lo, twa_hi)
        v10 = _val(tws_hi, twa_lo)
        v11 = _val(tws_hi, twa_hi)

        # Need at least two corners to interpolate
        corners = [v for v in [v00, v01, v10, v11] if v is not None]
        if len(corners) < 2:
            # Fall back to nearest single cell
            return _val(
                PolarTable._nearest_bin(tws_kt, tws_bins),
                PolarTable._nearest_bin(twa_deg, twa_bins),
            )

        # Fill missing corners with average of available
        avg = sum(corners) / len(corners)
        v00 = v00 if v00 is not None else avg
        v01 = v01 if v01 is not None else avg
        v10 = v10 if v10 is not None else avg
        v11 = v11 if v11 is not None else avg

        # Compute interpolation fractions
        if tws_hi != tws_lo:
            tws_frac = (tws_kt - tws_lo) / (tws_hi - tws_lo)
        else:
            tws_frac = 0.0

        if twa_hi != twa_lo:
            twa_frac = (twa_deg - twa_lo) / (twa_hi - twa_lo)
        else:
            twa_frac = 0.0

        # Bilinear
        v0 = v00 * (1 - twa_frac) + v01 * twa_frac
        v1 = v10 * (1 - twa_frac) + v11 * twa_frac
        result = v0 * (1 - tws_frac) + v1 * tws_frac

        return max(0.0, result)

    @staticmethod
    def _bracket(value: float, bins: list[float]) -> tuple[float, float]:
        """Find the two bins that bracket a value."""
        for i in range(len(bins) - 1):
            if bins[i] <= value <= bins[i + 1]:
                return bins[i], bins[i + 1]
        # At edges
        if value <= bins[0]:
            return bins[0], bins[0]
        return bins[-1], bins[-1]
