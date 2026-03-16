"""Polar Analyzer — main entry point.

Modes:
  live    — Connect to SignalK, record, compute, publish (default)
  inspect — Discover available SignalK paths
  replay  — Feed recorded JSONL through the pipeline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path

from aiohttp import web

from config import VERSION, Config
from models import InstantSample, PerformanceMetrics, SignalKUpdate
from performance_calc import PerformanceCalc
from polar_engine import PolarEngine
from polar_store import PolarStore
from recorder import Recorder
from sample_filter import SampleFilter
from sea_state_classifier import SeaStateClassifier
from signalk_auth import SignalKAuth
from signalk_client import SignalKClient
from signalk_publisher import SignalKPublisher
from state_store import StateStore
from trip_manager import TripManager
from web_server import WebServer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async tasks
# ---------------------------------------------------------------------------


async def ingest_task(
    queue: asyncio.Queue[SignalKUpdate],
    store: StateStore,
) -> None:
    """Read SignalK updates from queue and apply to state store."""
    while True:
        update = await queue.get()
        store.apply(update)


async def sampler_task(
    config: Config,
    store: StateStore,
    sample_filter: SampleFilter,
    recorder: Recorder,
    engine: PolarEngine,
    trip_manager: TripManager,
) -> None:
    """Produce InstantSamples at configured rate, filter, and record."""
    interval = 1.0 / config.sample_rate_hz
    total = 0
    valid = 0

    while True:
        await asyncio.sleep(interval)

        sample = store.snapshot()
        total += 1

        # Always record raw data
        recorder.record(sample)

        # Filter for steady-state sailing
        result = sample_filter.process(sample)

        if result.passed and result.valid_sample is not None:
            valid += 1
            # Feed valid sample to polar engine
            engine.add_sample(result.valid_sample)
            # Feed to trip manager (adds to trip engine if trip is active)
            trip_manager.add_sample(result.valid_sample)

        # Periodic stats logging
        if total % 60 == 0:
            pct = (valid / total * 100) if total > 0 else 0
            logger.info(
                "Sampler: %d total, %d valid (%.1f%%), propulsion=%s",
                total,
                valid,
                pct,
                result.propulsion_mode.value,
            )


async def auth_task(
    config: Config,
    auth: SignalKAuth,
    client: SignalKClient,
    publisher: SignalKPublisher,
    auth_ready: asyncio.Event,
) -> None:
    """Authenticate with SignalK and configure client + publisher."""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            token = await auth.authenticate()
            client.set_auth_token(token)
            publisher.set_send_fn(client.send)
            auth_ready.set()
            logger.info("Authentication complete")

            # Send meta deltas now that we're authenticated
            await asyncio.sleep(2)  # Wait for reconnect
            await publisher.publish_meta()
            return
        except Exception as exc:
            logger.warning(
                "Auth attempt %d/%d failed: %s",
                attempt + 1,
                max_retries,
                exc,
            )
            await asyncio.sleep(10)

    logger.error("Authentication failed after %d attempts", max_retries)
    # Still allow sampling/recording without publishing
    auth_ready.set()


async def publisher_task(
    config: Config,
    publisher: SignalKPublisher,
    perf_calc: PerformanceCalc,
    store: StateStore,
    web_server: WebServer,
    auth_ready: asyncio.Event,
) -> None:
    """Periodically compute performance metrics and publish to SignalK + SSE."""
    await auth_ready.wait()
    logger.info("Publisher ready — computing performance metrics")

    while True:
        await asyncio.sleep(config.publish_interval_s)

        try:
            # Get current instrument snapshot
            sample = store.snapshot()

            # Compute performance metrics
            metrics = perf_calc.compute(sample)
            if metrics is None:
                continue

            # Publish to SignalK
            await publisher.publish_performance(metrics)

            # Update web server cache and broadcast via SSE
            web_server.update_metrics(metrics)
            await web_server.broadcast_sse(_metrics_to_sse(metrics, sample))

        except Exception as exc:
            logger.debug("Publisher cycle error: %s", exc)


async def polar_maintenance_task(
    config: Config,
    engine: PolarEngine,
    polar_store: PolarStore,
) -> None:
    """Periodically recompute polars and save to disk."""
    # Wait a bit before first maintenance cycle
    await asyncio.sleep(60)

    while True:
        try:
            # Recompute percentiles and spline smoothing
            engine.recompute()

            # Save master polar periodically
            polar_store.save(engine.master)
            logger.info(
                "Polar maintenance: %d valid cells, version %d",
                sum(1 for c in engine.master.cells.values() if c.is_valid),
                engine.master.version,
            )
        except Exception as exc:
            logger.warning("Polar maintenance error: %s", exc)

        # Run every 5 minutes
        await asyncio.sleep(300)


async def flusher_task(recorder: Recorder) -> None:
    """Periodically flush the recorder buffer."""
    while True:
        await asyncio.sleep(30)
        recorder.flush()


async def console_task(
    config: Config,
    store: StateStore,
    sample_filter: SampleFilter,
    engine: PolarEngine,
    perf_calc: PerformanceCalc,
) -> None:
    """Print periodic status summary to console."""
    while True:
        await asyncio.sleep(10)

        # Build a compact status line
        snap = store.snapshot()
        tws_kt = f"{snap.tws * 1.94384:.1f}" if snap.tws else "---"
        twa_deg = f"{abs(snap.twa) * 57.2958:.0f}" if snap.twa else "---"
        bsp_kt = f"{snap.bsp * 1.94384:.1f}" if snap.bsp else "---"
        sog_kt = f"{snap.sog * 1.94384:.1f}" if snap.sog else "---"
        wave = f"{snap.wave_height:.1f}m" if snap.wave_height else "---"
        prop = sample_filter.propulsion_override.value

        side = ""
        if snap.twa is not None:
            side = "P" if snap.twa < 0 else "S"

        # Polar stats
        valid_cells = sum(1 for c in engine.master.cells.values() if c.is_valid)
        session_samples = engine._session_sample_count

        # Performance
        metrics = perf_calc.compute(snap)
        perf_str = "---"
        if metrics and metrics.polar_speed_ratio is not None:
            perf_str = f"{metrics.polar_speed_ratio * 100:.0f}%"

        logger.info(
            "TWS=%skt TWA=%s%s BSP=%skt SOG=%skt Waves=%s Mode=%s "
            "Perf=%s Cells=%d Sess=%d Updates=%d",
            tws_kt,
            twa_deg,
            side,
            bsp_kt,
            sog_kt,
            wave,
            prop,
            perf_str,
            valid_cells,
            session_samples,
            store.update_count,
        )


def _metrics_to_sse(metrics: PerformanceMetrics, sample: InstantSample) -> dict:
    """Convert performance metrics + instrument data to SSE payload."""
    from models import MS_TO_KT, RAD_TO_DEG

    data: dict = {
        "type": "performance",
        "timestamp": metrics.timestamp,
        # Instruments
        "tws_kt": round(sample.tws * MS_TO_KT, 1) if sample.tws else None,
        "twa_deg": round(abs(sample.twa) * RAD_TO_DEG, 1) if sample.twa else None,
        "twa_side": ("port" if sample.twa < 0 else "starboard") if sample.twa else None,
        "bsp_kt": round(sample.bsp * MS_TO_KT, 1) if sample.bsp else None,
        "sog_kt": round(sample.sog * MS_TO_KT, 1) if sample.sog else None,
        # Performance
        "polar_speed_kt": round(metrics.polar_speed * MS_TO_KT, 2) if metrics.polar_speed else None,
        "polar_speed_ratio": round(metrics.polar_speed_ratio, 3)
        if metrics.polar_speed_ratio
        else None,
        "vmg_kt": round(metrics.vmg * MS_TO_KT, 2) if metrics.vmg else None,
        "beat_angle_deg": round(metrics.beat_angle * RAD_TO_DEG, 1) if metrics.beat_angle else None,
        "gybe_angle_deg": round(metrics.gybe_angle * RAD_TO_DEG, 1) if metrics.gybe_angle else None,
        "target_angle_deg": round(metrics.target_angle * RAD_TO_DEG, 1)
        if metrics.target_angle
        else None,
        "target_speed_kt": round(metrics.target_speed * MS_TO_KT, 2)
        if metrics.target_speed
        else None,
    }
    return data


# ---------------------------------------------------------------------------
# Main modes
# ---------------------------------------------------------------------------


async def live_mode(config: Config) -> None:
    """Connect to SignalK, record data, compute polars, serve web UI."""
    logger.info(
        "Starting Polar Analyzer v%s (data format v%s) in live mode", config.app_version, VERSION
    )
    logger.info("SignalK: %s", config.signalk_url)
    logger.info("Data dir: %s", config.data_dir)
    logger.info("Web port: %d", config.web_port)

    # Ensure data directory exists
    Path(config.data_dir).mkdir(parents=True, exist_ok=True)

    # --- Phase 1 components ---
    queue: asyncio.Queue[SignalKUpdate] = asyncio.Queue(maxsize=config.queue_maxsize)
    state_store = StateStore(config)
    sample_filter_inst = SampleFilter(config)
    recorder = Recorder(config)
    client = SignalKClient(config, queue)
    auth = SignalKAuth(config)
    publisher = SignalKPublisher(source_label=config.source_label)
    auth_ready = asyncio.Event()

    # --- Phase 2 components ---
    engine = PolarEngine(config)
    polar_store = PolarStore(config)
    _sea_state = SeaStateClassifier(config)  # will be used for sea-state-aware polars

    # Load existing polar data from disk
    existing_polar = polar_store.load()
    if existing_polar is not None:
        engine.set_master(existing_polar)
        logger.info("Loaded existing polar: %d cells", len(existing_polar.cells))
    else:
        logger.info("Starting with empty polar table")

    # --- Phase 3 components ---
    perf_calc = PerformanceCalc(config, engine)
    trip_manager = TripManager(config, engine, polar_store)

    # --- Phase 4 components ---
    web_server = WebServer(
        config,
        engine,
        polar_store,
        perf_calc,
        trip_manager,
        state_store,
        sample_filter_inst,
    )

    # Launch tasks
    tasks = [
        asyncio.create_task(client.run(), name="signalk_client"),
        asyncio.create_task(ingest_task(queue, state_store), name="ingest"),
        asyncio.create_task(
            sampler_task(
                config,
                state_store,
                sample_filter_inst,
                recorder,
                engine,
                trip_manager,
            ),
            name="sampler",
        ),
        asyncio.create_task(
            auth_task(config, auth, client, publisher, auth_ready),
            name="auth",
        ),
        asyncio.create_task(
            publisher_task(
                config,
                publisher,
                perf_calc,
                state_store,
                web_server,
                auth_ready,
            ),
            name="publisher",
        ),
        asyncio.create_task(
            polar_maintenance_task(config, engine, polar_store),
            name="polar_maintenance",
        ),
        asyncio.create_task(flusher_task(recorder), name="flusher"),
        asyncio.create_task(
            console_task(config, state_store, sample_filter_inst, engine, perf_calc),
            name="console",
        ),
    ]

    # Start web server
    runner = web.AppRunner(web_server.app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.web_port)
    await site.start()
    logger.info("Web server started on http://0.0.0.0:%d", config.web_port)

    logger.info("Launched %d tasks + web server", len(tasks))

    # Wait for shutdown signal
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await stop.wait()

    # Graceful shutdown
    logger.info("Shutting down...")

    # End active trip if any
    if trip_manager.active_trip is not None:
        trip_manager.end_trip()
        logger.info("Ended active trip on shutdown")

    # Save polars before exit
    try:
        engine.recompute()
        polar_store.save(engine.master)
        logger.info("Saved polar data on shutdown")
    except Exception as exc:
        logger.warning("Failed to save polars on shutdown: %s", exc)

    # Stop web server
    await runner.cleanup()

    # Cancel async tasks
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    await client.close()
    recorder.close()
    logger.info("Polar Analyzer stopped")


async def inspect_mode(config: Config) -> None:
    """Connect to SignalK and list all available paths."""
    logger.info("Inspecting SignalK at %s", config.signalk_url)

    # Use subscribe=all to discover paths
    inspect_url = config.signalk_url.replace("subscribe=none", "subscribe=all")

    paths_seen: dict[str, str] = {}

    # Collect for a few seconds
    import aiohttp

    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(inspect_url, heartbeat=30)

        # Read hello
        hello = await ws.receive_json()
        print(f"Server: {hello.get('name')} v{hello.get('version')}")
        print(f"Self: {hello.get('self')}")
        print()

        # Collect paths for 5 seconds
        end_time = time.time() + 5
        while time.time() < end_time:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    for update in data.get("updates", []):
                        source = update.get("source", {})
                        label = source.get("label", "") if isinstance(source, dict) else ""
                        for val in update.get("values", []):
                            path = val.get("path", "")
                            value = val.get("value")
                            if path:
                                paths_seen[path] = f"{value} (source: {label})"
            except TimeoutError:
                continue

        await ws.close()

    # Print results
    print(f"Found {len(paths_seen)} paths:")
    print("-" * 80)
    for path in sorted(paths_seen.keys()):
        print(f"  {path}: {paths_seen[path]}")


async def replay_mode(config: Config, replay_file: str) -> None:
    """Feed recorded JSONL data through the pipeline for testing.

    Reads InstantSample records from a JSONL file and runs them through
    the filter → polar engine → performance calc pipeline.
    """
    logger.info("Starting replay from %s", replay_file)

    replay_path = Path(replay_file)
    if not replay_path.exists():
        logger.error("Replay file not found: %s", replay_file)
        return

    # Ensure data directory exists
    Path(config.data_dir).mkdir(parents=True, exist_ok=True)

    # Components (no SignalK connection needed)
    sample_filter_inst = SampleFilter(config)
    engine = PolarEngine(config)
    polar_store = PolarStore(config)
    trip_manager = TripManager(config, engine, polar_store)

    # Load existing polar
    existing = polar_store.load()
    if existing is not None:
        engine.set_master(existing)

    # Process file
    total = 0
    valid = 0

    with open(replay_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                sample = InstantSample(
                    timestamp=data.get("timestamp", time.time()),
                    tws=data.get("tws"),
                    twa=data.get("twa"),
                    aws=data.get("aws"),
                    awa=data.get("awa"),
                    bsp=data.get("bsp"),
                    sog=data.get("sog"),
                    cog=data.get("cog"),
                    heading=data.get("heading"),
                    rot=data.get("rot"),
                    pitch=data.get("pitch"),
                    roll=data.get("roll"),
                    latitude=data.get("latitude"),
                    longitude=data.get("longitude"),
                    rudder_angle=data.get("rudder_angle"),
                    current_drift=data.get("current_drift"),
                    current_set=data.get("current_set"),
                    wave_height=data.get("wave_height"),
                    wave_period=data.get("wave_period"),
                    depth=data.get("depth"),
                    engine_rpm=data.get("engine_rpm"),
                    engine_state=data.get("engine_state"),
                )

                total += 1
                result = sample_filter_inst.process(sample)

                if result.passed and result.valid_sample is not None:
                    valid += 1
                    engine.add_sample(result.valid_sample)
                    trip_manager.add_sample(result.valid_sample)

                if total % 1000 == 0:
                    pct = (valid / total * 100) if total > 0 else 0
                    logger.info("Processed %d samples, %d valid (%.1f%%)", total, valid, pct)

            except (json.JSONDecodeError, KeyError) as exc:
                logger.debug("Skipping malformed line: %s", exc)
                continue

    # Final computation
    engine.recompute()
    polar_store.save(engine.master)

    valid_cells = sum(1 for c in engine.master.cells.values() if c.is_valid)
    pct = (valid / total * 100) if total > 0 else 0
    logger.info(
        "Replay complete: %d total, %d valid (%.1f%%), %d polar cells",
        total,
        valid,
        pct,
        valid_cells,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Polar Analyzer — learns boat polars from SignalK data"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="live",
        choices=["live", "inspect", "replay"],
        help="Run mode (default: live)",
    )
    parser.add_argument(
        "--url",
        help="SignalK WebSocket URL",
    )
    parser.add_argument(
        "--http-url",
        help="SignalK HTTP URL",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Web server port",
    )
    parser.add_argument(
        "--data-dir",
        help="Data directory",
    )
    parser.add_argument(
        "--replay-file",
        help="JSONL file to replay (required for replay mode)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Config
    config = Config.from_env()
    if args.url:
        config.signalk_url = args.url
    if args.http_url:
        config.signalk_http_url = args.http_url
    if args.port:
        config.web_port = args.port
    if args.data_dir:
        config.data_dir = args.data_dir

    # Derive HTTP URL from WS URL if not explicitly set
    if args.url and not args.http_url:
        ws_url = args.url
        http_url = (
            ws_url.split("/signalk")[0].replace("ws://", "http://").replace("wss://", "https://")
        )
        config.signalk_http_url = http_url

    # Run
    if args.mode == "live":
        asyncio.run(live_mode(config))
    elif args.mode == "inspect":
        asyncio.run(inspect_mode(config))
    elif args.mode == "replay":
        if not args.replay_file:
            print("Error: --replay-file is required for replay mode")
            sys.exit(1)
        asyncio.run(replay_mode(config, args.replay_file))


if __name__ == "__main__":
    main()
