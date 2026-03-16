"""Trip manager — trip lifecycle, per-trip polars, persistence.

Manages start/stop of trips, accumulates per-trip polar data,
and persists trip metadata and polars to disk.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from config import Config
from models import PolarTable, Trip, ValidSample
from polar_engine import PolarEngine
from polar_store import PolarStore

logger = logging.getLogger(__name__)


class TripManager:
    """Manages sailing trips with per-trip polar data."""

    def __init__(
        self,
        config: Config,
        engine: PolarEngine,
        store: PolarStore,
    ) -> None:
        self._config = config
        self._engine = engine
        self._store = store
        self._trips_dir = Path(config.data_dir) / config.trips_dir
        self._trips_dir.mkdir(parents=True, exist_ok=True)

        self._active_trip: Trip | None = None
        self._trip_engine: PolarEngine | None = None

    @property
    def active_trip(self) -> Trip | None:
        return self._active_trip

    @property
    def trip_engine(self) -> PolarEngine | None:
        return self._trip_engine

    def start_trip(self, name: str, notes: str = "") -> Trip:
        """Start a new trip. Ends any active trip first."""
        if self._active_trip is not None:
            self.end_trip()

        trip = Trip(
            trip_id=str(uuid.uuid4())[:8],
            name=name,
            started_at=time.time(),
            notes=notes,
        )

        # Create per-trip polar engine
        self._trip_engine = PolarEngine(self._config)
        self._active_trip = trip

        # Also reset the global session buffer
        self._engine.reset_session()

        self._save_trip_meta(trip)
        logger.info("Started trip '%s' (id=%s)", name, trip.trip_id)
        return trip

    def end_trip(self) -> Trip | None:
        """End the active trip. Merges session data into master polar."""
        if self._active_trip is None:
            return None

        trip = self._active_trip
        trip.ended_at = time.time()
        trip.is_active = False

        # Compute trip polar
        if self._trip_engine is not None:
            self._trip_engine.recompute()
            # Save trip polar
            trip_polar_path = self._trip_dir(trip.trip_id) / "polar.json"
            data = PolarStore._table_to_dict(self._trip_engine.master)
            trip_polar_path.write_text(json.dumps(data, indent=2))

        # Merge session into master
        updated = self._engine.merge_session_to_master()
        self._engine.reset_session()

        # Save updated master
        self._store.save(self._engine.master)

        # Save final trip metadata
        self._save_trip_meta(trip)

        logger.info(
            "Ended trip '%s': %d samples, %d cells merged to master",
            trip.name,
            trip.sample_count,
            updated,
        )

        self._active_trip = None
        self._trip_engine = None
        return trip

    def add_sample(self, sample: ValidSample) -> None:
        """Add a sample to the active trip (if any)."""
        if self._active_trip is not None and self._trip_engine is not None:
            self._trip_engine.add_sample(sample)
            self._active_trip.sample_count += 1

    def list_trips(self) -> list[dict[str, Any]]:
        """List all trips with metadata."""
        trips = []
        for meta_file in sorted(self._trips_dir.glob("*/meta.json")):
            try:
                data = json.loads(meta_file.read_text())
                trips.append(data)
            except Exception:
                pass
        return trips

    def get_trip(self, trip_id: str) -> dict[str, Any] | None:
        """Get trip metadata by ID."""
        meta_path = self._trip_dir(trip_id) / "meta.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            return None

    def get_trip_polar(self, trip_id: str) -> PolarTable | None:
        """Load a trip's polar table."""
        polar_path = self._trip_dir(trip_id) / "polar.json"
        if not polar_path.exists():
            return None
        try:
            data = json.loads(polar_path.read_text())
            return PolarStore._dict_to_table(data)
        except Exception as exc:
            logger.error("Failed to load trip polar %s: %s", trip_id, exc)
            return None

    def delete_trip(self, trip_id: str) -> bool:
        """Delete a trip and its data."""
        trip_dir = self._trip_dir(trip_id)
        if not trip_dir.exists():
            return False
        import shutil

        shutil.rmtree(trip_dir)
        logger.info("Deleted trip %s", trip_id)
        return True

    def _trip_dir(self, trip_id: str) -> Path:
        d = self._trips_dir / trip_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_trip_meta(self, trip: Trip) -> None:
        meta_path = self._trip_dir(trip.trip_id) / "meta.json"
        meta_path.write_text(json.dumps(asdict(trip), indent=2, default=str))
