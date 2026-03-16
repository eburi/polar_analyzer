"""Web server — aiohttp serving REST API, SSE, and static files.

Provides:
- REST API for polar data, trips, performance, admin
- Server-Sent Events for real-time performance updates
- Static file serving for the web UI
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from config import Config
from models import (
    MS_TO_KT,
    PerformanceMetrics,
    PropulsionOverride,
    RAD_TO_DEG,
)
from performance_calc import PerformanceCalc
from polar_engine import PolarEngine
from polar_store import PolarStore
from sample_filter import SampleFilter
from state_store import StateStore
from trip_manager import TripManager

logger = logging.getLogger(__name__)


class WebServer:
    """aiohttp web server for the Polar Analyzer UI."""

    def __init__(
        self,
        config: Config,
        engine: PolarEngine,
        store: PolarStore,
        perf_calc: PerformanceCalc,
        trip_manager: TripManager,
        state_store: StateStore,
        sample_filter: SampleFilter,
    ) -> None:
        self._config = config
        self._engine = engine
        self._store = store
        self._perf_calc = perf_calc
        self._trip_manager = trip_manager
        self._state_store = state_store
        self._sample_filter = sample_filter
        self._app = web.Application()
        self._sse_clients: list[web.StreamResponse] = []
        self._latest_metrics: PerformanceMetrics | None = None
        self._setup_routes()

    @property
    def app(self) -> web.Application:
        return self._app

    def update_metrics(self, metrics: PerformanceMetrics) -> None:
        """Update cached metrics (called from the performance task)."""
        self._latest_metrics = metrics

    async def broadcast_sse(self, data: dict[str, Any]) -> None:
        """Send an SSE event to all connected clients."""
        payload = f"data: {json.dumps(data, default=str)}\n\n"
        dead: list[web.StreamResponse] = []
        for client in self._sse_clients:
            try:
                await client.write(payload.encode())
            except (ConnectionResetError, ConnectionAbortedError, Exception):
                dead.append(client)
        for d in dead:
            self._sse_clients.remove(d)

    def _setup_routes(self) -> None:
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/performance", self._handle_performance)
        self._app.router.add_get("/api/polar", self._handle_polar)
        self._app.router.add_get("/api/polar/curves", self._handle_polar_curves)
        self._app.router.add_get("/api/polar/density", self._handle_polar_density)
        self._app.router.add_get("/api/polar/sea-state/{state}", self._handle_polar_sea_state)
        self._app.router.add_post("/api/polar/recompute", self._handle_polar_recompute)
        self._app.router.add_post("/api/polar/reset", self._handle_polar_reset)
        self._app.router.add_post("/api/polar/save", self._handle_polar_save)
        self._app.router.add_get("/api/polar/archives", self._handle_archives)
        self._app.router.add_get("/api/trips", self._handle_trips_list)
        self._app.router.add_post("/api/trips", self._handle_trip_start)
        self._app.router.add_post("/api/trips/end", self._handle_trip_end)
        self._app.router.add_get("/api/trips/{trip_id}", self._handle_trip_detail)
        self._app.router.add_get("/api/trips/{trip_id}/polar", self._handle_trip_polar)
        self._app.router.add_delete("/api/trips/{trip_id}", self._handle_trip_delete)
        self._app.router.add_post("/api/propulsion/override", self._handle_propulsion_override)
        self._app.router.add_get("/api/events", self._handle_sse)

        # Static files — served last (catch-all)
        static_dir = Path(self._config.web_static_dir)
        if not static_dir.is_absolute():
            # Relative to project root
            static_dir = Path(__file__).parent.parent / static_dir
        if static_dir.exists():
            self._app.router.add_get("/", self._handle_index)
            self._app.router.add_static("/", static_dir, show_index=False)
        else:
            logger.warning("Static dir not found: %s", static_dir)

    # --- Status ---

    async def _handle_status(self, request: web.Request) -> web.Response:
        snap = self._state_store.snapshot()
        active_trip = self._trip_manager.active_trip
        master = self._engine.master

        data = {
            "connected": self._state_store.update_count > 0,
            "updates_received": self._state_store.update_count,
            "polar_version": master.version,
            "polar_cells": len(master.cells),
            "polar_valid_cells": sum(1 for c in master.cells.values() if c.is_valid),
            "session_samples": self._engine._session_sample_count,
            "propulsion_override": self._sample_filter.propulsion_override.value,
            "active_trip": {
                "trip_id": active_trip.trip_id,
                "name": active_trip.name,
                "started_at": active_trip.started_at,
                "sample_count": active_trip.sample_count,
            } if active_trip else None,
            "instruments": {
                "tws_kt": round(snap.tws * MS_TO_KT, 1) if snap.tws else None,
                "twa_deg": round(abs(snap.twa) * RAD_TO_DEG, 1) if snap.twa else None,
                "twa_side": ("port" if snap.twa < 0 else "starboard") if snap.twa else None,
                "bsp_kt": round(snap.bsp * MS_TO_KT, 1) if snap.bsp else None,
                "sog_kt": round(snap.sog * MS_TO_KT, 1) if snap.sog else None,
                "wave_height_m": round(snap.wave_height, 2) if snap.wave_height else None,
            },
        }
        return web.json_response(data)

    # --- Performance ---

    async def _handle_performance(self, request: web.Request) -> web.Response:
        m = self._latest_metrics
        if m is None:
            return web.json_response({"error": "no data yet"}, status=404)

        data = {
            "timestamp": m.timestamp,
            "polar_speed_kt": round(m.polar_speed * MS_TO_KT, 2) if m.polar_speed else None,
            "polar_speed_ratio": round(m.polar_speed_ratio, 3) if m.polar_speed_ratio else None,
            "vmg_kt": round(m.vmg * MS_TO_KT, 2) if m.vmg else None,
            "beat_angle_deg": round(m.beat_angle * RAD_TO_DEG, 1) if m.beat_angle else None,
            "beat_angle_vmg_kt": round(m.beat_angle_vmg * MS_TO_KT, 2) if m.beat_angle_vmg else None,
            "beat_angle_target_kt": round(m.beat_angle_target_speed * MS_TO_KT, 2) if m.beat_angle_target_speed else None,
            "gybe_angle_deg": round(m.gybe_angle * RAD_TO_DEG, 1) if m.gybe_angle else None,
            "gybe_angle_vmg_kt": round(m.gybe_angle_vmg * MS_TO_KT, 2) if m.gybe_angle_vmg else None,
            "gybe_angle_target_kt": round(m.gybe_angle_target_speed * MS_TO_KT, 2) if m.gybe_angle_target_speed else None,
            "target_angle_deg": round(m.target_angle * RAD_TO_DEG, 1) if m.target_angle else None,
            "target_speed_kt": round(m.target_speed * MS_TO_KT, 2) if m.target_speed else None,
        }
        return web.json_response(data)

    # --- Polar data ---

    async def _handle_polar(self, request: web.Request) -> web.Response:
        master = self._engine.master
        data = PolarStore._table_to_dict(master)
        return web.json_response(data)

    async def _handle_polar_curves(self, request: web.Request) -> web.Response:
        """Get polar curves for all TWS bins — ready for Plotly."""
        table_type = request.query.get("table", "master")
        trip_id = request.query.get("trip_id")

        table = None
        if table_type == "session":
            table = self._engine.session_table
        elif table_type == "trip" and trip_id:
            table = self._trip_manager.get_trip_polar(trip_id)
        else:
            table = self._engine.master

        if table is None:
            return web.json_response({"error": "table not found"}, status=404)

        curves = {}
        for tws_kt in table.tws_bins_kt:
            curve = self._engine.get_polar_curve(tws_kt, table)
            if curve:
                curves[str(tws_kt)] = {
                    "twa_deg": [p[0] for p in curve],
                    "bsp_kt": [round(p[1], 2) for p in curve],
                }
        return web.json_response({
            "tws_bins": table.tws_bins_kt,
            "curves": curves,
        })

    async def _handle_polar_density(self, request: web.Request) -> web.Response:
        density = self._engine.get_data_density()
        return web.json_response(density)

    async def _handle_polar_sea_state(self, request: web.Request) -> web.Response:
        state_str = request.match_info["state"]
        try:
            from models import SeaState
            sea_state = SeaState(state_str)
        except ValueError:
            return web.json_response({"error": f"invalid sea state: {state_str}"}, status=400)

        table = self._engine.get_sea_state_table(sea_state)
        curves = {}
        for tws_kt in table.tws_bins_kt:
            curve = self._engine.get_polar_curve(tws_kt, table)
            if curve:
                curves[str(tws_kt)] = {
                    "twa_deg": [p[0] for p in curve],
                    "bsp_kt": [round(p[1], 2) for p in curve],
                }
        return web.json_response({"sea_state": state_str, "curves": curves})

    async def _handle_polar_recompute(self, request: web.Request) -> web.Response:
        self._engine.recompute()
        return web.json_response({"status": "ok", "version": self._engine.master.version})

    async def _handle_polar_reset(self, request: web.Request) -> web.Response:
        old = self._engine.reset_master()
        self._store.archive(old)
        return web.json_response({"status": "ok", "archived_version": old.version})

    async def _handle_polar_save(self, request: web.Request) -> web.Response:
        path = self._store.save(self._engine.master)
        return web.json_response({"status": "ok", "path": str(path)})

    async def _handle_archives(self, request: web.Request) -> web.Response:
        return web.json_response(self._store.list_archives())

    # --- Trips ---

    async def _handle_trips_list(self, request: web.Request) -> web.Response:
        trips = self._trip_manager.list_trips()
        active = self._trip_manager.active_trip
        return web.json_response({
            "trips": trips,
            "active_trip_id": active.trip_id if active else None,
        })

    async def _handle_trip_start(self, request: web.Request) -> web.Response:
        body = await request.json()
        name = body.get("name", f"Trip {time.strftime('%Y-%m-%d %H:%M')}")
        notes = body.get("notes", "")
        trip = self._trip_manager.start_trip(name, notes)
        return web.json_response({
            "status": "ok",
            "trip_id": trip.trip_id,
            "name": trip.name,
        })

    async def _handle_trip_end(self, request: web.Request) -> web.Response:
        trip = self._trip_manager.end_trip()
        if trip is None:
            return web.json_response({"error": "no active trip"}, status=400)
        return web.json_response({
            "status": "ok",
            "trip_id": trip.trip_id,
            "sample_count": trip.sample_count,
        })

    async def _handle_trip_detail(self, request: web.Request) -> web.Response:
        trip_id = request.match_info["trip_id"]
        data = self._trip_manager.get_trip(trip_id)
        if data is None:
            return web.json_response({"error": "trip not found"}, status=404)
        return web.json_response(data)

    async def _handle_trip_polar(self, request: web.Request) -> web.Response:
        trip_id = request.match_info["trip_id"]

        # If this is the active trip, compute from the live trip engine
        active = self._trip_manager.active_trip
        if active and active.trip_id == trip_id and self._trip_manager.trip_engine:
            self._trip_manager.trip_engine.recompute()
            table = self._trip_manager.trip_engine.master
        else:
            table = self._trip_manager.get_trip_polar(trip_id)

        if table is None:
            return web.json_response({"error": "no polar data for trip"}, status=404)

        curves = {}
        for tws_kt in table.tws_bins_kt:
            curve = self._engine.get_polar_curve(tws_kt, table)
            if curve:
                curves[str(tws_kt)] = {
                    "twa_deg": [p[0] for p in curve],
                    "bsp_kt": [round(p[1], 2) for p in curve],
                }
        return web.json_response({"trip_id": trip_id, "curves": curves})

    async def _handle_trip_delete(self, request: web.Request) -> web.Response:
        trip_id = request.match_info["trip_id"]
        if self._trip_manager.delete_trip(trip_id):
            return web.json_response({"status": "ok"})
        return web.json_response({"error": "trip not found"}, status=404)

    # --- Propulsion override ---

    async def _handle_propulsion_override(self, request: web.Request) -> web.Response:
        body = await request.json()
        mode = body.get("mode", "auto")
        try:
            override = PropulsionOverride(mode)
        except ValueError:
            return web.json_response(
                {"error": f"invalid mode: {mode}, must be auto/sailing/motoring"},
                status=400,
            )
        self._sample_filter.propulsion_override = override
        return web.json_response({"status": "ok", "mode": override.value})

    # --- SSE ---

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.content_type = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Connection"] = "keep-alive"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        await resp.prepare(request)

        self._sse_clients.append(resp)
        logger.info("SSE client connected (%d total)", len(self._sse_clients))

        try:
            # Keep alive until client disconnects
            while True:
                await asyncio.sleep(30)
                try:
                    await resp.write(b": keepalive\n\n")
                except (ConnectionResetError, ConnectionAbortedError):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            if resp in self._sse_clients:
                self._sse_clients.remove(resp)
            logger.info("SSE client disconnected (%d remaining)", len(self._sse_clients))

        return resp

    # --- Static ---

    async def _handle_index(self, request: web.Request) -> web.FileResponse:
        static_dir = Path(self._config.web_static_dir)
        if not static_dir.is_absolute():
            static_dir = Path(__file__).parent.parent / static_dir
        return web.FileResponse(static_dir / "index.html")
