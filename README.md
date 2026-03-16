# Polar Analyzer

A SignalK-connected application that **learns boat polars from live sailing data**, computes real-time performance metrics, and publishes them back to SignalK. Runs standalone on Raspbian/OpenPlotter or as a Home Assistant addon.

Designed for a 14m sailing catamaran but works with any vessel that has wind and speed instruments on SignalK.

## Features

- **Self-learning polars** — builds polar diagrams from actual sailing data using 95th percentile computation with cubic spline smoothing
- **Real-time performance metrics** — target boat speed, VMG, performance ratio, optimal beat/gybe angles published to SignalK at 5s intervals
- **3-layer propulsion filter** — automatically excludes motoring data using direct engine sensors (if available), heuristic BSP/TWS decorrelation analysis, or manual override. Handles battery-charging-under-sail correctly
- **Sea-state-aware polars** — separate polar tables for flat, moderate, and rough conditions (uses wave height data if available)
- **Trip management** — start/end trips with per-trip polar accumulation, session-to-master merge via EMA blending
- **Interactive web dashboard** — Plotly.js polar diagrams, instrument gauges, trip management, and admin controls on port 3001
- **Dual deployment** — standalone Python app or Home Assistant addon

## Quick Start

### Standalone

```bash
# Install dependencies
pip install -r requirements.txt

# Run in live mode (connects to SignalK)
python src/main.py live

# Or specify a custom SignalK server
python src/main.py live --url ws://192.168.1.100:3000/signalk/v1/stream?subscribe=none

# Inspect available SignalK paths
python src/main.py inspect

# Replay recorded data
python src/main.py replay --replay-file data/recording.jsonl
```

Open `http://localhost:3001` for the web dashboard.

### Home Assistant Addon

1. Copy the `polar_analyzer/` directory to your HA addons folder
2. In HA, go to Settings > Add-ons > Local add-ons and install "Polar Analyzer"
3. Configure the SignalK server URL in the addon options
4. Start the addon — approve the device access request in SignalK admin

## CLI Reference

```
python src/main.py [mode] [options]

Modes:
  live      Connect to SignalK, record, compute, publish (default)
  inspect   Discover available SignalK paths
  replay    Feed recorded JSONL through the pipeline

Options:
  --url URL          SignalK WebSocket URL
  --http-url URL     SignalK HTTP URL
  --port PORT        Web server port (default: 3001)
  --data-dir DIR     Data directory (default: ~/.polar_analyzer)
  --replay-file FILE JSONL file to replay (required for replay mode)
  -v, --verbose      Enable debug logging
```

## Configuration

Configuration is layered: defaults, then `POLAR_ANALYZER_*` environment variables, then CLI arguments.

| Environment Variable | Description | Default |
|---|---|---|
| `POLAR_ANALYZER_SIGNALK_URL` | WebSocket URL | `ws://primrose.local:3000/signalk/v1/stream?subscribe=none` |
| `POLAR_ANALYZER_SIGNALK_HTTP_URL` | HTTP URL | `http://primrose.local:3000` |
| `POLAR_ANALYZER_TOKEN_FILE` | Auth token path | `~/.polar_analyzer/signalk_token.json` |
| `POLAR_ANALYZER_DATA_DIR` | Data directory | `~/.polar_analyzer` |
| `POLAR_ANALYZER_WEB_PORT` | Web dashboard port | `3001` |
| `POLAR_ANALYZER_SAMPLE_RATE` | Sample rate (Hz) | `1.0` |
| `POLAR_ANALYZER_PUBLISH_INTERVAL` | SignalK publish interval (s) | `5.0` |
| `POLAR_ANALYZER_WEB_STATIC_DIR` | Web static files dir | `web` |

## SignalK Integration

### Published Paths

The app publishes these performance values to SignalK under `vessels.self`:

| Path | Units | Description |
|---|---|---|
| `performance.polarSpeed` | m/s | Target BSP from learned polar |
| `performance.polarSpeedRatio` | ratio | Actual / target BSP |
| `performance.velocityMadeGood` | m/s | VMG to wind |
| `performance.beatAngle` | rad | Optimal upwind TWA |
| `performance.beatAngleVelocityMadeGood` | m/s | VMG at beat angle |
| `performance.beatAngleTargetSpeed` | m/s | BSP at beat angle |
| `performance.gybeAngle` | rad | Optimal downwind TWA |
| `performance.gybeAngleVelocityMadeGood` | m/s | VMG at gybe angle |
| `performance.gybeAngleTargetSpeed` | m/s | BSP at gybe angle |
| `performance.targetAngle` | rad | Current applicable target TWA |
| `performance.targetSpeed` | m/s | Current applicable target BSP |

### Subscribed Paths

Wind (TWS, TWA, AWS, AWA), navigation (STW, SOG, COG, heading, ROT, attitude, position), steering (rudder angle), environment (current drift/set, wave height/period, depth), and propulsion (RPM, state — optional).

## Architecture

```
SignalK ──WS──> signalk_client ──Queue──> state_store
                                              │
                                        sampler (1Hz)
                                              │
                                        sample_filter
                                        (propulsion + steady-state)
                                              │
                                    ┌─────────┴─────────┐
                              polar_engine          trip_manager
                              (bin, percentile,     (per-trip polars)
                               spline, interpolate)
                                    │
                              performance_calc
                              (target BSP, VMG,
                               ratio, angles)
                                    │
                              signalk_publisher ──Delta──> SignalK
                                    │
                              web_server (SSE) ──────────> Browser
```

All components run as async tasks in a single event loop. Queues decouple producers from consumers. The web server provides REST API + Server-Sent Events for live updates.

## Algorithms

- **Polar binning**: TWS bins at 4-30 kt, TWA bins at 30-180 deg (5-deg steps), symmetric port/starboard
- **Polar values**: 95th percentile of BSP per cell (minimum 20 samples)
- **Smoothing**: Cubic spline along TWA axis per TWS column
- **Interpolation**: Bilinear interpolation between bins for continuous lookup
- **Incremental updates**: EMA (alpha=0.1) blending of session data into master polar
- **Steady-state filter**: 60s rolling window — TWS CV < 15%, BSP CV < 15%, TWD std < 10 deg, ROT < 2 deg/s, tack detection with exclusion zone
- **Propulsion detection**: Layer 1 (direct engine RPM/state), Layer 2 (constant BSP in variable TWS heuristic), Layer 3 (manual override)

## Web Dashboard

The dashboard at port 3001 has four panels:

- **Dashboard** — live instrument gauges (TWS, TWA, BSP, SOG), performance ratio gauge, optimal angle indicators, propulsion override toggle, mini polar plot with current position
- **Polar Diagram** — interactive Plotly.js charts (polar plot, cartesian, density heatmap) with TWS and sea state selectors
- **Trips** — start/end trips, view per-trip polars, delete old trips
- **Admin** — recompute polars, save/reset, manage archives, system info

## Data Storage

- `~/.polar_analyzer/polars.json` — master polar table (JSON with versioning)
- `~/.polar_analyzer/trips/` — per-trip data
- `~/.polar_analyzer/recordings/` — raw JSONL recordings (daily rotation)
- `~/.polar_analyzer/archives/` — archived polar snapshots

## Development

```bash
# Install dev dependencies
pip install -r requirements.txt
pip install pytest ruff

# Run tests (75 tests)
python -m pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/
```

## Requirements

- Python 3.11+
- SignalK server with wind and speed instrument data
- aiohttp, numpy, scipy, pyarrow

## License

MIT
