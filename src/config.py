"""Configuration for Polar Analyzer.

Layered: defaults → environment variables (POLAR_ANALYZER_*) → CLI args.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- Versioning ---
# Data format version — included in every output row so training pipelines
# can partition or filter data by the version that produced it.
# Bump when: fields added/removed/renamed in InstantSample, ValidSample,
# PolarCell, PolarTable, PerformanceMetrics; or when feature semantics change.
VERSION = "0.1.0"


@dataclass
class Config:
    """Application configuration with sensible defaults."""

    # --- App version (matches polar_analyzer/config.yaml) ---
    app_version: str = "0.1.0"

    # --- SignalK connection ---
    signalk_url: str = "http://primrose.local:3000"
    reconnect_delays: list[float] = field(default_factory=lambda: [1, 2, 5, 10, 30])

    @property
    def signalk_ws_url(self) -> str:
        """Derive WebSocket streaming URL from the base SignalK URL.

        Accepts either an HTTP base URL (``http://host:3000``) or a legacy
        full WebSocket URL (``ws://host:3000/signalk/v1/stream?subscribe=none``).
        """
        base = self.signalk_url.rstrip("/")
        # Already a full WebSocket URL — return as-is
        if base.startswith(("ws://", "wss://")) and "/signalk/" in base:
            return base
        ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
        return f"{ws_base}/signalk/v1/stream?subscribe=none"

    @property
    def signalk_http_url(self) -> str:
        """HTTP base URL, derived from signalk_url.

        Handles both ``http://host:3000`` and legacy ``ws://host:3000/...``
        formats so the auth module always gets a clean HTTP base URL.
        """
        base = self.signalk_url.rstrip("/")
        # Strip WebSocket path if a legacy full WS URL was provided
        if base.startswith(("ws://", "wss://")):
            base = base.split("/signalk/")[0] if "/signalk/" in base else base
            base = base.replace("wss://", "https://").replace("ws://", "http://")
        return base

    # --- Auth ---
    device_name: str = "Polar Analyzer"
    device_description: str = "Learns boat polars from live sailing data"
    token_file: str = str(Path.home() / ".polar_analyzer" / "signalk_token.json")
    auth_poll_interval: float = 5.0

    # --- Sampling ---
    sample_rate_hz: float = 1.0
    stale_threshold_s: float = 10.0

    # --- Filter thresholds ---
    filter_window_s: float = 60.0
    tws_cv_max: float = 0.15  # TWS coeff of variation (std/mean)
    twd_std_max_deg: float = 10.0  # TWD std dev in degrees
    bsp_cv_max: float = 0.15  # BSP coeff of variation
    rot_max_deg_s: float = 2.0  # Rate of turn deg/s
    tack_heading_change_deg: float = 25.0  # Heading change that flags tack/gybe
    tack_detection_window_s: float = 10.0
    tack_exclusion_s: float = 30.0  # Exclude data ± this many seconds around tack
    min_tws_ms: float = 1.54  # ~3 knots
    min_bsp_ms: float = 0.26  # ~0.5 knots

    # --- Propulsion filter ---
    motoring_bsp_cv_max: float = 0.05  # BSP very steady → motoring suspect
    motoring_tws_cv_min: float = 0.20  # while TWS is variable → confirms motoring
    motoring_detection_window_s: float = 30.0

    # --- Polar binning ---
    tws_bin_centers_kt: list[float] = field(
        default_factory=lambda: [4, 6, 8, 10, 12, 14, 16, 18, 20, 25, 30]
    )
    twa_bin_centers_deg: list[float] = field(
        default_factory=lambda: [float(a) for a in range(30, 185, 5)]  # 30..180
    )
    min_samples_per_cell: int = 20
    polar_percentile: float = 95.0

    # --- Sea state classification ---
    sea_state_flat_max_m: float = 0.5
    sea_state_moderate_max_m: float = 1.5
    # Above moderate_max → rough

    # --- Performance calculation ---
    performance_update_hz: float = 1.0

    # --- Publishing ---
    publish_interval_s: float = 5.0
    source_label: str = "polar-analyzer"

    # --- Incremental polar update ---
    ema_alpha: float = 0.1

    # --- Web server ---
    web_port: int = 3001
    web_static_dir: str = "web"

    # --- Data storage ---
    data_dir: str = str(Path.home() / ".polar_analyzer")
    polar_file: str = "polars.json"
    trips_dir: str = "trips"
    recorder_batch_size: int = 100

    # --- Queue ---
    queue_maxsize: int = 1000

    @classmethod
    def from_env(cls) -> Config:
        """Build config from POLAR_ANALYZER_* environment variables."""
        cfg = cls()
        env_map: dict[str, tuple[str, type]] = {
            "POLAR_ANALYZER_SIGNALK_URL": ("signalk_url", str),
            "POLAR_ANALYZER_TOKEN_FILE": ("token_file", str),
            "POLAR_ANALYZER_DATA_DIR": ("data_dir", str),
            "POLAR_ANALYZER_WEB_PORT": ("web_port", int),
            "POLAR_ANALYZER_SAMPLE_RATE": ("sample_rate_hz", float),
            "POLAR_ANALYZER_PUBLISH_INTERVAL": ("publish_interval_s", float),
            "POLAR_ANALYZER_WEB_STATIC_DIR": ("web_static_dir", str),
        }
        for env_key, (attr, conv) in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                setattr(cfg, attr, conv(val))
        return cfg
