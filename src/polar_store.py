"""Polar store — JSON persistence with versioning.

Saves and loads PolarTable instances to/from JSON files.
Supports archiving old polars with timestamps.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from config import Config
from models import PolarCell, PolarTable, SeaState

logger = logging.getLogger(__name__)


class PolarStore:
    """Persists polar tables to JSON files."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._data_dir = Path(config.data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def polar_path(self) -> Path:
        return self._data_dir / self._config.polar_file

    @property
    def archive_dir(self) -> Path:
        d = self._data_dir / "archive"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, table: PolarTable) -> Path:
        """Save a polar table to the default location."""
        path = self.polar_path
        data = self._table_to_dict(table)
        path.write_text(json.dumps(data, indent=2))
        logger.info(
            "Saved polar: %d cells, version %d → %s",
            len(table.cells),
            table.version,
            path,
        )
        return path

    def load(self) -> PolarTable | None:
        """Load the polar table from the default location.

        Returns None if file doesn't exist.
        """
        path = self.polar_path
        if not path.exists():
            logger.info("No existing polar file at %s", path)
            return None

        try:
            data = json.loads(path.read_text())
            table = self._dict_to_table(data)
            logger.info(
                "Loaded polar: %d cells, version %d from %s",
                len(table.cells),
                table.version,
                path,
            )
            return table
        except Exception as exc:
            logger.error("Failed to load polar from %s: %s", path, exc)
            return None

    def archive(self, table: PolarTable) -> Path:
        """Archive a polar table with timestamp in filename."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"polars_archive_{ts}_v{table.version}.json"
        path = self.archive_dir / filename
        data = self._table_to_dict(table)
        path.write_text(json.dumps(data, indent=2))
        logger.info("Archived polar → %s", path)
        return path

    def list_archives(self) -> list[dict[str, Any]]:
        """List all archived polars with metadata."""
        archives = []
        for f in sorted(self.archive_dir.glob("polars_archive_*.json")):
            try:
                data = json.loads(f.read_text())
                archives.append(
                    {
                        "filename": f.name,
                        "path": str(f),
                        "version": data.get("version", 0),
                        "created_at": data.get("created_at", 0),
                        "updated_at": data.get("updated_at", 0),
                        "cell_count": len(data.get("cells", {})),
                        "sea_state": data.get("sea_state", "unknown"),
                    }
                )
            except Exception:
                archives.append({"filename": f.name, "error": "unreadable"})
        return archives

    def load_archive(self, filename: str) -> PolarTable | None:
        """Load a specific archived polar."""
        path = self.archive_dir / filename
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return self._dict_to_table(data)
        except Exception as exc:
            logger.error("Failed to load archive %s: %s", filename, exc)
            return None

    # --- Serialization ---

    @staticmethod
    def _table_to_dict(table: PolarTable) -> dict[str, Any]:
        """Serialize a PolarTable to a JSON-compatible dict."""
        cells_dict = {}
        for (tws, twa), cell in table.cells.items():
            key = f"{tws},{twa}"
            cells_dict[key] = {
                "tws_kt": cell.tws_center_kt,
                "twa_deg": cell.twa_center_deg,
                "sample_count": cell.sample_count,
                "bsp_percentile_ms": cell.bsp_percentile,
                "bsp_smoothed_ms": cell.bsp_smoothed,
                # Don't store raw samples in the archive — too large.
                # Store summary stats instead.
            }

        return {
            "format": "polar_analyzer_v1",
            "tws_bins_kt": table.tws_bins_kt,
            "twa_bins_deg": table.twa_bins_deg,
            "sea_state": table.sea_state.value,
            "created_at": table.created_at,
            "updated_at": table.updated_at,
            "version": table.version,
            "cells": cells_dict,
        }

    @staticmethod
    def _dict_to_table(data: dict[str, Any]) -> PolarTable:
        """Deserialize a PolarTable from a dict."""
        table = PolarTable(
            tws_bins_kt=data["tws_bins_kt"],
            twa_bins_deg=data["twa_bins_deg"],
            sea_state=SeaState(data.get("sea_state", "unknown")),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            version=data.get("version", 1),
        )

        for _key_str, cell_data in data.get("cells", {}).items():
            tws = cell_data["tws_kt"]
            twa = cell_data["twa_deg"]
            cell = PolarCell(
                tws_center_kt=tws,
                twa_center_deg=twa,
                sample_count=cell_data.get("sample_count", 0),
                bsp_percentile=cell_data.get("bsp_percentile_ms"),
                bsp_smoothed=cell_data.get("bsp_smoothed_ms"),
            )
            table.cells[(tws, twa)] = cell

        return table
