# Polar Analyzer — AI Coding Instructions

## Project Overview

Polar Analyzer is a SignalK-connected application that learns boat polars from live sailing data, computes real-time performance metrics, and publishes them back to SignalK. It runs standalone on Raspbian/OpenPlotter or as a Home Assistant addon.

**Architecture reference:** Follows the same patterns as [sea_state_analyzer](https://github.com/eburi/sea_state_analyzer) — async Python, WebSocket SignalK client, device auth, delta publishing, dual deployment.

**Vessel:** 14m sailing catamaran (MMSI 538071881), 7.96m beam, 1.35m draft. Catamaran-specific: no heel correction needed, higher speed range (BSP > TWS normal when reaching), pitch more relevant than heel.

## Tech Stack

- **Python 3.11+** with asyncio
- **aiohttp** — WebSocket client + web server (single event loop)
- **numpy** — Rolling statistics, percentile computation
- **scipy** — Cubic spline interpolation for polar curves
- **Plotly.js** — Client-side interactive polar plots with real-time updates via `Plotly.react()`
- **JSON** — Polar data persistence (human-readable)
- **Parquet** — Raw sample archive (via pyarrow)

## Coding Standards

- All source code in `src/`
- Type hints on all function signatures
- Dataclasses for all data models (not dicts)
- `asyncio.create_task()` for concurrent work, never threads
- `asyncio.Queue` for producer-consumer decoupling
- Graceful degradation: missing sensors reduce features, never crash
- All SignalK values are SI units internally (m/s, radians, Kelvin); convert only at display boundaries
- Tests in `tests/` using pytest; aim for >80% coverage on core logic (filter, engine, performance calc)
- Ruff for linting and formatting

## SignalK Connection

- **Server:** primrose.local / 192.168.46.222 port 3000
- **Config:** Single `signalk_url` (e.g. `http://host:3000`); WebSocket and HTTP URLs are derived automatically
- **WebSocket:** Derived as `ws://host:3000/signalk/v1/stream?subscribe=none`, then explicit subscription
- **Auth:** Device access request protocol (separate from sea_state_analyzer, device name "Polar Analyzer")
- **Token storage:** `~/.polar_analyzer/signalk_token.json` (standalone) or `/data/signalk_token.json` (HA)
- **Publishing:** Delta messages to `vessels.self` context with source label `polar-analyzer`

## Web UI

- **Port:** 3001
- **Server:** aiohttp serving static files from `web/` + REST API + Server-Sent Events for live updates
- **Plots:** Plotly.js polar charts, updated in real-time via SSE
- **No build step:** Vanilla JS + Plotly CDN (keep it simple for resource-constrained devices)

## Key Algorithms

### Propulsion Filter (sample_filter.py — motoring detection)
The app must exclude motoring data from polar recording. Engine detection uses a layered approach since not all vessels have engine sensors on SignalK:

**Layer 1 — Direct engine data (if available):**
- Subscribe to `propulsion.*.revolutions` and `propulsion.*.state` (wildcard)
- If RPM > 0 or state != "stopped" → motoring, exclude sample
- Many vessels (including the dev vessel) have no engine gateway on N2K, so this layer is optional

**Layer 2 — Heuristic detection (always active):**
- BSP/TWS decorrelation: under sail, BSP tracks TWS changes; under motor, BSP is independent
- STW/SOG divergence with no current: if |STW - SOG| is large but current drift is near zero, suspect motoring
- Heading vs COG divergence without leeway: motor thrust eliminates leeway
- Constant BSP in variable TWS: 30s window where BSP std dev < 5% but TWS std dev > 20% → motoring

**Layer 3 — Manual override via web UI:**
- Toggle button: "Motoring" / "Sailing" / "Auto-detect"
- Default is "Auto-detect" (layers 1+2)
- Manual override persists until explicitly changed or until trip ends

**Battery charging exception:**
- During long passages, sailors run the engine in neutral solely to charge batteries
- In this mode: engine RPM > 0 BUT propeller is not engaged (no thrust)
- Detection: RPM > 0 but BSP still correlates with TWS, and no SOG/STW divergence
- When heuristic layer 2 shows sailing correlation despite engine RPM, treat as SAILING
- The manual "Sailing" override also covers this case

### Steady-State Filter (sample_filter.py)
- 60s rolling window for all statistics
- TWS std dev < 15% of mean
- TWD std dev < 10 degrees
- BSP std dev < 15% of mean
- Rate of turn < 2 deg/s
- Tack/gybe detection: heading change > 25 degrees in 10s, exclude +/- 30s around event
- Minimum thresholds: TWS > 3 knots (1.54 m/s), BSP > 0.5 knots (0.26 m/s)
- Propulsion filter must pass (not motoring) before steady-state checks apply

### Polar Binning (polar_engine.py)
- TWS bins (knots): 4, 6, 8, 10, 12, 14, 16, 18, 20, 25, 30 (2kt width to 20kt, 5kt above)
- TWA bins (degrees): 30, 35, 40, ... 175, 180 (5-degree width, 31 bins)
- Use absolute value of TWA (symmetric port/starboard)
- Minimum 20 samples per cell before valid
- 95th percentile of BSP per cell as polar value
- Cubic spline smoothing along TWA axis per TWS column
- Separate polars for sea state: Flat (<0.5m Hs), Moderate (0.5-1.5m), Rough (>1.5m)

### Performance Calculation (performance_calc.py)
- Target BSP: bilinear interpolation from polar at current TWS/TWA
- Performance ratio: BSP / target BSP
- VMG: BSP * cos(TWA)
- Optimal beat angle: TWA where upwind VMG is maximized per TWS
- Optimal gybe angle: TWA where downwind VMG is maximized per TWS
- Update at 1Hz, publish to SignalK at 5s intervals

### Incremental Updates
- EMA with alpha=0.1 to blend new data into master polar
- Session-based: accumulate during trip, merge on trip end
- Require minimum samples before allowing cell update

## SignalK Paths

### Subscribed (input)
```
environment.wind.speedTrue               1000ms
environment.wind.angleTrueWater          1000ms
environment.wind.speedApparent           1000ms
environment.wind.angleApparent           1000ms
navigation.speedThroughWater             1000ms
navigation.speedOverGround               1000ms
navigation.courseOverGroundTrue           1000ms
navigation.headingTrue                   1000ms
navigation.rateOfTurn                    1000ms
navigation.attitude                      1000ms
navigation.position                      5000ms
steering.rudderAngle                     1000ms
environment.current.drift                5000ms
environment.current.setTrue              5000ms
environment.water.waves.significantHeight 5000ms
environment.water.waves.truePeriod       5000ms
environment.water.waves.motionSeverity   5000ms
environment.depth.belowKeel              5000ms
propulsion.*.revolutions                 1000ms  (optional — absent on many vessels)
propulsion.*.state                       1000ms  (optional — absent on many vessels)
```

### Published (output)
```
performance.polarSpeed                   m/s    Target BSP from polar
performance.polarSpeedRatio              ratio  Actual/target BSP
performance.velocityMadeGood             m/s    VMG to wind
performance.beatAngle                    rad    Optimal upwind TWA
performance.beatAngleVelocityMadeGood    m/s    VMG at beat angle
performance.beatAngleTargetSpeed         m/s    BSP at beat angle
performance.gybeAngle                    rad    Optimal downwind TWA
performance.gybeAngleVelocityMadeGood    m/s    VMG at gybe angle
performance.gybeAngleTargetSpeed         m/s    BSP at gybe angle
performance.targetAngle                  rad    Current applicable target TWA
performance.targetSpeed                  m/s    Current applicable target BSP
```

## Directory Layout
```
polar_analyzer/
├── src/
│   ├── main.py                  # CLI entry: live / inspect / replay
│   ├── config.py                # Config dataclass + Config.from_env()
│   ├── paths.py                 # SignalK path constants
│   ├── models.py                # Data models (dataclasses)
│   ├── signalk_client.py        # WS client with reconnect
│   ├── signalk_auth.py          # Device registration + JWT
│   ├── signalk_publisher.py     # Delta builder + publish
│   ├── state_store.py           # Latest values + staleness
│   ├── sample_filter.py         # Steady-state validation
│   ├── polar_engine.py          # Binning, percentile, spline
│   ├── polar_store.py           # Save/load polar tables
│   ├── performance_calc.py      # Target BSP, VMG, ratio
│   ├── trip_manager.py          # Trip lifecycle
│   ├── sea_state_classifier.py  # Wave height → category
│   ├── web_server.py            # aiohttp server + REST API + SSE
│   └── recorder.py              # Raw data logging
├── web/
│   ├── index.html               # Main dashboard
│   ├── app.js                   # Plotly polar charts + SSE client
│   └── style.css                # Minimal styling
├── tests/
├── polar_analyzer/              # HA addon
│   ├── config.yaml
│   ├── Dockerfile
│   ├── run.sh
│   ├── deploy.sh
│   └── requirements.txt
├── requirements.txt
├── conftest.py
└── CLAUDE.md
```

## Implementation Phases

### Phase 1: Foundation
- Project scaffold, config, paths, requirements
- SignalK client (adapt from sea_state_analyzer)
- SignalK auth (adapt from sea_state_analyzer)
- State store with staleness tracking
- 1Hz sampler producing InstantSample
- Raw JSONL recorder

### Phase 2: Polar Engine
- Sample filter with rolling window stats + tack detection
- Polar data model with bin assignment
- 95th percentile computation + spline smoothing
- Polar persistence (JSON with versioning)
- Sea state classification for multi-dimensional polars

### Phase 3: Performance & Publishing
- Performance calculator (target BSP, VMG, ratio, optimal angles)
- SignalK publisher with meta deltas
- Trip manager (start/stop/archive)
- Incremental polar updates (EMA blending)

### Phase 4: Web Interface
- aiohttp web server on port 3001
- REST API endpoints
- SSE for live performance data
- Plotly.js polar diagrams (interactive, real-time)
- Trip management UI
- Polar admin (archive/reset/export)

### Phase 5: Deployment & Testing
- Home Assistant addon packaging
- pytest suite for core algorithms
- Replay mode for testing with recorded data
- Edge case handling

## Important Notes

- Use `environment.wind.angleTrueWater` (not `angleTrueGround`) for polars — water-referenced TWA is correct
- Use `navigation.speedThroughWater` (not SOG) for polar BSP — STW excludes current effects
- Always use absolute TWA for polar binning (polars are symmetric)
- sea_state_analyzer is already running on the same SignalK instance — wave data paths are populated
- Current drift/set is available — can be used for current-corrected performance analysis
- The `derived-data` source on SignalK computes TWS/TWA from apparent wind + STW — this is correct for polars
- Current dev vessel has NO engine gateway on N2K — propulsion detection must work without direct engine data
