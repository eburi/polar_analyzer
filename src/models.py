"""Data models — all dataclasses, all SI units internally."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# --- Unit conversions (internal SI ↔ display) ---

MS_TO_KT = 1.94384
KT_TO_MS = 1 / MS_TO_KT
RAD_TO_DEG = 180.0 / math.pi
DEG_TO_RAD = math.pi / 180.0


# --- Enums ---


class SeaState(Enum):
    """Sea state classification by significant wave height."""

    FLAT = "flat"  # Hs < 0.5 m
    MODERATE = "moderate"  # 0.5 ≤ Hs < 1.5 m
    ROUGH = "rough"  # Hs ≥ 1.5 m
    UNKNOWN = "unknown"  # No wave data available


class PropulsionMode(Enum):
    """Current propulsion detection result."""

    SAILING = "sailing"
    MOTORING = "motoring"
    UNKNOWN = "unknown"


class PropulsionOverride(Enum):
    """Manual override from web UI."""

    AUTO = "auto"  # Use auto-detection (layers 1+2)
    SAILING = "sailing"  # Force sailing (e.g. engine on for battery charging)
    MOTORING = "motoring"  # Force motoring


class FilterRejectReason(Enum):
    """Why a sample was rejected by the steady-state filter."""

    MOTORING = "motoring"
    TWS_TOO_LOW = "tws_too_low"
    BSP_TOO_LOW = "bsp_too_low"
    TWS_UNSTABLE = "tws_unstable"
    TWD_UNSTABLE = "twd_unstable"
    BSP_UNSTABLE = "bsp_unstable"
    TURNING = "turning"
    TACK_GYBE = "tack_gybe"
    STALE_DATA = "stale_data"
    INSUFFICIENT_DATA = "insufficient_data"


# --- SignalK raw update ---


@dataclass
class SignalKUpdate:
    """A single path/value update from a SignalK delta."""

    path: str
    value: Any
    timestamp: str  # ISO 8601
    source: str = ""


# --- Instant sample (snapshot of all relevant state) ---


@dataclass
class InstantSample:
    """1Hz snapshot from the state store — all values in SI."""

    timestamp: float  # Unix epoch

    # Wind
    tws: float | None = None  # m/s
    twa: float | None = None  # rad (signed, negative=port)
    aws: float | None = None  # m/s
    awa: float | None = None  # rad (signed)

    # Speed
    bsp: float | None = None  # m/s (speed through water)
    sog: float | None = None  # m/s (speed over ground)

    # Course / heading
    cog: float | None = None  # rad
    heading: float | None = None  # rad
    rot: float | None = None  # rad/s (rate of turn)

    # Attitude
    pitch: float | None = None  # rad
    roll: float | None = None  # rad

    # Position
    latitude: float | None = None  # degrees
    longitude: float | None = None  # degrees

    # Steering
    rudder_angle: float | None = None  # rad

    # Environment
    current_drift: float | None = None  # m/s
    current_set: float | None = None  # rad
    wave_height: float | None = None  # m (significant height)
    wave_period: float | None = None  # s
    motion_severity: float | None = None  # 0-1 ratio
    depth: float | None = None  # m

    # Propulsion (optional)
    engine_rpm: float | None = None  # revolutions/s (from SignalK)
    engine_state: str | None = None  # "stopped", "started", etc.

    @property
    def twa_deg(self) -> float | None:
        """TWA in degrees (absolute value for polar binning)."""
        return abs(self.twa) * RAD_TO_DEG if self.twa is not None else None

    @property
    def tws_kt(self) -> float | None:
        """TWS in knots."""
        return self.tws * MS_TO_KT if self.tws is not None else None

    @property
    def bsp_kt(self) -> float | None:
        """BSP in knots."""
        return self.bsp * MS_TO_KT if self.bsp is not None else None

    def has_required_fields(self) -> bool:
        """Check if the minimum fields for polar recording are present."""
        return all(v is not None for v in [self.tws, self.twa, self.bsp])


# --- Valid (filtered) sample ready for polar binning ---


@dataclass
class ValidSample:
    """A sample that passed all filters — ready for polar binning."""

    timestamp: float
    tws: float  # m/s
    twa_abs: float  # rad (absolute — symmetric)
    bsp: float  # m/s
    sea_state: SeaState = SeaState.UNKNOWN

    # Optional context
    sog: float | None = None
    current_drift: float | None = None
    wave_height: float | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def tws_kt(self) -> float:
        return self.tws * MS_TO_KT

    @property
    def twa_deg(self) -> float:
        return self.twa_abs * RAD_TO_DEG

    @property
    def bsp_kt(self) -> float:
        return self.bsp * MS_TO_KT


# --- Polar table structures ---


@dataclass
class PolarCell:
    """One cell in the polar grid: a (TWS, TWA) bin."""

    tws_center_kt: float
    twa_center_deg: float
    samples: list[float] = field(default_factory=list)  # BSP values in m/s
    bsp_percentile: float | None = None  # Computed polar BSP (m/s)
    bsp_smoothed: float | None = None  # After spline smoothing (m/s)
    sample_count: int = 0

    def add_sample(self, bsp_ms: float) -> None:
        """Add a BSP sample to this cell."""
        self.samples.append(bsp_ms)
        self.sample_count = len(self.samples)

    @property
    def is_valid(self) -> bool:
        """Has enough samples for a reliable polar value."""
        return self.sample_count >= 20  # Config default, but hardcoded here for speed


@dataclass
class PolarTable:
    """Full polar grid: TWS x TWA -> BSP."""

    tws_bins_kt: list[float]  # bin centers
    twa_bins_deg: list[float]  # bin centers
    cells: dict[tuple[float, float], PolarCell] = field(default_factory=dict)
    sea_state: SeaState = SeaState.UNKNOWN
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 1

    def get_cell(self, tws_kt: float, twa_deg: float) -> PolarCell:
        """Get or create the cell for the nearest bin."""
        tws_bin = self._nearest_bin(tws_kt, self.tws_bins_kt)
        twa_bin = self._nearest_bin(twa_deg, self.twa_bins_deg)
        key = (tws_bin, twa_bin)
        if key not in self.cells:
            self.cells[key] = PolarCell(tws_center_kt=tws_bin, twa_center_deg=twa_bin)
        return self.cells[key]

    def lookup_bsp(self, tws_kt: float, twa_deg: float) -> float | None:
        """Look up polar BSP (smoothed if available, else percentile) in m/s."""
        tws_bin = self._nearest_bin(tws_kt, self.tws_bins_kt)
        twa_bin = self._nearest_bin(twa_deg, self.twa_bins_deg)
        cell = self.cells.get((tws_bin, twa_bin))
        if cell is None or not cell.is_valid:
            return None
        return cell.bsp_smoothed if cell.bsp_smoothed is not None else cell.bsp_percentile

    @staticmethod
    def _nearest_bin(value: float, bins: list[float]) -> float:
        """Find the nearest bin center."""
        return min(bins, key=lambda b: abs(b - value))


# --- Performance metrics ---


@dataclass
class PerformanceMetrics:
    """Real-time performance calculations published to SignalK."""

    timestamp: float

    # Core metrics
    polar_speed: float | None = None  # m/s — target BSP from polar
    polar_speed_ratio: float | None = None  # ratio — actual / target
    vmg: float | None = None  # m/s — VMG to wind

    # Optimal upwind
    beat_angle: float | None = None  # rad
    beat_angle_vmg: float | None = None  # m/s
    beat_angle_target_speed: float | None = None  # m/s

    # Optimal downwind
    gybe_angle: float | None = None  # rad
    gybe_angle_vmg: float | None = None  # m/s
    gybe_angle_target_speed: float | None = None  # m/s

    # Current applicable target
    target_angle: float | None = None  # rad
    target_speed: float | None = None  # m/s


# --- Trip ---


@dataclass
class Trip:
    """A sailing trip/voyage/race with its own polar data."""

    trip_id: str
    name: str
    started_at: float  # Unix epoch
    ended_at: float | None = None
    is_active: bool = True
    sample_count: int = 0
    distance_nm: float = 0.0
    notes: str = ""


# --- Filter result ---


@dataclass
class FilterResult:
    """Result of applying all filters to an InstantSample."""

    passed: bool
    propulsion_mode: PropulsionMode = PropulsionMode.UNKNOWN
    reject_reasons: list[FilterRejectReason] = field(default_factory=list)
    valid_sample: ValidSample | None = None
