/**
 * SeaPath ECDIS - Interactive Navigation Interface
 * =================================================
 * Manages MapLibre GL chart plotting, vessel hydrodynamics,
 * bathymetric routing requests, and dynamic depth cross-section rendering.
 */

// -----------------------------------------------------------------------------
// MapLibre Chart Initialization
// -----------------------------------------------------------------------------
const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: [36.0, 44.8], // Regional overview (Black Sea & Crimean Fairways)
    zoom: 7.5,
    pitch: 0,
    attributionControl: false
});

// -----------------------------------------------------------------------------
// UI Element References
// -----------------------------------------------------------------------------
const draftSlider = document.getElementById('draft-slider');
const draftVal = document.getElementById('draft-val');
const speedSlider = document.getElementById('speed-slider');
const speedVal = document.getElementById('speed-val');
const ukcSlider = document.getElementById('ukc-slider');
const ukcVal = document.getElementById('ukc-val');

const dynDraftVal = document.getElementById('dynamic-draft-val');
const dangerVal = document.getElementById('danger-val');

const startCoordTxt = document.getElementById('start-coord');
const goalCoordTxt = document.getElementById('goal-coord');
const statusMsg = document.getElementById('status');
const chartPanel = document.getElementById('chart-panel');
const vesselSelect = document.getElementById('vessel-profile');

// -----------------------------------------------------------------------------
// Vessel Profiles
// -----------------------------------------------------------------------------
const VESSEL_PROFILES = {
    yacht: { draft: 1.2, speed: 10, ukc: 0.5 },
    ferry: { draft: 4.5, speed: 18, ukc: 1.0 },
    cargo: { draft: 10.0, speed: 12, ukc: 2.0 }
};

// State
let draft = parseFloat(draftSlider.value);
let speedKnots = parseFloat(speedSlider.value);
let baseUkc = parseFloat(ukcSlider.value);
let dynamicSquat = 0.0;

let startPoint = null;
let goalPoint = null;
let startMarker = null;
let goalMarker = null;

// -----------------------------------------------------------------------------
// Depth Profile Chart Setup (Chart.js)
// -----------------------------------------------------------------------------
const ctx = document.getElementById('depth-chart').getContext('2d');
Chart.defaults.color = '#94a3b8';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

const chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
        labels: [],
        datasets: [
            {
                label: 'Seafloor Bathymetry (m)',
                data: [],
                borderColor: '#38bdf8',
                backgroundColor: 'rgba(56, 189, 248, 0.15)',
                fill: true,
                tension: 0.3,
                pointRadius: 0
            },
            {
                label: 'Vessel Keel Safety Limit (Draft + Squat + UKC)',
                data: [],
                borderColor: '#ef4444',
                borderDash: [5, 5],
                borderWidth: 2,
                fill: false,
                tension: 0,
                pointRadius: 0
            }
        ]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: 'index' },
        scales: {
            y: {
                reverse: true,
                grid: { color: 'rgba(255, 255, 255, 0.05)' },
                title: { display: true, text: 'Depth (m)', color: '#94a3b8' }
            },
            x: {
                grid: { display: false },
                ticks: { maxTicksLimit: 12, color: '#94a3b8' }
            }
        },
        plugins: {
            legend: { position: 'top', align: 'end' },
            tooltip: {
                backgroundColor: 'rgba(15, 23, 42, 0.95)',
                padding: 10,
                titleFont: { size: 12 },
                bodyFont: { size: 12 }
            }
        }
    }
});

// -----------------------------------------------------------------------------
// Vessel Hydrodynamics (Barrass Formulation)
// -----------------------------------------------------------------------------
function updateVesselPhysics() {
    // Open-water Barrass squat formula: (Cb * V^2) / 100
    const blockCoefficient = 0.6;
    dynamicSquat = (blockCoefficient * Math.pow(speedKnots, 2)) / 100.0;

    const dynamicDraft = draft + dynamicSquat;
    const requiredDepth = dynamicDraft + baseUkc;

    dynDraftVal.innerText = dynamicDraft.toFixed(2) + 'm';
    dangerVal.innerText = requiredDepth.toFixed(2) + 'm';
}

// -----------------------------------------------------------------------------
// Coordinate Display Formatting (DMM Notation)
// -----------------------------------------------------------------------------
function formatCoordinates(lat, lon) {
    const toDmm = (decimal, isLat) => {
        const deg = Math.floor(Math.abs(decimal));
        const min = ((Math.abs(decimal) - deg) * 60).toFixed(1);
        const hemi = isLat ? (decimal >= 0 ? 'N' : 'S') : (decimal >= 0 ? 'E' : 'W');
        return `${deg}°${min}'${hemi}`;
    };
    return `${toDmm(lat, true)} ${toDmm(lon, false)}`;
}

// -----------------------------------------------------------------------------
// Event Listeners: Parameter Controls
// -----------------------------------------------------------------------------
if (vesselSelect) {
    vesselSelect.addEventListener('change', (e) => {
        const profile = VESSEL_PROFILES[e.target.value];
        if (profile) {
            draftSlider.max = Math.max(15.0, profile.draft * 2).toString();
            draftSlider.value = profile.draft;
            speedSlider.value = profile.speed;
            ukcSlider.value = profile.ukc;

            draft = profile.draft;
            speedKnots = profile.speed;
            baseUkc = profile.ukc;

            draftVal.innerText = draft.toFixed(1) + 'm';
            speedVal.innerText = speedKnots + ' kts';
            ukcVal.innerText = baseUkc.toFixed(1) + 'm';

            updateVesselPhysics();
            if (startPoint && goalPoint) {
                calculateRoute();
            }
        }
    });

    const markCustomProfile = () => {
        vesselSelect.value = 'custom';
    };

    draftSlider.addEventListener('input', (e) => {
        draft = parseFloat(e.target.value);
        draftVal.innerText = draft.toFixed(1) + 'm';
        markCustomProfile();
        updateVesselPhysics();
    });

    speedSlider.addEventListener('input', (e) => {
        speedKnots = parseFloat(e.target.value);
        speedVal.innerText = speedKnots + ' kts';
        markCustomProfile();
        updateVesselPhysics();
    });

    ukcSlider.addEventListener('input', (e) => {
        baseUkc = parseFloat(e.target.value);
        ukcVal.innerText = baseUkc.toFixed(1) + 'm';
        markCustomProfile();
        updateVesselPhysics();
    });
}

// -----------------------------------------------------------------------------
// Map Layer Setup
// -----------------------------------------------------------------------------
map.on('load', () => {
    // Route layer
    map.addSource('route', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'route-layer',
        type: 'line',
        source: 'route',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#10b981', 'line-width': 4, 'line-opacity': 0.9 }
    });

    // Bathymetry Heatmap Overlay (GeoTIFF EPSG:3857 bounds)
    map.addSource('depth-heatmap', {
        type: 'image',
        url: '/heatmap.png',
        coordinates: [
            [33.12291666666667, 52.502083333333324], // NW
            [43.00035105177397, 52.502083333333324], // NE
            [43.00035105177397, 43.12257235357225],   // SE
            [33.12291666666667, 43.12257235357225]    // SW
        ]
    });

    map.addLayer({
        id: 'depth-heatmap-layer',
        type: 'raster',
        source: 'depth-heatmap',
        paint: { 'raster-opacity': 0.8 }
    }, 'route-layer');

    updateVesselPhysics();
});

// -----------------------------------------------------------------------------
// Interactive Waypoint Placement
// -----------------------------------------------------------------------------
map.on('click', (e) => {
    const coords = [e.lngLat.lng, e.lngLat.lat];

    if (!startPoint) {
        startPoint = coords;
        startMarker = new maplibregl.Marker({ color: '#10b981' }).setLngLat(coords).addTo(map);
        startCoordTxt.innerText = `A: ${formatCoordinates(coords[1], coords[0])}`;
    } else if (!goalPoint) {
        goalPoint = coords;
        goalMarker = new maplibregl.Marker({ color: '#ef4444' }).setLngLat(coords).addTo(map);
        goalCoordTxt.innerText = `B: ${formatCoordinates(coords[1], coords[0])}`;
        calculateRoute();
    }
});

document.getElementById('clear-route').addEventListener('click', () => {
    if (startMarker) startMarker.remove();
    if (goalMarker) goalMarker.remove();

    startPoint = null;
    goalPoint = null;
    startMarker = null;
    goalMarker = null;

    startCoordTxt.innerText = 'A: Not set';
    goalCoordTxt.innerText = 'B: Not set';

    map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
    chartPanel.style.display = 'none';
    statusMsg.innerText = '';
});

// -----------------------------------------------------------------------------
// Route Calculation & Rendering
// -----------------------------------------------------------------------------
window.calculateRoute = async function () {
    if (!startPoint || !goalPoint) {
        statusMsg.innerText = 'Please designate departure (A) and destination (B) waypoints.';
        statusMsg.style.color = 'var(--status-warning)';
        return;
    }

    statusMsg.innerText = 'Computing passage plan...';
    statusMsg.style.color = 'var(--accent-blue)';

    try {
        const payload = {
            start_lon: startPoint[0],
            start_lat: startPoint[1],
            goal_lon: goalPoint[0],
            goal_lat: goalPoint[1],
            draft: draft,
            speed_knots: speedKnots,
            ukc: baseUkc
        };

        const res = await fetch('/api/route', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            const data = await res.json();
            map.getSource('route').setData(data);

            const p = data.properties;
            const distNm = p.distance_nm !== undefined ? p.distance_nm.toFixed(1) + ' NM' : '—';
            const eta = (p.eta_hours !== null && p.eta_hours !== undefined) ? p.eta_hours.toFixed(1) + ' h' : '— (Stationary)';
            const clearance = p.min_clearance_m !== undefined ? p.min_clearance_m.toFixed(2) + ' m' : '—';
            const waypointsCount = p.waypoints || '—';

            statusMsg.innerHTML =
                `✓ <b>${distNm}</b> &nbsp;|&nbsp; ETA <b>${eta}</b>` +
                ` &nbsp;|&nbsp; Min UKC <b>${clearance}</b>` +
                ` &nbsp;|&nbsp; ${waypointsCount} waypoints`;
            statusMsg.style.color = 'var(--status-success)';

            if (data.profile && data.profile.length) {
                renderProfileChart(data.profile, payload.draft + dynamicSquat + payload.ukc);
                chartPanel.style.display = 'block';
            }
        } else {
            const err = await res.json().catch(() => ({}));
            statusMsg.innerText = '❌ ' + (err.detail || 'No navigable passage found.');
            statusMsg.style.color = 'var(--status-danger)';
            map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
            chartPanel.style.display = 'none';
        }
    } catch (err) {
        console.error('Passage plan calculation failure:', err);
        statusMsg.innerText = 'Communication error with navigation server.';
        statusMsg.style.color = 'var(--status-danger)';
    }
};

function renderProfileChart(profile, safeDepthLimit) {
    chartInstance.data.labels = profile.map((p) => {
        const nm = (p.distance_from_start_m / 1852.0).toFixed(1);
        return `${nm} NM`;
    });

    chartInstance.data.datasets[0].data = profile.map((p) => p.depth);
    chartInstance.data.datasets[1].data = profile.map(() => safeDepthLimit);

    const validDepths = profile.map((p) => p.depth).filter((d) => d > 0);
    const maxDepth = validDepths.length ? Math.max(...validDepths) : 100;
    const minDepth = Math.max(0, safeDepthLimit - 5);
    const displayMax = Math.min(maxDepth * 1.15, maxDepth + 50);

    chartInstance.options.scales.y.min = minDepth;
    chartInstance.options.scales.y.max = Math.ceil(displayMax);

    chartInstance.update();
}
