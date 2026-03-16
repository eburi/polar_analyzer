/**
 * Polar Analyzer — Web UI
 *
 * SSE client for real-time updates, Plotly.js polar charts, API integration.
 * Vanilla JS — no build step, runs on resource-constrained devices.
 */

// ---------------------------------------------------------------------------
// Base path — works both standalone (/) and behind HA ingress (/api/hassio_ingress/<token>/)
// ---------------------------------------------------------------------------

const BASE = (() => {
    // Ensure the base path ends with '/' so relative URL concatenation works
    let path = window.location.pathname;
    if (!path.endsWith('/')) path += '/';
    return path;
})();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
    connected: false,
    sse: null,
    statusTimer: null,
    currentPanel: 'dashboard',
    polarPlotInitialized: false,
    miniPolarInitialized: false,
    currentTripId: null,
};

// TWS bin colors for polar curves
const TWS_COLORS = {
    '4':  '#9ecae1', '6':  '#6baed6', '8':  '#4292c6',
    '10': '#2171b5', '12': '#08519c', '14': '#08306b',
    '16': '#d94701', '18': '#e6550d', '20': '#fd8d3c',
    '25': '#f03b20', '30': '#bd0026',
};

// Plotly dark layout base
const DARK_LAYOUT = {
    paper_bgcolor: '#16213e',
    plot_bgcolor: '#16213e',
    font: { color: '#e0e0e0', family: 'Segoe UI, system-ui, sans-serif', size: 11 },
    margin: { t: 30, r: 30, b: 30, l: 30 },
};

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    initNav();
    initPropulsionButtons();
    initTripControls();
    initAdminButtons();
    initPolarControls();
    connectSSE();
    fetchStatus();
    state.statusTimer = setInterval(fetchStatus, 5000);
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

function initNav() {
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const panel = btn.dataset.panel;
            switchPanel(panel);
        });
    });
}

function switchPanel(name) {
    state.currentPanel = name;
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelector(`.nav-btn[data-panel="${name}"]`).classList.add('active');
    document.getElementById(`panel-${name}`).classList.add('active');

    // Load data when switching panels
    if (name === 'polar') loadPolarPlot();
    if (name === 'trips') loadTrips();
    if (name === 'admin') loadAdminData();
}

// ---------------------------------------------------------------------------
// SSE — Server-Sent Events
// ---------------------------------------------------------------------------

function connectSSE() {
    if (state.sse) {
        state.sse.close();
    }

    state.sse = new EventSource(BASE + 'api/events');

    state.sse.onopen = () => {
        state.connected = true;
        updateConnectionStatus(true);
    };

    state.sse.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleSSEData(data);
        } catch (e) {
            // Ignore parse errors (keepalive comments)
        }
    };

    state.sse.onerror = () => {
        state.connected = false;
        updateConnectionStatus(false);
        // EventSource auto-reconnects
    };
}

function handleSSEData(data) {
    if (data.type === 'performance') {
        updateDashboardGauges(data);
        if (state.currentPanel === 'dashboard') {
            updateMiniPolar(data);
        }
    }
}

function updateConnectionStatus(connected) {
    const el = document.getElementById('connection-status');
    if (connected) {
        el.textContent = 'Connected';
        el.className = 'status connected';
    } else {
        el.textContent = 'Disconnected';
        el.className = 'status disconnected';
    }
}

// ---------------------------------------------------------------------------
// Dashboard — Gauges
// ---------------------------------------------------------------------------

function updateDashboardGauges(data) {
    setText('g-tws', fmt(data.tws_kt, 1));
    setText('g-bsp', fmt(data.bsp_kt, 1));
    setText('g-sog', fmt(data.sog_kt, 1));
    setText('g-polar-speed', fmt(data.polar_speed_kt, 1));
    setText('g-vmg', fmt(data.vmg_kt, 1));

    // TWA with side indicator
    if (data.twa_deg != null) {
        const side = data.twa_side === 'port' ? 'P' : 'S';
        setText('g-twa', `${data.twa_deg.toFixed(0)}${side}`);
    } else {
        setText('g-twa', '---');
    }

    // Performance ratio with color coding
    const perfEl = document.getElementById('g-perf');
    if (data.polar_speed_ratio != null) {
        const pct = (data.polar_speed_ratio * 100).toFixed(0);
        perfEl.textContent = pct + '%';
        perfEl.className = 'gauge-value large ' + perfClass(data.polar_speed_ratio);
    } else {
        perfEl.textContent = '---';
        perfEl.className = 'gauge-value large';
    }

    // Target angle
    if (data.target_angle_deg != null) {
        setText('g-target-angle', data.target_angle_deg.toFixed(0) + '\u00B0');
    } else {
        setText('g-target-angle', '---');
    }

    // Optimal angles
    setText('g-beat-angle', data.beat_angle_deg != null ? data.beat_angle_deg.toFixed(0) + '\u00B0' : '---');
    setText('g-beat-vmg', data.beat_angle_deg != null ? fmt(data.vmg_kt, 1) + ' kt' : '--- kt');
    setText('g-gybe-angle', data.gybe_angle_deg != null ? data.gybe_angle_deg.toFixed(0) + '\u00B0' : '---');
    setText('g-gybe-vmg', data.gybe_angle_deg != null ? fmt(data.vmg_kt, 1) + ' kt' : '--- kt');
}

function perfClass(ratio) {
    if (ratio < 0.7) return 'perf-low';
    if (ratio < 0.9) return 'perf-mid';
    if (ratio < 1.05) return 'perf-high';
    return 'perf-great';
}

// ---------------------------------------------------------------------------
// Dashboard — Mini Polar (current position on polar)
// ---------------------------------------------------------------------------

function updateMiniPolar(data) {
    if (data.twa_deg == null || data.bsp_kt == null) return;

    // Fetch current polar curves and overlay current position
    fetch(BASE + 'api/polar/curves')
        .then(r => r.json())
        .then(curves => {
            renderMiniPolar(curves, data);
        })
        .catch(() => {});
}

function renderMiniPolar(curvesData, liveData) {
    const traces = [];

    // Polar curves for each TWS
    const bins = curvesData.tws_bins || [];
    const curves = curvesData.curves || {};

    for (const tws of bins) {
        const key = String(tws);
        const curve = curves[key];
        if (!curve) continue;

        traces.push({
            type: 'scatterpolar',
            mode: 'lines',
            r: curve.bsp_kt,
            theta: curve.twa_deg,
            name: tws + ' kt',
            line: { color: TWS_COLORS[key] || '#888', width: 1.5 },
            hovertemplate: 'TWA: %{theta}\u00B0<br>BSP: %{r:.1f} kt<extra>' + tws + ' kt TWS</extra>',
        });
    }

    // Current boat position
    if (liveData.twa_deg != null && liveData.bsp_kt != null) {
        traces.push({
            type: 'scatterpolar',
            mode: 'markers',
            r: [liveData.bsp_kt],
            theta: [liveData.twa_deg],
            name: 'Current',
            marker: { color: '#f0f040', size: 12, symbol: 'diamond' },
            hovertemplate: 'TWA: %{theta:.0f}\u00B0<br>BSP: %{r:.1f} kt<extra>Current</extra>',
        });
    }

    // Target position
    if (liveData.target_angle_deg != null && liveData.polar_speed_kt != null) {
        traces.push({
            type: 'scatterpolar',
            mode: 'markers',
            r: [liveData.polar_speed_kt],
            theta: [liveData.target_angle_deg],
            name: 'Target',
            marker: { color: '#14d9c4', size: 10, symbol: 'cross' },
            hovertemplate: 'TWA: %{theta:.0f}\u00B0<br>BSP: %{r:.1f} kt<extra>Target</extra>',
        });
    }

    const layout = {
        ...DARK_LAYOUT,
        showlegend: false,
        margin: { t: 10, r: 20, b: 10, l: 20 },
        polar: {
            bgcolor: '#16213e',
            angularaxis: {
                direction: 'clockwise',
                rotation: 90,
                dtick: 30,
                gridcolor: '#2a3a5c',
                linecolor: '#2a3a5c',
                tickfont: { size: 9 },
                range: [0, 180],
            },
            radialaxis: {
                gridcolor: '#2a3a5c',
                linecolor: '#2a3a5c',
                tickfont: { size: 9 },
                ticksuffix: ' kt',
            },
        },
    };

    const config = { responsive: true, displayModeBar: false };

    if (!state.miniPolarInitialized) {
        Plotly.newPlot('mini-polar-plot', traces, layout, config);
        state.miniPolarInitialized = true;
    } else {
        Plotly.react('mini-polar-plot', traces, layout, config);
    }
}

// ---------------------------------------------------------------------------
// Polar Diagram Panel
// ---------------------------------------------------------------------------

function initPolarControls() {
    document.getElementById('polar-refresh-btn').addEventListener('click', loadPolarPlot);
    document.getElementById('polar-table-select').addEventListener('change', loadPolarPlot);
    document.getElementById('polar-sea-state-select').addEventListener('change', loadPolarPlot);
    document.getElementById('polar-view-select').addEventListener('change', loadPolarPlot);
}

function loadPolarPlot() {
    const tableType = document.getElementById('polar-table-select').value;
    const seaState = document.getElementById('polar-sea-state-select').value;
    const viewType = document.getElementById('polar-view-select').value;

    let url;
    if (seaState !== 'all') {
        url = `${BASE}api/polar/sea-state/${seaState}`;
    } else {
        url = `${BASE}api/polar/curves?table=${tableType}`;
    }

    if (viewType === 'density') {
        loadDensityPlot();
        return;
    }

    fetch(url)
        .then(r => r.json())
        .then(data => {
            if (viewType === 'polar') {
                renderPolarPlot(data);
            } else {
                renderCartesianPlot(data);
            }
        })
        .catch(err => console.error('Failed to load polar:', err));
}

function renderPolarPlot(curvesData) {
    const traces = [];
    const bins = curvesData.tws_bins || [];
    const curves = curvesData.curves || {};

    for (const tws of bins) {
        const key = String(tws);
        const curve = curves[key];
        if (!curve || curve.bsp_kt.length === 0) continue;

        traces.push({
            type: 'scatterpolar',
            mode: 'lines+markers',
            r: curve.bsp_kt,
            theta: curve.twa_deg,
            name: tws + ' kt TWS',
            line: { color: TWS_COLORS[key] || '#888', width: 2 },
            marker: { size: 3 },
            hovertemplate: 'TWA: %{theta}\u00B0<br>BSP: %{r:.1f} kt<extra>' + tws + ' kt TWS</extra>',
        });
    }

    if (traces.length === 0) {
        traces.push({
            type: 'scatterpolar',
            mode: 'text',
            r: [5],
            theta: [90],
            text: ['No polar data yet'],
            textfont: { color: '#8892a4', size: 14 },
            showlegend: false,
        });
    }

    const layout = {
        ...DARK_LAYOUT,
        showlegend: true,
        legend: {
            x: 1.05, y: 1,
            bgcolor: 'rgba(22, 33, 62, 0.8)',
            bordercolor: '#2a3a5c',
            borderwidth: 1,
            font: { size: 11 },
        },
        polar: {
            bgcolor: '#16213e',
            angularaxis: {
                direction: 'clockwise',
                rotation: 90,
                dtick: 15,
                gridcolor: '#2a3a5c',
                linecolor: '#2a3a5c',
                range: [0, 180],
                ticksuffix: '\u00B0',
            },
            radialaxis: {
                gridcolor: '#2a3a5c',
                linecolor: '#2a3a5c',
                ticksuffix: ' kt',
                angle: 90,
            },
        },
    };

    Plotly.react('polar-plot', traces, layout, { responsive: true });
    state.polarPlotInitialized = true;
}

function renderCartesianPlot(curvesData) {
    const traces = [];
    const bins = curvesData.tws_bins || [];
    const curves = curvesData.curves || {};

    for (const tws of bins) {
        const key = String(tws);
        const curve = curves[key];
        if (!curve || curve.bsp_kt.length === 0) continue;

        traces.push({
            type: 'scatter',
            mode: 'lines+markers',
            x: curve.twa_deg,
            y: curve.bsp_kt,
            name: tws + ' kt TWS',
            line: { color: TWS_COLORS[key] || '#888', width: 2 },
            marker: { size: 4 },
        });
    }

    const layout = {
        ...DARK_LAYOUT,
        showlegend: true,
        legend: { x: 1.02, y: 1, bgcolor: 'rgba(22, 33, 62, 0.8)' },
        xaxis: {
            title: 'TWA (\u00B0)',
            gridcolor: '#2a3a5c',
            zerolinecolor: '#2a3a5c',
            range: [25, 185],
        },
        yaxis: {
            title: 'BSP (kt)',
            gridcolor: '#2a3a5c',
            zerolinecolor: '#2a3a5c',
        },
    };

    Plotly.react('polar-plot', traces, layout, { responsive: true });
}

function loadDensityPlot() {
    fetch(BASE + 'api/polar/density')
        .then(r => r.json())
        .then(data => renderDensityPlot(data))
        .catch(err => console.error('Failed to load density:', err));
}

function renderDensityPlot(data) {
    if (!data.tws || data.tws.length === 0) {
        Plotly.react('polar-plot', [], { ...DARK_LAYOUT, title: 'No data' }, { responsive: true });
        return;
    }

    const trace = {
        type: 'heatmap',
        x: data.twa,
        y: data.tws,
        z: data.count,
        colorscale: 'Viridis',
        colorbar: { title: 'Samples', tickfont: { color: '#e0e0e0' } },
        hovertemplate: 'TWA: %{x}\u00B0<br>TWS: %{y} kt<br>Samples: %{z}<extra></extra>',
    };

    const layout = {
        ...DARK_LAYOUT,
        xaxis: {
            title: 'TWA (\u00B0)',
            gridcolor: '#2a3a5c',
        },
        yaxis: {
            title: 'TWS (kt)',
            gridcolor: '#2a3a5c',
        },
    };

    Plotly.react('polar-plot', [trace], layout, { responsive: true });
}

// ---------------------------------------------------------------------------
// Propulsion Override
// ---------------------------------------------------------------------------

function initPropulsionButtons() {
    document.querySelectorAll('.prop-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const mode = btn.dataset.mode;
            setPropulsionOverride(mode);
        });
    });
}

function setPropulsionOverride(mode) {
    fetch(BASE + 'api/propulsion/override', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            updatePropulsionUI(data.mode);
        }
    })
    .catch(err => console.error('Failed to set propulsion:', err));
}

function updatePropulsionUI(mode) {
    document.querySelectorAll('.prop-btn').forEach(b => b.classList.remove('active'));
    const active = document.querySelector(`.prop-btn[data-mode="${mode}"]`);
    if (active) active.classList.add('active');
    setText('prop-mode', mode);
}

// ---------------------------------------------------------------------------
// Trips
// ---------------------------------------------------------------------------

function initTripControls() {
    document.getElementById('trip-start-btn').addEventListener('click', startTrip);
    document.getElementById('trip-end-btn').addEventListener('click', endTrip);
}

function startTrip() {
    const name = document.getElementById('trip-name').value || undefined;
    const notes = document.getElementById('trip-notes').value || '';

    fetch(BASE + 'api/trips', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, notes }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            document.getElementById('trip-start-btn').disabled = true;
            document.getElementById('trip-end-btn').disabled = false;
            state.currentTripId = data.trip_id;
            showTripBanner(data.name);
            loadTrips();
        }
    })
    .catch(err => console.error('Failed to start trip:', err));
}

function endTrip() {
    fetch(BASE + 'api/trips/end', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            document.getElementById('trip-start-btn').disabled = false;
            document.getElementById('trip-end-btn').disabled = true;
            state.currentTripId = null;
            hideTripBanner();
            loadTrips();
        }
    })
    .catch(err => console.error('Failed to end trip:', err));
}

function loadTrips() {
    fetch(BASE + 'api/trips')
    .then(r => r.json())
    .then(data => {
        renderTripsTable(data.trips || []);
        if (data.active_trip_id) {
            state.currentTripId = data.active_trip_id;
            document.getElementById('trip-start-btn').disabled = true;
            document.getElementById('trip-end-btn').disabled = false;
        } else {
            document.getElementById('trip-start-btn').disabled = false;
            document.getElementById('trip-end-btn').disabled = true;
        }
    })
    .catch(err => console.error('Failed to load trips:', err));
}

function renderTripsTable(trips) {
    const tbody = document.getElementById('trips-tbody');
    tbody.innerHTML = '';

    for (const trip of trips.reverse()) {
        const tr = document.createElement('tr');
        const started = trip.started_at ? new Date(trip.started_at * 1000).toLocaleString() : '---';
        const ended = trip.ended_at ? new Date(trip.ended_at * 1000).toLocaleString() : (trip.is_active ? 'Active' : '---');

        tr.innerHTML = `
            <td>${esc(trip.name || trip.trip_id)}</td>
            <td>${started}</td>
            <td>${ended}</td>
            <td>${trip.sample_count || 0}</td>
            <td>
                <button class="btn" onclick="viewTripPolar('${trip.trip_id}')">Polar</button>
                <button class="btn btn-danger" onclick="deleteTrip('${trip.trip_id}')">Delete</button>
            </td>
        `;
        tbody.appendChild(tr);
    }
}

function showTripBanner(name) {
    const banner = document.getElementById('trip-active-banner');
    banner.style.display = 'block';
    document.getElementById('trip-active-name').textContent = name;
}

function hideTripBanner() {
    document.getElementById('trip-active-banner').style.display = 'none';
}

// Global functions called from inline handlers
window.viewTripPolar = function(tripId) {
    fetch(`${BASE}api/trips/${tripId}/polar`)
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            alert(data.error);
            return;
        }
        const plotDiv = document.getElementById('trip-polar-plot');
        plotDiv.style.display = 'block';
        renderTripPolar(data);
    })
    .catch(err => console.error('Failed to load trip polar:', err));
};

window.deleteTrip = function(tripId) {
    if (!confirm('Delete this trip and its polar data?')) return;
    fetch(`${BASE}api/trips/${tripId}`, { method: 'DELETE' })
    .then(r => r.json())
    .then(() => loadTrips())
    .catch(err => console.error('Failed to delete trip:', err));
};

function renderTripPolar(data) {
    const traces = [];
    const curves = data.curves || {};

    for (const [tws, curve] of Object.entries(curves)) {
        if (curve.bsp_kt.length === 0) continue;
        traces.push({
            type: 'scatterpolar',
            mode: 'lines+markers',
            r: curve.bsp_kt,
            theta: curve.twa_deg,
            name: tws + ' kt TWS',
            line: { color: TWS_COLORS[tws] || '#888', width: 2 },
            marker: { size: 3 },
        });
    }

    const layout = {
        ...DARK_LAYOUT,
        title: `Trip Polar: ${data.trip_id}`,
        showlegend: true,
        polar: {
            bgcolor: '#16213e',
            angularaxis: {
                direction: 'clockwise',
                rotation: 90,
                dtick: 15,
                gridcolor: '#2a3a5c',
                linecolor: '#2a3a5c',
                range: [0, 180],
            },
            radialaxis: {
                gridcolor: '#2a3a5c',
                linecolor: '#2a3a5c',
                ticksuffix: ' kt',
            },
        },
    };

    Plotly.react('trip-polar-plot', traces, layout, { responsive: true });
}

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

function initAdminButtons() {
    document.getElementById('admin-recompute').addEventListener('click', () => {
        adminAction(BASE + 'api/polar/recompute', 'Polars recomputed');
    });
    document.getElementById('admin-save').addEventListener('click', () => {
        adminAction(BASE + 'api/polar/save', 'Polar saved');
    });
    document.getElementById('admin-reset').addEventListener('click', () => {
        if (!confirm('Reset master polar? Current data will be archived.')) return;
        adminAction(BASE + 'api/polar/reset', 'Master polar reset and archived');
    });
}

function adminAction(url, successMsg) {
    const result = document.getElementById('admin-result');
    result.textContent = 'Working...';

    fetch(url, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            result.textContent = successMsg + ' (v' + (data.version || data.archived_version || '?') + ')';
        } else {
            result.textContent = 'Error: ' + (data.error || 'unknown');
        }
        loadAdminData();
    })
    .catch(err => {
        result.textContent = 'Error: ' + err.message;
    });
}

function loadAdminData() {
    fetchStatus();
    loadArchives();
}

function loadArchives() {
    fetch(BASE + 'api/polar/archives')
    .then(r => r.json())
    .then(archives => {
        const tbody = document.getElementById('archives-tbody');
        tbody.innerHTML = '';
        for (const a of archives) {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${esc(a.filename || '?')}</td>
                <td>${a.version || '?'}</td>
                <td>${a.cell_count || '?'}</td>
                <td>${a.sea_state || '?'}</td>
            `;
            tbody.appendChild(tr);
        }
    })
    .catch(() => {});
}

// ---------------------------------------------------------------------------
// Status polling (fallback + stats)
// ---------------------------------------------------------------------------

function fetchStatus() {
    fetch(BASE + 'api/status')
    .then(r => r.json())
    .then(data => {
        state.connected = true;
        updateConnectionStatus(true);

        // Session stats
        setText('s-valid-cells', data.polar_valid_cells || 0);
        setText('s-session-samples', data.session_samples || 0);
        setText('s-polar-version', data.polar_version || 0);
        setText('s-updates', data.updates_received || 0);
        setText('s-active-trip', data.active_trip ? data.active_trip.name : 'None');

        // Admin stats
        setText('a-updates', data.updates_received || 0);
        setText('a-valid-cells', data.polar_valid_cells || 0);
        setText('a-polar-version', data.polar_version || 0);

        // Update propulsion UI
        if (data.propulsion_override) {
            updatePropulsionUI(data.propulsion_override);
        }

        // Update trip banner
        if (data.active_trip) {
            state.currentTripId = data.active_trip.trip_id;
            showTripBanner(data.active_trip.name);
            setText('trip-active-samples', data.active_trip.sample_count || 0);
            document.getElementById('trip-start-btn').disabled = true;
            document.getElementById('trip-end-btn').disabled = false;
        }

        // Populate dashboard gauges from status if no SSE data yet
        if (data.instruments) {
            const inst = data.instruments;
            if (inst.tws_kt != null) setText('g-tws', inst.tws_kt.toFixed(1));
            if (inst.bsp_kt != null) setText('g-bsp', inst.bsp_kt.toFixed(1));
            if (inst.sog_kt != null) setText('g-sog', inst.sog_kt.toFixed(1));
            if (inst.twa_deg != null) {
                const side = inst.twa_side === 'port' ? 'P' : 'S';
                setText('g-twa', inst.twa_deg.toFixed(0) + side);
            }
        }
    })
    .catch(() => {
        updateConnectionStatus(false);
    });
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function fmt(value, decimals) {
    if (value == null) return '---';
    return value.toFixed(decimals);
}

function esc(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
