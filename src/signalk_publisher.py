"""SignalK publisher — sends performance deltas and meta information.

Builds delta messages and sends them via the SignalK WebSocket connection.
Publishes meta deltas once on startup for dashboard display labels.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from models import PerformanceMetrics
from paths import (
    PUB_BEAT_ANGLE,
    PUB_BEAT_ANGLE_TARGET_SPEED,
    PUB_BEAT_ANGLE_VMG,
    PUB_GYBE_ANGLE,
    PUB_GYBE_ANGLE_TARGET_SPEED,
    PUB_GYBE_ANGLE_VMG,
    PUB_POLAR_SPEED,
    PUB_POLAR_SPEED_RATIO,
    PUB_TARGET_ANGLE,
    PUB_TARGET_SPEED,
    PUB_VMG,
    PUBLISH_META,
)

logger = logging.getLogger(__name__)


class SignalKPublisher:
    """Builds and sends delta messages to SignalK."""

    def __init__(self, source_label: str = "polar-analyzer") -> None:
        self._source_label = source_label
        self._send_fn: Any = None  # Will be set to client.send
        self._meta_sent = False
        self._publish_count = 0

    def set_send_fn(self, send_fn: Any) -> None:
        """Set the WebSocket send function (from SignalKClient)."""
        self._send_fn = send_fn

    async def publish_meta(self) -> None:
        """Send meta deltas for all published paths (once on startup)."""
        if self._meta_sent or self._send_fn is None:
            return

        meta_values = []
        for path, meta in PUBLISH_META.items():
            meta_values.append(
                {
                    "path": path,
                    "value": {"meta": meta},
                }
            )

        delta = {
            "context": "vessels.self",
            "updates": [
                {
                    "source": {"label": self._source_label, "type": "signalk"},
                    "timestamp": self._iso_now(),
                    "meta": [{"path": path, "value": meta} for path, meta in PUBLISH_META.items()],
                }
            ],
        }

        try:
            await self._send_fn(delta)
            self._meta_sent = True
            logger.info("Published meta deltas for %d paths", len(PUBLISH_META))
        except Exception as exc:
            logger.warning("Failed to publish meta: %s", exc)

    async def publish_performance(self, metrics: PerformanceMetrics) -> None:
        """Send a delta with current performance values."""
        if self._send_fn is None:
            return

        values = []
        path_map = {
            PUB_POLAR_SPEED: metrics.polar_speed,
            PUB_POLAR_SPEED_RATIO: metrics.polar_speed_ratio,
            PUB_VMG: metrics.vmg,
            PUB_BEAT_ANGLE: metrics.beat_angle,
            PUB_BEAT_ANGLE_VMG: metrics.beat_angle_vmg,
            PUB_BEAT_ANGLE_TARGET_SPEED: metrics.beat_angle_target_speed,
            PUB_GYBE_ANGLE: metrics.gybe_angle,
            PUB_GYBE_ANGLE_VMG: metrics.gybe_angle_vmg,
            PUB_GYBE_ANGLE_TARGET_SPEED: metrics.gybe_angle_target_speed,
            PUB_TARGET_ANGLE: metrics.target_angle,
            PUB_TARGET_SPEED: metrics.target_speed,
        }

        for path, value in path_map.items():
            if value is not None:
                values.append({"path": path, "value": value})

        if not values:
            return

        delta = {
            "context": "vessels.self",
            "updates": [
                {
                    "source": {"label": self._source_label, "type": "signalk"},
                    "timestamp": self._iso_now(),
                    "values": values,
                }
            ],
        }

        try:
            await self._send_fn(delta)
            self._publish_count += 1
            if self._publish_count <= 2:
                logger.info("Published performance delta (%d values)", len(values))
        except Exception as exc:
            logger.warning("Failed to publish performance: %s", exc)

    @property
    def publish_count(self) -> int:
        return self._publish_count

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
