/**
 * SeaPath ECDIS - Professional Marine Navigation & Passage Planning Interface
 * ============================================================================
 * Implements IMO Resolution A.893(21), IEC 61174:2015, and PIANC MarCom WG 121 standards:
 * - Reactive multi-waypoint passage planning with draggable chart markers.
 * - Dynamic Under-Keel Clearance (PIANC DUKC): Squat, Turning Heel, Wave response, Tide level.
 * - Swept safety corridors (XTD) and circular turn arcs with Wheel Over Points (WOP).
 * - Choke point / bottleneck localization with speed adaptation & tidal window advisories.
 * - Real-time ECDIS Bridge Simulator with virtual echo sounder & bridge telemetry HUD.
 */

// -----------------------------------------------------------------------------
// 1. MapLibre Chart Initialization
// -----------------------------------------------------------------------------
const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: [36.0, 44.8], // Black Sea & Crimean Fairways
    zoom: 7.5,
    pitch: 0,
    attributionControl: false
});

// -----------------------------------------------------------------------------
// 2. UI Element References
// -----------------------------------------------------------------------------
const draftSlider = document.getElementById('draft-slider');
const draftVal = document.getElementById('draft-val');
const speedSlider = document.getElementById('speed-slider');
const speedVal = document.getElementById('speed-val');
const ukcSlider = document.getElementById('ukc-slider');
const ukcVal = document.getElementById('ukc-val');
const radiusSlider = document.getElementById('radius-slider');
const radiusVal = document.getElementById('radius-val');
const tideSlider = document.getElementById('tide-slider');
const tideVal = document.getElementById('tide-val');
const waveSlider = document.getElementById('wave-slider');
const waveVal = document.getElementById('wave-val');

const toggleFairwaysCheckbox = document.getElementById('toggle-fairways');
const prioritizeFairwaysCheckbox = document.getElementById('prioritize-fairways');
const xtdSlider = document.getElementById('xtd-slider');
const xtdVal = document.getElementById('xtd-val');

const dynDraftVal = document.getElementById('dynamic-draft-val');
const dangerVal = document.getElementById('danger-val');
const squatVal = document.getElementById('squat-val');
const cbVal = document.getElementById('cb-val');

const vesselSelect = document.getElementById('vessel-profile');
const waypointsListContainer = document.getElementById('waypoints-list');
const addWpBtn = document.getElementById('add-wp-btn');
const clearRouteBtn = document.getElementById('clear-route');
const statusMsg = document.getElementById('status');

const bottomDrawer = document.getElementById('bottom-drawer');
const tabProfileBtn = document.getElementById('tab-profile-btn');
const tabWaypointsBtn = document.getElementById('tab-waypoints-btn');
const tabSafetyBtn = document.getElementById('tab-safety-btn');
const tabSimulatorBtn = document.getElementById('tab-simulator-btn');
const paneProfile = document.getElementById('pane-profile');
const paneWaypoints = document.getElementById('pane-waypoints');
const paneSafety = document.getElementById('pane-safety');
const paneSimulator = document.getElementById('pane-simulator');
const safetyBadge = document.getElementById('safety-badge');
const safetyDashboard = document.getElementById('safety-dashboard-content');

const exportActions = document.getElementById('export-actions');
const exportGpxBtn = document.getElementById('export-gpx-btn');
const exportRtzBtn = document.getElementById('export-rtz-btn');
const drawerCloseBtn = document.getElementById('drawer-close-btn');
const waypointsTbody = document.getElementById('waypoints-tbody');
const wpBadge = document.getElementById('wp-badge');

// Simulator HUD Controls
const simPlayBtn = document.getElementById('sim-play-btn');
const simPauseBtn = document.getElementById('sim-pause-btn');
const simResetBtn = document.getElementById('sim-reset-btn');
const simSpeedBtns = document.querySelectorAll('.sim-speed-btn');
const simProgressText = document.getElementById('sim-progress-text');
const simProgressFill = document.getElementById('sim-progress-fill');
const hudDepthVal = document.getElementById('hud-depth-val');
const hudChartDepth = document.getElementById('hud-chart-depth');
const hudTide = document.getElementById('hud-tide');
const hudUkcVal = document.getElementById('hud-ukc-val');
const hudUkcStatus = document.getElementById('hud-ukc-status');
const hudHdgVal = document.getElementById('hud-hdg-val');
const hudSogVal = document.getElementById('hud-sog-val');
const hudNextWpVal = document.getElementById('hud-next-wp-val');
const hudWpDist = document.getElementById('hud-wp-dist');
const hudWpEta = document.getElementById('hud-wp-eta');
const simAlarmBanner = document.getElementById('sim-alarm-banner');

// -----------------------------------------------------------------------------
// 3. Vessel Profiles & Navigation State
// -----------------------------------------------------------------------------
const VESSEL_PROFILES = {
    yacht: { draft: 1.2, speed: 10, ukc: 0.5, radius: 50, cb: 0.50 },
    ferry: { draft: 4.5, speed: 18, ukc: 1.0, radius: 250, cb: 0.65 },
    cargo: { draft: 10.0, speed: 12, ukc: 2.0, radius: 500, cb: 0.82 }
};

let draft = parseFloat(draftSlider.value);
let speedKnots = parseFloat(speedSlider.value);
let baseUkc = parseFloat(ukcSlider.value);
let turningRadius = radiusSlider ? parseFloat(radiusSlider.value) : 50.0;
let tideOffsetM = tideSlider ? parseFloat(tideSlider.value) : 0.0;
let waveHeightM = waveSlider ? parseFloat(waveSlider.value) : 0.0;
let portXtdM = xtdSlider ? parseFloat(xtdSlider.value) : 185.2;
let fairwayPreference = prioritizeFairwaysCheckbox && prioritizeFairwaysCheckbox.checked ? 1.0 : 0.0;
let currentCb = 0.50;
let dynamicSquat = 0.0;

// Multi-waypoint state
let plannedWaypoints = []; // [{ id: number, coords: [lon, lat], marker: Marker, role: 'start'|'via'|'goal' }]
let nextWpId = 1;
let isAddingViaMode = false;
let currentRouteData = null;
let activeWpMarker = null;

// -----------------------------------------------------------------------------
// 4. Depth Profile Chart Setup (Chart.js)
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
        animation: { duration: 300 },
        scales: {
            y: {
                reverse: true,
                title: { display: true, text: 'Depth (Meters below LAT)' },
                grid: { color: 'rgba(255, 255, 255, 0.05)' }
            },
            x: {
                title: { display: true, text: 'Cumulative Distance along Track' },
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
// 5. Vessel Hydrodynamics & PIANC DUKC Budget
// -----------------------------------------------------------------------------
function updateVesselPhysics() {
    // Open-water Barrass squat formula: (Cb * V^2) / 100
    dynamicSquat = (currentCb * Math.pow(speedKnots, 2)) / 100.0;
    const waveAllowance = 0.35 * waveHeightM;
    const dynamicDraft = draft + dynamicSquat + waveAllowance;
    const requiredChartDepth = Math.max(0.5, dynamicDraft + baseUkc - tideOffsetM);

    if (squatVal) squatVal.innerText = dynamicSquat.toFixed(2) + 'm';
    if (cbVal) cbVal.innerText = currentCb.toFixed(2);
    if (dynDraftVal) dynDraftVal.innerText = dynamicDraft.toFixed(2) + 'm';
    if (dangerVal) dangerVal.innerText = requiredChartDepth.toFixed(2) + 'm';
}

let recalcDebounceTimer = null;
function triggerRouteRecalculation(immediate = false) {
    if (plannedWaypoints.length < 2) return;
    if (recalcDebounceTimer) {
        clearTimeout(recalcDebounceTimer);
        recalcDebounceTimer = null;
    }
    if (immediate) {
        calculateRoute();
    } else {
        recalcDebounceTimer = setTimeout(() => {
            calculateRoute();
        }, 180);
    }
}

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
// 6. Parameter Event Listeners
// -----------------------------------------------------------------------------
if (vesselSelect) {
    vesselSelect.addEventListener('change', (e) => {
        const profile = VESSEL_PROFILES[e.target.value];
        if (profile) {
            draftSlider.value = profile.draft;
            speedSlider.value = profile.speed;
            ukcSlider.value = profile.ukc;
            if (radiusSlider) radiusSlider.value = profile.radius;
            currentCb = profile.cb || 0.65;

            draft = profile.draft;
            speedKnots = profile.speed;
            baseUkc = profile.ukc;
            turningRadius = profile.radius;

            draftVal.innerText = draft.toFixed(1) + 'm';
            speedVal.innerText = speedKnots + ' kts';
            ukcVal.innerText = baseUkc.toFixed(1) + 'm';
            if (radiusVal) radiusVal.innerText = Math.round(turningRadius) + 'm';

            updateVesselPhysics();
            triggerRouteRecalculation(true);
        }
    });

    const markCustomProfile = () => {
        vesselSelect.value = 'custom';
        if (draft < 2.5) currentCb = 0.50;
        else if (draft < 7.0) currentCb = 0.65;
        else currentCb = 0.82;
    };

    draftSlider.addEventListener('input', (e) => {
        draft = parseFloat(e.target.value);
        draftVal.innerText = draft.toFixed(1) + 'm';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    draftSlider.addEventListener('change', () => triggerRouteRecalculation(true));

    speedSlider.addEventListener('input', (e) => {
        speedKnots = parseFloat(e.target.value);
        speedVal.innerText = speedKnots + ' kts';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    speedSlider.addEventListener('change', () => triggerRouteRecalculation(true));

    ukcSlider.addEventListener('input', (e) => {
        baseUkc = parseFloat(e.target.value);
        ukcVal.innerText = baseUkc.toFixed(1) + 'm';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    ukcSlider.addEventListener('change', () => triggerRouteRecalculation(true));

    if (radiusSlider) {
        radiusSlider.addEventListener('input', (e) => {
            turningRadius = parseFloat(e.target.value);
            if (radiusVal) radiusVal.innerText = Math.round(turningRadius) + 'm';
            markCustomProfile();
            triggerRouteRecalculation(false);
        });
        radiusSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }

    if (tideSlider) {
        tideSlider.addEventListener('input', (e) => {
            tideOffsetM = parseFloat(e.target.value);
            if (tideVal) tideVal.innerText = `${tideOffsetM >= 0 ? '+' : ''}${tideOffsetM.toFixed(1)}m`;
            updateVesselPhysics();
            triggerRouteRecalculation(false);
        });
        tideSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }

    if (waveSlider) {
        waveSlider.addEventListener('input', (e) => {
            waveHeightM = parseFloat(e.target.value);
            if (waveVal) waveVal.innerText = `${waveHeightM.toFixed(1)}m`;
            updateVesselPhysics();
            triggerRouteRecalculation(false);
        });
        waveSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }

    if (xtdSlider) {
        xtdSlider.addEventListener('input', (e) => {
            portXtdM = parseFloat(e.target.value);
            const nm = (portXtdM / 1852.0).toFixed(2);
            if (xtdVal) xtdVal.innerText = `${Math.round(portXtdM)}m (${nm} NM)`;
            triggerRouteRecalculation(false);
        });
        xtdSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }
}

// -----------------------------------------------------------------------------
// 7. Map Chart Layers (Heatmap, Fairways, Corridors, Turn Arcs, Bottleneck)
// -----------------------------------------------------------------------------
map.on('load', () => {
    // 1. Bathymetric Safety Corridor Layer
    map.addSource('route-corridor', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'route-corridor-fill',
        type: 'fill',
        source: 'route-corridor',
        paint: {
            'fill-color': '#10b981',
            'fill-opacity': 0.12
        }
    });

    map.addLayer({
        id: 'route-corridor-line',
        type: 'line',
        source: 'route-corridor',
        paint: {
            'line-color': '#10b981',
            'line-width': 1.0,
            'line-dasharray': [3, 2],
            'line-opacity': 0.6
        }
    });

    // 2. Primary Route Track Layer
    map.addSource('route', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'route-layer',
        type: 'line',
        source: 'route',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
            'line-color': '#10b981',
            'line-width': 4.5,
            'line-opacity': 0.95
        }
    });

    // 3. Fillet Circular Turn Arcs & WOP Markers Layers
    map.addSource('turn-arcs', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'turn-arcs-line',
        type: 'line',
        source: 'turn-arcs',
        filter: ['==', ['get', 'role'], 'turn_arc'],
        paint: {
            'line-color': '#f59e0b',
            'line-width': 3.5,
            'line-opacity': 0.9
        }
    });

    map.addLayer({
        id: 'wop-markers-layer',
        type: 'circle',
        source: 'turn-arcs',
        filter: ['==', ['get', 'role'], 'wop_marker'],
        paint: {
            'circle-color': '#fbbf24',
            'circle-radius': 5.5,
            'circle-stroke-width': 2,
            'circle-stroke-color': '#0f172a'
        }
    });

    // 4. Critical Bottleneck Point Layer
    map.addSource('bottleneck-point', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'bottleneck-marker-layer',
        type: 'circle',
        source: 'bottleneck-point',
        paint: {
            'circle-color': '#ef4444',
            'circle-radius': 7.5,
            'circle-stroke-width': 2.5,
            'circle-stroke-color': '#ffffff'
        }
    });

    // 5. High-Resolution Bathymetry Heatmap
    map.addSource('depth-heatmap', {
        type: 'image',
        url: '/heatmap.png',
        coordinates: [
            [33.15, 45.40],
            [33.85, 45.40],
            [33.85, 44.35],
            [33.15, 44.35]
        ]
    });

    map.addLayer({
        id: 'depth-heatmap-layer',
        type: 'raster',
        source: 'depth-heatmap',
        paint: { 'raster-opacity': 0.8 }
    }, 'route-corridor-fill');

    // 6. Vector Fairways & TSS Corridors
    fetch('/api/fairways')
        .then(res => res.json())
        .then(fairwaysData => {
            map.addSource('fairways-data', {
                type: 'geojson',
                data: fairwaysData
            });

            map.addLayer({
                id: 'fairways-fill',
                type: 'fill',
                source: 'fairways-data',
                filter: ['!=', ['get', 'category'], 'restricted'],
                paint: { 'fill-color': '#06b6d4', 'fill-opacity': 0.12 }
            }, 'route-corridor-fill');

            map.addLayer({
                id: 'fairways-line',
                type: 'line',
                source: 'fairways-data',
                filter: ['!=', ['get', 'category'], 'restricted'],
                paint: {
                    'line-color': '#06b6d4',
                    'line-width': 1.5,
                    'line-dasharray': [4, 3],
                    'line-opacity': 0.85
                }
            }, 'route-corridor-fill');

            map.addLayer({
                id: 'restricted-fill',
                type: 'fill',
                source: 'fairways-data',
                filter: ['==', ['get', 'category'], 'restricted'],
                paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.18 }
            }, 'route-corridor-fill');

            map.addLayer({
                id: 'restricted-line',
                type: 'line',
                source: 'fairways-data',
                filter: ['==', ['get', 'category'], 'restricted'],
                paint: {
                    'line-color': '#ef4444',
                    'line-width': 2,
                    'line-dasharray': [6, 3],
                    'line-opacity': 0.95
                }
            }, 'route-corridor-fill');
        })
        .catch(err => console.warn('Could not load vector fairways:', err));

    if (toggleFairwaysCheckbox) {
        toggleFairwaysCheckbox.addEventListener('change', (e) => {
            const vis = e.target.checked ? 'visible' : 'none';
            ['fairways-fill', 'fairways-line', 'restricted-fill', 'restricted-line'].forEach(id => {
                if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis);
            });
        });
    }

    if (prioritizeFairwaysCheckbox) {
        prioritizeFairwaysCheckbox.addEventListener('change', (e) => {
            fairwayPreference = e.target.checked ? 1.0 : 0.0;
            triggerRouteRecalculation(true);
        });
    }

    updateVesselPhysics();
});

// -----------------------------------------------------------------------------
// 8. Interactive Multi-Waypoint Management & Drag-and-Drop
// -----------------------------------------------------------------------------
function createWaypointMarker(wpObj) {
    const el = document.createElement('div');
    el.style.width = '18px';
    el.style.height = '18px';
    el.style.borderRadius = '50%';
    el.style.border = '2px solid #ffffff';
    el.style.boxShadow = '0 0 6px rgba(0,0,0,0.6)';
    el.style.cursor = 'grab';

    if (wpObj.role === 'start') {
        el.style.background = '#10b981';
    } else if (wpObj.role === 'goal') {
        el.style.background = '#ef4444';
    } else {
        el.style.background = '#f59e0b';
    }

    const marker = new maplibregl.Marker({ element: el, draggable: true })
        .setLngLat(wpObj.coords)
        .addTo(map);

    marker.on('dragend', () => {
        const lngLat = marker.getLngLat();
        wpObj.coords = [lngLat.lng, lngLat.lat];
        renderWaypointsListUI();
        triggerRouteRecalculation(true);
    });

    return marker;
}

function renderWaypointsListUI() {
    if (!waypointsListContainer) return;
    waypointsListContainer.innerHTML = '';

    if (plannedWaypoints.length === 0) {
        waypointsListContainer.innerHTML = `
            <div class="wp wp-start"><div class="wp-indicator"></div><span>A (Start): Click on chart</span></div>
            <div class="wp wp-goal"><div class="wp-indicator"></div><span>B (Goal): Click on chart</span></div>
        `;
        return;
    }

    plannedWaypoints.forEach((wp, idx) => {
        const row = document.createElement('div');
        row.className = 'wp-item-row';

        let badgeColor = '#f59e0b';
        let label = `WP${idx + 1}`;
        if (wp.role === 'start') {
            badgeColor = '#10b981';
            label = 'A (Start)';
        } else if (wp.role === 'goal') {
            badgeColor = '#ef4444';
            label = 'B (Goal)';
        }

        const coordStr = formatCoordinates(wp.coords[1], wp.coords[0]);
        row.innerHTML = `
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${badgeColor};"></span>
                <span style="font-weight: 600;">${label}</span>
                <span style="color: #94a3b8; font-family: monospace;">${coordStr}</span>
            </div>
            ${plannedWaypoints.length > 2 ? `<button class="wp-item-del-btn" data-id="${wp.id}" title="Remove waypoint">✕</button>` : ''}
        `;

        const delBtn = row.querySelector('.wp-item-del-btn');
        if (delBtn) {
            delBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                removeWaypoint(wp.id);
            });
        }

        row.addEventListener('click', () => {
            map.flyTo({ center: wp.coords, zoom: Math.max(map.getZoom(), 10.0), speed: 1.2 });
        });

        waypointsListContainer.appendChild(row);
    });
}

function addWaypoint(coords, role = 'via') {
    const wp = {
        id: nextWpId++,
        coords: [coords[0], coords[1]],
        role: role,
        marker: null
    };
    wp.marker = createWaypointMarker(wp);

    if (role === 'via' && plannedWaypoints.length >= 2) {
        // Insert right before the goal waypoint
        plannedWaypoints.splice(plannedWaypoints.length - 1, 0, wp);
    } else {
        plannedWaypoints.push(wp);
    }

    renderWaypointsListUI();
    if (plannedWaypoints.length >= 2) {
        calculateRoute();
    }
}

function removeWaypoint(id) {
    const idx = plannedWaypoints.findIndex(w => w.id === id);
    if (idx !== -1) {
        plannedWaypoints[idx].marker.remove();
        plannedWaypoints.splice(idx, 1);
        renderWaypointsListUI();
        if (plannedWaypoints.length >= 2) {
            triggerRouteRecalculation(true);
        } else {
            clearRouteData();
        }
    }
}

function clearRouteData() {
    plannedWaypoints.forEach(w => w.marker && w.marker.remove());
    plannedWaypoints = [];
    if (activeWpMarker) activeWpMarker.remove();
    activeWpMarker = null;
    currentRouteData = null;

    map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
    if (map.getSource('route-corridor')) map.getSource('route-corridor').setData({ type: 'FeatureCollection', features: [] });
    if (map.getSource('turn-arcs')) map.getSource('turn-arcs').setData({ type: 'FeatureCollection', features: [] });
    if (map.getSource('bottleneck-point')) map.getSource('bottleneck-point').setData({ type: 'FeatureCollection', features: [] });

    renderWaypointsListUI();
    renderWaypointsTable([]);
    renderSafetyDashboard(null);

    if (bottomDrawer) bottomDrawer.style.display = 'none';
    if (exportActions) exportActions.style.display = 'none';
    statusMsg.innerText = '';
    stopSimulator();
}

if (clearRouteBtn) {
    clearRouteBtn.addEventListener('click', clearRouteData);
}

if (addWpBtn) {
    addWpBtn.addEventListener('click', () => {
        isAddingViaMode = !isAddingViaMode;
        if (isAddingViaMode) {
            addWpBtn.innerText = 'Click on chart to add WP (Cancel)';
            addWpBtn.style.background = 'rgba(245, 158, 11, 0.25)';
            addWpBtn.style.borderColor = '#f59e0b';
            addWpBtn.style.color = '#fbbf24';
        } else {
            addWpBtn.innerText = '+ Add Via Waypoint';
            addWpBtn.style.background = 'rgba(56, 189, 248, 0.15)';
            addWpBtn.style.borderColor = '#38bdf8';
            addWpBtn.style.color = '#38bdf8';
        }
    });
}

map.on('click', (e) => {
    const coords = [e.lngLat.lng, e.lngLat.lat];

    if (isAddingViaMode) {
        addWaypoint(coords, 'via');
        isAddingViaMode = false;
        addWpBtn.innerText = '+ Add Via Waypoint';
        addWpBtn.style.background = 'rgba(56, 189, 248, 0.15)';
        addWpBtn.style.borderColor = '#38bdf8';
        addWpBtn.style.color = '#38bdf8';
        return;
    }

    if (plannedWaypoints.length === 0) {
        addWaypoint(coords, 'start');
    } else if (plannedWaypoints.length === 1) {
        addWaypoint(coords, 'goal');
    }
});

// -----------------------------------------------------------------------------
// 9. Drawer Tabs & Panel Controls
// -----------------------------------------------------------------------------
const drawerTabs = [
    { btn: tabProfileBtn, pane: paneProfile },
    { btn: tabWaypointsBtn, pane: paneWaypoints },
    { btn: tabSafetyBtn, pane: paneSafety },
    { btn: tabSimulatorBtn, pane: paneSimulator }
];

drawerTabs.forEach(t => {
    if (t.btn && t.pane) {
        t.btn.addEventListener('click', () => {
            drawerTabs.forEach(o => {
                if (o.btn) o.btn.classList.remove('active');
                if (o.pane) o.pane.classList.remove('active');
            });
            t.btn.classList.add('active');
            t.pane.classList.add('active');
        });
    }
});

if (drawerCloseBtn) {
    drawerCloseBtn.addEventListener('click', () => {
        if (bottomDrawer) bottomDrawer.style.display = 'none';
    });
}

// -----------------------------------------------------------------------------
// 10. Waypoints Table Rendering (Passage Plan)
// -----------------------------------------------------------------------------
function renderWaypointsTable(waypoints) {
    if (!waypointsTbody) return;
    waypointsTbody.innerHTML = '';

    if (!waypoints || !waypoints.length) {
        if (wpBadge) wpBadge.innerText = '0 WP';
        return;
    }

    if (wpBadge) wpBadge.innerText = `${waypoints.length} WP`;

    waypoints.forEach((wp, idx) => {
        const tr = document.createElement('tr');
        tr.dataset.wpIndex = idx;

        const isLast = (idx === waypoints.length - 1);
        const brgVal = wp.leg_bearing_deg !== undefined ? wp.leg_bearing_deg : 0.0;
        const distVal = wp.leg_distance_nm !== undefined ? wp.leg_distance_nm : 0.0;
        const turnVal = wp.turn_angle_deg !== undefined ? wp.turn_angle_deg : 0.0;
        const radVal = wp.turn_radius_m !== undefined ? wp.turn_radius_m : 0.0;
        const depthVal = wp.depth_m !== undefined ? wp.depth_m : 0.0;
        const clrVal = wp.clearance_m !== undefined ? wp.clearance_m : 0.0;

        const brgTxt = isLast ? '—' : `${brgVal.toFixed(1)}°T`;
        const distTxt = isLast ? '—' : `${distVal.toFixed(2)}`;
        const turnTxt = (idx === 0 || isLast || turnVal < 0.5) ? '0.0°' : `${turnVal.toFixed(1)}°`;
        const radTxt = (idx === 0 || isLast || radVal <= 0) ? '—' : `${Math.round(radVal)}m`;
        const rotVal = wp.rot_deg_min !== undefined ? wp.rot_deg_min : 0.0;
        const wopVal = wp.wop_distance_m !== undefined ? wp.wop_distance_m : 0.0;

        let rotTxt = '—';
        if (!isLast && idx > 0 && Math.abs(turnVal) >= 0.5 && rotVal !== 0.0) {
            rotTxt = `${rotVal > 0 ? '+' : ''}${rotVal.toFixed(1)}°/m`;
        }

        let wopTxt = '—';
        if (!isLast && idx > 0 && wopVal > 0) {
            wopTxt = `${Math.round(wopVal)}m`;
        }

        const depthTxt = `${depthVal.toFixed(1)}m`;
        let clrClass = 'val-clearance-safe';
        if (clrVal < 0.5) clrClass = 'val-clearance-crit';
        else if (clrVal < 1.5) clrClass = 'val-clearance-warn';
        const clrTxt = `<span class="${clrClass}">${clrVal.toFixed(2)}m</span>`;

        const latDmm = formatCoordinates(wp.lat, wp.lon).split(' ')[0];
        const lonDmm = formatCoordinates(wp.lat, wp.lon).split(' ')[1];

        tr.innerHTML = `
            <td><b>WP${idx + 1}</b></td>
            <td>${latDmm}</td>
            <td>${lonDmm}</td>
            <td>${brgTxt}</td>
            <td>${distTxt}</td>
            <td>${turnTxt}</td>
            <td>${radTxt}</td>
            <td>${rotTxt}</td>
            <td>${wopTxt}</td>
            <td>${depthTxt}</td>
            <td>${clrTxt}</td>
        `;

        tr.addEventListener('click', () => {
            document.querySelectorAll('#waypoints-tbody tr').forEach(r => r.classList.remove('active-wp-row'));
            tr.classList.add('active-wp-row');

            if (activeWpMarker) activeWpMarker.remove();
            const el = document.createElement('div');
            el.style.width = '20px';
            el.style.height = '20px';
            el.style.borderRadius = '50%';
            el.style.border = '2px solid #38bdf8';
            el.style.boxShadow = '0 0 10px #38bdf8';
            el.style.background = 'rgba(56, 189, 248, 0.3)';

            activeWpMarker = new maplibregl.Marker({ element: el })
                .setLngLat([wp.lon, wp.lat])
                .addTo(map);

            map.flyTo({ center: [wp.lon, wp.lat], zoom: Math.max(map.getZoom(), 10.0), speed: 1.2 });
        });

        waypointsTbody.appendChild(tr);
    });
}

// -----------------------------------------------------------------------------
// 11. Route Calculation & API Interaction
// -----------------------------------------------------------------------------
window.calculateRoute = async function () {
    if (plannedWaypoints.length < 2) {
        statusMsg.innerText = 'Please designate departure (A) and destination (B) waypoints.';
        statusMsg.style.color = 'var(--status-warning)';
        return;
    }

    statusMsg.innerText = 'Computing safe passage plan...';
    statusMsg.style.color = 'var(--accent-blue)';

    try {
        const coordsList = plannedWaypoints.map(w => w.coords);
        const payload = {
            waypoints: coordsList,
            draft: draft,
            speed_knots: speedKnots,
            ukc: baseUkc,
            turning_radius_m: turningRadius,
            fairway_preference: fairwayPreference,
            block_coefficient: currentCb,
            port_xtd_m: portXtdM,
            stbd_xtd_m: portXtdM,
            tide_offset_m: tideOffsetM,
            wave_height_m: waveHeightM
        };

        const res = await fetch('/api/route', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            const data = await res.json();
            currentRouteData = data;

            map.getSource('route').setData(data);
            if (map.getSource('route-corridor')) {
                map.getSource('route-corridor').setData(data.corridor || { type: 'FeatureCollection', features: [] });
            }
            if (map.getSource('turn-arcs')) {
                map.getSource('turn-arcs').setData(data.turn_arcs || { type: 'FeatureCollection', features: [] });
            }

            // Update bottleneck marker on map
            if (map.getSource('bottleneck-point')) {
                const bPt = data.bottleneck && data.bottleneck.shallowest_point;
                if (bPt && bPt.lon && bPt.lat) {
                    map.getSource('bottleneck-point').setData({
                        type: 'Feature',
                        properties: { role: 'bottleneck' },
                        geometry: { type: 'Point', coordinates: [bPt.lon, bPt.lat] }
                    });
                } else {
                    map.getSource('bottleneck-point').setData({ type: 'FeatureCollection', features: [] });
                }
            }

            const p = data.properties;
            const distNm = p.distance_nm !== undefined ? p.distance_nm.toFixed(1) + ' NM' : '—';
            const eta = (p.eta_hours !== null && p.eta_hours !== undefined) ? p.eta_hours.toFixed(1) + ' h' : '— (Stationary)';
            const clearance = p.min_clearance_m !== undefined ? p.min_clearance_m.toFixed(2) + ' m' : '—';
            const waypointsCount = p.waypoints || (data.waypoints ? data.waypoints.length : '—');

            statusMsg.innerHTML =
                `✓ <b>${distNm}</b> &nbsp;|&nbsp; ETA <b>${eta}</b>` +
                ` &nbsp;|&nbsp; Min UKC <b>${clearance}</b>` +
                ` &nbsp;|&nbsp; ${waypointsCount} waypoints`;
            statusMsg.style.color = 'var(--status-success)';

            renderWaypointsTable(data.waypoints || []);
            renderSafetyDashboard(data.safety_check, data.bottleneck, data.dukc);

            if (data.profile && data.profile.length) {
                renderProfileChart(data.profile, payload.draft + dynamicSquat + payload.ukc - tideOffsetM);
            }

            if (bottomDrawer) bottomDrawer.style.display = 'flex';
            if (exportActions) exportActions.style.display = 'inline-flex';

            initSimulator(data);
        } else {
            const err = await res.json().catch(() => ({}));
            statusMsg.innerText = '❌ ' + (err.detail || 'No navigable passage found.');
            statusMsg.style.color = 'var(--status-danger)';
            map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('route-corridor')) map.getSource('route-corridor').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('turn-arcs')) map.getSource('turn-arcs').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('bottleneck-point')) map.getSource('bottleneck-point').setData({ type: 'FeatureCollection', features: [] });
            renderSafetyDashboard(null, null, null);
            currentRouteData = null;
            stopSimulator();
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

// -----------------------------------------------------------------------------
// 12. ECDIS Route Safety Verification Dashboard Rendering
// -----------------------------------------------------------------------------
function renderSafetyDashboard(safetyData, bottleneckData, dukcData) {
    if (!safetyDashboard) return;
    if (!safetyData) {
        safetyDashboard.innerHTML = '<div style="color: #94a3b8; padding: 10px;">No safety audit data available.</div>';
        if (safetyBadge) {
            safetyBadge.innerText = 'OK';
            safetyBadge.className = 'badge-safe';
        }
        return;
    }

    if (safetyBadge) {
        if (safetyData.status === 'CRITICAL_HAZARD') {
            safetyBadge.innerText = 'DANGER';
            safetyBadge.className = 'badge-danger';
        } else if (safetyData.status === 'WARNING_ADVISORY') {
            safetyBadge.innerText = `${safetyData.alarms_count} WARN`;
            safetyBadge.className = 'badge-warn';
        } else {
            safetyBadge.innerText = 'PASSED';
            safetyBadge.className = 'badge-safe';
        }
    }

    const bannerClass = safetyData.status === 'CRITICAL_HAZARD'
        ? 'safety-banner-danger'
        : (safetyData.status === 'WARNING_ADVISORY' ? 'safety-banner-warning' : 'safety-banner-passed');

    const m = safetyData.metrics || {};
    let html = `
        <div class="safety-status-banner ${bannerClass}">
            <span>${safetyData.summary}</span>
            <span style="font-size: 10px; opacity: 0.8;">IEC 61174:2015 §6.8 / PIANC MarCom WG 121</span>
        </div>
        <div class="safety-metrics-grid">
            <div class="metric-box">
                <span class="metric-label">Safety Contour Depth</span>
                <span class="metric-val" style="color: #38bdf8;">${m.safety_depth_m !== undefined ? m.safety_depth_m.toFixed(2) + 'm' : '—'}</span>
            </div>
            <div class="metric-box">
                <span class="metric-label">Dyn. Squat (PIANC)</span>
                <span class="metric-val" style="color: #38bdf8;">${m.dynamic_squat_m !== undefined ? m.dynamic_squat_m.toFixed(2) + 'm' : '—'}</span>
            </div>
            <div class="metric-box">
                <span class="metric-label">Min Seafloor UKC</span>
                <span class="metric-val ${m.min_clearance_m < 0 ? 'val-danger' : (m.min_clearance_m < 0.5 ? 'val-warn' : '')}" style="${m.min_clearance_m >= 0.5 ? 'color: #10b981;' : ''}">${m.min_clearance_m !== undefined ? m.min_clearance_m.toFixed(2) + 'm' : '—'}</span>
            </div>
            <div class="metric-box">
                <span class="metric-label">Bridge ROT Limit</span>
                <span class="metric-val" style="color: #94a3b8;">${m.rot_threshold_deg_min !== undefined ? m.rot_threshold_deg_min + '°/min' : '—'}</span>
            </div>
        </div>
    `;

    // Bottleneck & Choke Point Analysis Card
    if (bottleneckData && bottleneckData.shallowest_point) {
        const b = bottleneckData.shallowest_point;
        const isHazard = bottleneckData.is_grounding_hazard;
        html += `
            <div class="bottleneck-card ${isHazard ? '' : 'bottleneck-safe'}">
                <div class="bottleneck-header">
                    <span>${isHazard ? '⚠️ CRITICAL PASSAGE CHOKE POINT DETECTED' : '✓ CONTROLLING PASSAGE SOUNDING (BOTTLENECK)'}</span>
                    <span>Chainage: ${b.distance_nm} NM</span>
                </div>
                <div style="font-size: 11.5px; color: var(--text-secondary); line-height: 1.4;">
                    Chart Depth: <b>${b.chart_depth_m}m</b> (Eff. with Tide: <b>${b.effective_depth_m}m</b>) |
                    Net Seafloor UKC: <b style="color: ${b.net_clearance_m < 0 ? '#ef4444' : '#10b981'};">${b.net_clearance_m}m</b> |
                    Max Allowable Static Draft: <b>${bottleneckData.max_allowable_draft_m}m</b>
                </div>
        `;

        if (bottleneckData.speed_adaptation && bottleneckData.speed_adaptation.is_viable) {
            const sa = bottleneckData.speed_adaptation;
            html += `
                <div class="advisory-box">
                    <b>💡 Speed Adaptation Advisory:</b> ${sa.advisory}
                    <button id="apply-safe-speed-btn" style="margin-left: 8px; padding: 2px 8px; font-size: 11px; background: #38bdf8; color: #0f172a; border: none; border-radius: 2px; font-weight: 600; cursor: pointer;">
                        Apply ${sa.recommended_speed_knots} kts
                    </button>
                </div>
            `;
        }

        if (bottleneckData.tidal_window) {
            const tw = bottleneckData.tidal_window;
            html += `
                <div class="tidal-box">
                    <b>🌊 M2 Tidal Window Analysis:</b> ${tw.notes}
                </div>
            `;
        }

        html += `</div>`;
    }

    // Dynamic Under-Keel Clearance (PIANC DUKC Budget)
    if (dukcData) {
        html += `
            <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 4px; padding: 10px 12px; margin-bottom: 10px; font-size: 11.5px;">
                <div style="font-weight: 600; color: #38bdf8; margin-bottom: 6px;">📐 PIANC DUKC DYNAMIC MOTION BUDGET</div>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; color: var(--text-secondary);">
                    <div>Static Draft: <b>${dukcData.static_draft_m}m</b></div>
                    <div>PIANC Squat: <b>${dukcData.dynamic_squat_m}m</b></div>
                    <div>Turning Heel Sinkage: <b>+${dukcData.heel_sinkage_m}m</b> (${dukcData.heel_angle_deg}°)</div>
                    <div>Wave Response (Hs): <b>+${dukcData.wave_allowance_m}m</b></div>
                    <div>Total Dynamic Draft: <b style="color: #f59e0b;">${dukcData.total_dynamic_draft_m}m</b></div>
                    <div>Effective Seafloor Depth: <b style="color: #38bdf8;">${dukcData.effective_depth_m}m</b></div>
                </div>
            </div>
        `;
    }

    // Navigational Alarms
    if (safetyData.alarms && safetyData.alarms.length) {
        html += '<div class="alarms-list">';
        safetyData.alarms.forEach(a => {
            const cardClass = a.severity === 'CRITICAL' ? 'alarm-critical' : 'alarm-warning';
            html += `
                <div class="alarm-card ${cardClass}">
                    <div class="alarm-header">
                        <span class="alarm-title">${a.title}</span>
                        <span class="alarm-code">[${a.code}]</span>
                    </div>
                    <div class="alarm-detail">${a.detail}</div>
                </div>
            `;
        });
        html += '</div>';
    }

    safetyDashboard.innerHTML = html;

    const applySpeedBtn = document.getElementById('apply-safe-speed-btn');
    if (applySpeedBtn && bottleneckData && bottleneckData.speed_adaptation) {
        applySpeedBtn.addEventListener('click', () => {
            const safeV = bottleneckData.speed_adaptation.recommended_speed_knots;
            speedSlider.value = safeV;
            speedKnots = safeV;
            speedVal.innerText = safeV + ' kts';
            updateVesselPhysics();
            calculateRoute();
        });
    }
}

// -----------------------------------------------------------------------------
// 13. ECDIS Route Playback & Bridge Simulator Engine (Direction E)
// -----------------------------------------------------------------------------
let simState = {
    isRunning: false,
    speedMultiplier: 1,
    currentDistanceM: 0,
    totalDistanceM: 0,
    routeCoords: [],
    profile: [],
    waypoints: [],
    animFrameId: null,
    lastTimestamp: null,
    vesselMarker: null
};

let audioCtx = null;
function playBridgeChime() {
    try {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (audioCtx.state === 'suspended') audioCtx.resume();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, audioCtx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.35);
        gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.35);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.4);
    } catch (e) {
        // audio might be blocked by browser autoplay policy
    }
}

function initVesselSimulatorMarker() {
    if (simState.vesselMarker) return simState.vesselMarker;
    const el = document.createElement('div');
    el.className = 'sim-vessel-marker';
    el.style.width = '28px';
    el.style.height = '28px';
    el.style.cursor = 'pointer';
    el.innerHTML = `
        <svg id="vessel-sim-svg" width="28" height="28" viewBox="0 0 28 28" style="transform-origin: 14px 14px; filter: drop-shadow(0 0 6px rgba(56, 189, 248, 0.8));">
            <polygon points="14,2 22,23 14,18 6,23" fill="#38bdf8" stroke="#ffffff" stroke-width="2"/>
        </svg>
    `;
    simState.vesselMarker = new maplibregl.Marker({ element: el, anchor: 'center' });
    return simState.vesselMarker;
}

function initSimulator(data) {
    if (!data || !data.geometry || !data.geometry.coordinates) return;
    simState.routeCoords = data.geometry.coordinates;
    simState.profile = data.profile || [];
    simState.waypoints = data.waypoints || [];
    simState.totalDistanceM = (data.properties.distance_nm || 0) * 1852.0;
    simState.currentDistanceM = 0;

    const marker = initVesselSimulatorMarker();
    if (simState.routeCoords.length > 0) {
        marker.setLngLat(simState.routeCoords[0]).addTo(map);
    }

    if (simPlayBtn) simPlayBtn.disabled = false;
    if (simPauseBtn) simPauseBtn.disabled = true;
    updateSimulatorHUD(0);
}

function stopSimulator() {
    simState.isRunning = false;
    simState.currentDistanceM = 0;
    if (simState.animFrameId) cancelAnimationFrame(simState.animFrameId);
    simState.animFrameId = null;
    simState.lastTimestamp = null;
    if (simState.vesselMarker) simState.vesselMarker.remove();
    simState.vesselMarker = null;

    if (simPlayBtn) {
        simPlayBtn.innerText = '▶ Start';
        simPlayBtn.disabled = false;
    }
    if (simPauseBtn) simPauseBtn.disabled = true;
    if (simAlarmBanner) simAlarmBanner.style.display = 'none';
}

function calculateBearing(p1, p2) {
    const dLon = (p2[0] - p1[0]) * Math.PI / 180.0;
    const lat1 = p1[1] * Math.PI / 180.0;
    const lat2 = p2[1] * Math.PI / 180.0;
    const y = Math.sin(dLon) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
    return (Math.atan2(y, x) * 180.0 / Math.PI + 360.0) % 360.0;
}

function getInterpolatedPosition(distM) {
    const coords = simState.routeCoords;
    if (!coords || coords.length < 2) return null;

    let accumulated = 0;
    for (let i = 0; i < coords.length - 1; i++) {
        const p1 = coords[i];
        const p2 = coords[i + 1];
        const dy = (p2[1] - p1[1]) * 111139.0;
        const dx = (p2[0] - p1[0]) * 111139.0 * Math.cos(p1[1] * Math.PI / 180.0);
        const segLen = Math.sqrt(dx * dx + dy * dy);

        if (accumulated + segLen >= distM || i === coords.length - 2) {
            const frac = segLen > 0 ? Math.max(0, Math.min(1, (distM - accumulated) / segLen)) : 0;
            const lon = p1[0] + (p2[0] - p1[0]) * frac;
            const lat = p1[1] + (p2[1] - p1[1]) * frac;
            const brg = calculateBearing(p1, p2);
            return { lon, lat, heading: brg, segIndex: i };
        }
        accumulated += segLen;
    }
    return { lon: coords[coords.length - 1][0], lat: coords[coords.length - 1][1], heading: 0, segIndex: coords.length - 1 };
}

function updateSimulatorHUD(distM) {
    const pos = getInterpolatedPosition(distM);
    if (!pos) return;

    if (simState.vesselMarker) {
        simState.vesselMarker.setLngLat([pos.lon, pos.lat]);
        const svg = document.getElementById('vessel-sim-svg');
        if (svg) svg.style.transform = `rotate(${pos.heading}deg)`;
    }

    // Interpolate depth sounding from profile
    let curDepth = 15.0;
    let curClearance = 5.0;
    const prof = simState.profile;
    if (prof && prof.length) {
        for (let i = 0; i < prof.length; i++) {
            if (prof[i].distance_from_start_m >= distM || i === prof.length - 1) {
                curDepth = prof[i].depth;
                curClearance = prof[i].clearance;
                break;
            }
        }
    }

    if (hudDepthVal) hudDepthVal.innerText = `${(curDepth + tideOffsetM).toFixed(1)} m`;
    if (hudChartDepth) hudChartDepth.innerText = `${curDepth.toFixed(1)}m`;
    if (hudTide) hudTide.innerText = `${tideOffsetM >= 0 ? '+' : ''}${tideOffsetM.toFixed(1)}m`;
    if (hudUkcVal) {
        hudUkcVal.innerText = `${curClearance.toFixed(2)} m`;
        hudUkcVal.style.color = curClearance < 0.5 ? '#ef4444' : (curClearance < 1.5 ? '#f59e0b' : '#10b981');
    }
    if (hudUkcStatus) {
        hudUkcStatus.innerText = curClearance < 0 ? 'CRITICAL GROUNDING DANGER' : (curClearance < 0.5 ? 'MARGINAL CLEARANCE' : 'SAFE NAVIGATION');
        hudUkcStatus.style.color = curClearance < 0.5 ? '#ef4444' : '#10b981';
    }
    if (hudHdgVal) hudHdgVal.innerText = `${Math.round(pos.heading).toString().padStart(3, '0')}°T`;
    if (hudSogVal) hudSogVal.innerText = `${speedKnots.toFixed(1)} kts`;

    // Next Waypoint & Wheel Over Point (WOP) Advisory
    let nextWp = null;
    let distToNextWp = 0;
    let cumWpDist = 0;
    const wps = simState.waypoints;

    for (let i = 0; i < wps.length; i++) {
        cumWpDist += (wps[i].leg_distance_nm || 0) * 1852.0;
        if (cumWpDist > distM) {
            nextWp = wps[i + 1] || wps[i];
            distToNextWp = cumWpDist - distM;
            break;
        }
    }

    if (nextWp) {
        const distNm = (distToNextWp / 1852.0).toFixed(2);
        const etaSec = speedKnots > 0 ? (distToNextWp / (speedKnots * 0.514444)) : 0;
        const etaMins = Math.floor(etaSec / 60);
        const etaRemSec = Math.floor(etaSec % 60);
        const etaStr = `${etaMins.toString().padStart(2, '0')}:${etaRemSec.toString().padStart(2, '0')}`;

        if (hudNextWpVal) hudNextWpVal.innerText = `WP${nextWp.index || '—'}`;
        if (hudWpDist) hudWpDist.innerText = `${distNm} NM`;
        if (hudWpEta) hudWpEta.innerText = etaStr;

        // Check if vessel is approaching Wheel Over Point (WOP)
        const wopDistM = nextWp.wop_distance_m || 0;
        const isApproachingWop = (distToNextWp <= wopDistM + 250 && distToNextWp >= wopDistM - 50);

        if (isApproachingWop && simAlarmBanner && Math.abs(nextWp.turn_angle_deg || 0) >= 4.0) {
            simAlarmBanner.style.display = 'block';
            simAlarmBanner.innerHTML = `
                ⚠️ <b>EXECUTE WHEEL OVER:</b> Approaching WP${nextWp.index}!
                Alter course to ${nextWp.rot_deg_min > 0 ? 'Starboard' : 'Port'} (${nextWp.leg_bearing_deg}°T) —
                Rate of Turn: <b>${Math.abs(nextWp.rot_deg_min)}°/min</b> (Radius: ${nextWp.turn_radius_m}m)
            `;
            playBridgeChime();
        } else if (simAlarmBanner && !isApproachingWop) {
            simAlarmBanner.style.display = 'none';
        }
    }

    // Progress bar
    const progressPct = simState.totalDistanceM > 0 ? Math.min(100, (distM / simState.totalDistanceM) * 100) : 0;
    if (simProgressFill) simProgressFill.style.width = `${progressPct.toFixed(1)}%`;
    if (simProgressText) {
        const curNm = (distM / 1852.0).toFixed(1);
        const totNm = (simState.totalDistanceM / 1852.0).toFixed(1);
        simProgressText.innerText = `${curNm} / ${totNm} NM (${Math.round(progressPct)}%)`;
    }
}

function simAnimationFrame(timestamp) {
    if (!simState.isRunning) return;

    if (!simState.lastTimestamp) simState.lastTimestamp = timestamp;
    const deltaSec = (timestamp - simState.lastTimestamp) / 1000.0;
    simState.lastTimestamp = timestamp;

    const vMs = speedKnots * 0.514444;
    const distanceStep = vMs * deltaSec * simState.speedMultiplier;
    simState.currentDistanceM += distanceStep;

    if (simState.currentDistanceM >= simState.totalDistanceM) {
        simState.currentDistanceM = simState.totalDistanceM;
        updateSimulatorHUD(simState.currentDistanceM);
        simState.isRunning = false;
        if (simPlayBtn) {
            simPlayBtn.innerText = '▶ Replay';
            simPlayBtn.disabled = false;
        }
        if (simPauseBtn) simPauseBtn.disabled = true;
        if (simAlarmBanner) {
            simAlarmBanner.style.display = 'block';
            simAlarmBanner.style.background = 'rgba(16, 185, 129, 0.2)';
            simAlarmBanner.style.borderColor = 'var(--status-success)';
            simAlarmBanner.style.color = '#6ee7b7';
            simAlarmBanner.innerHTML = '🏁 <b>PASSAGE COMPLETED:</b> Vessel safely arrived at final destination.';
        }
        return;
    }

    updateSimulatorHUD(simState.currentDistanceM);
    simState.animFrameId = requestAnimationFrame(simAnimationFrame);
}

if (simPlayBtn) {
    simPlayBtn.addEventListener('click', () => {
        if (!currentRouteData) {
            alert('Please compute a route on the chart before starting simulation.');
            return;
        }
        if (simState.currentDistanceM >= simState.totalDistanceM) {
            simState.currentDistanceM = 0;
        }
        simState.isRunning = true;
        simState.lastTimestamp = null;
        simPlayBtn.disabled = true;
        simPlayBtn.innerText = '▶ Running';
        if (simPauseBtn) simPauseBtn.disabled = false;
        simState.animFrameId = requestAnimationFrame(simAnimationFrame);
    });
}

if (simPauseBtn) {
    simPauseBtn.addEventListener('click', () => {
        simState.isRunning = false;
        if (simState.animFrameId) cancelAnimationFrame(simState.animFrameId);
        simPlayBtn.disabled = false;
        simPlayBtn.innerText = '▶ Resume';
        simPauseBtn.disabled = true;
    });
}

if (simResetBtn) {
    simResetBtn.addEventListener('click', () => {
        simState.isRunning = false;
        simState.currentDistanceM = 0;
        if (simState.animFrameId) cancelAnimationFrame(simState.animFrameId);
        simPlayBtn.disabled = false;
        simPlayBtn.innerText = '▶ Start';
        if (simPauseBtn) simPauseBtn.disabled = true;
        updateSimulatorHUD(0);
        if (simAlarmBanner) simAlarmBanner.style.display = 'none';
    });
}

simSpeedBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        simSpeedBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        simState.speedMultiplier = parseFloat(btn.dataset.speed) || 1;
    });
});

// -----------------------------------------------------------------------------
// 14. Route Export Helpers (GPX & RTZ 1.1)
// -----------------------------------------------------------------------------
async function triggerDownload(url, filename, payload) {
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error('Export request failed: ' + res.status);
        const blob = await res.blob();
        const blobUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(blobUrl);
    } catch (e) {
        console.error('Export error:', e);
        alert('Failed to export route: ' + e.message);
    }
}

if (exportGpxBtn) {
    exportGpxBtn.addEventListener('click', () => {
        if (!currentRouteData || !currentRouteData.waypoints) return;
        const payload = {
            waypoints: currentRouteData.waypoints,
            route: currentRouteData.geometry.coordinates,
            draft: draft,
            speed_knots: speedKnots,
            ukc: baseUkc,
            route_name: 'SeaPath_Passage_Plan'
        };
        triggerDownload('/api/export/gpx', 'seapath_passage_plan.gpx', payload);
    });
}

if (exportRtzBtn) {
    exportRtzBtn.addEventListener('click', () => {
        if (!currentRouteData || !currentRouteData.waypoints) return;
        const payload = {
            waypoints: currentRouteData.waypoints,
            draft: draft,
            speed_knots: speedKnots,
            ukc: baseUkc,
            route_name: 'SeaPath_Passage_Plan'
        };
        triggerDownload('/api/export/rtz', 'seapath_passage_plan.rtz', payload);
    });
}
