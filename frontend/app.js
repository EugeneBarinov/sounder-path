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
const radiusSlider = document.getElementById('radius-slider');
const radiusVal = document.getElementById('radius-val');

const toggleFairwaysCheckbox = document.getElementById('toggle-fairways');
const prioritizeFairwaysCheckbox = document.getElementById('prioritize-fairways');

const dynDraftVal = document.getElementById('dynamic-draft-val');
const dangerVal = document.getElementById('danger-val');
const squatVal = document.getElementById('squat-val');
const cbVal = document.getElementById('cb-val');

const startCoordTxt = document.getElementById('start-coord');
const goalCoordTxt = document.getElementById('goal-coord');
const statusMsg = document.getElementById('status');
const vesselSelect = document.getElementById('vessel-profile');

const xtdSlider = document.getElementById('xtd-slider');
const xtdVal = document.getElementById('xtd-val');

const bottomDrawer = document.getElementById('bottom-drawer');
const tabProfileBtn = document.getElementById('tab-profile-btn');
const tabWaypointsBtn = document.getElementById('tab-waypoints-btn');
const tabSafetyBtn = document.getElementById('tab-safety-btn');
const paneProfile = document.getElementById('pane-profile');
const paneWaypoints = document.getElementById('pane-waypoints');
const paneSafety = document.getElementById('pane-safety');
const safetyBadge = document.getElementById('safety-badge');
const safetyDashboard = document.getElementById('safety-dashboard-content');

const exportActions = document.getElementById('export-actions');
const exportGpxBtn = document.getElementById('export-gpx-btn');
const exportRtzBtn = document.getElementById('export-rtz-btn');
const drawerCloseBtn = document.getElementById('drawer-close-btn');
const waypointsTbody = document.getElementById('waypoints-tbody');
const wpBadge = document.getElementById('wp-badge');

// -----------------------------------------------------------------------------
// Vessel Profiles
// -----------------------------------------------------------------------------
const VESSEL_PROFILES = {
    yacht: { draft: 1.2, speed: 10, ukc: 0.5, radius: 50, cb: 0.50 },
    ferry: { draft: 4.5, speed: 18, ukc: 1.0, radius: 250, cb: 0.65 },
    cargo: { draft: 10.0, speed: 12, ukc: 2.0, radius: 500, cb: 0.82 }
};

// State
let draft = parseFloat(draftSlider.value);
let speedKnots = parseFloat(speedSlider.value);
let baseUkc = parseFloat(ukcSlider.value);
let turningRadius = radiusSlider ? parseFloat(radiusSlider.value) : 50.0;
let fairwayPreference = prioritizeFairwaysCheckbox && prioritizeFairwaysCheckbox.checked ? 1.0 : 0.0;
let portXtdM = xtdSlider ? parseFloat(xtdSlider.value) : 185.2;
let currentCb = 0.50;
let dynamicSquat = 0.0;

let startPoint = null;
let goalPoint = null;
let startMarker = null;
let goalMarker = null;
let currentRouteData = null;
let activeWpMarker = null;

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
    dynamicSquat = (currentCb * Math.pow(speedKnots, 2)) / 100.0;

    const dynamicDraft = draft + dynamicSquat;
    const requiredDepth = dynamicDraft + baseUkc;

    if (squatVal) squatVal.innerText = dynamicSquat.toFixed(2) + 'm';
    if (cbVal) cbVal.innerText = currentCb.toFixed(2);
    if (dynDraftVal) dynDraftVal.innerText = dynamicDraft.toFixed(2) + 'm';
    if (dangerVal) dangerVal.innerText = requiredDepth.toFixed(2) + 'm';
}

// -----------------------------------------------------------------------------
// Reactive Route Recalculation Debouncer
// -----------------------------------------------------------------------------
let recalcDebounceTimer = null;
function triggerRouteRecalculation(immediate = false) {
    if (!startPoint || !goalPoint) return;
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
    draftSlider.addEventListener('change', () => {
        triggerRouteRecalculation(true);
    });

    speedSlider.addEventListener('input', (e) => {
        speedKnots = parseFloat(e.target.value);
        speedVal.innerText = speedKnots + ' kts';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    speedSlider.addEventListener('change', () => {
        triggerRouteRecalculation(true);
    });

    ukcSlider.addEventListener('input', (e) => {
        baseUkc = parseFloat(e.target.value);
        ukcVal.innerText = baseUkc.toFixed(1) + 'm';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    ukcSlider.addEventListener('change', () => {
        triggerRouteRecalculation(true);
    });

    if (radiusSlider) {
        radiusSlider.addEventListener('input', (e) => {
            turningRadius = parseFloat(e.target.value);
            if (radiusVal) radiusVal.innerText = Math.round(turningRadius) + 'm';
            markCustomProfile();
            triggerRouteRecalculation(false);
        });
        radiusSlider.addEventListener('change', () => {
            triggerRouteRecalculation(true);
        });
    }

    if (xtdSlider) {
        xtdSlider.addEventListener('input', (e) => {
            portXtdM = parseFloat(e.target.value);
            const nm = (portXtdM / 1852.0).toFixed(2);
            if (xtdVal) xtdVal.innerText = `${Math.round(portXtdM)}m (${nm} NM)`;
            triggerRouteRecalculation(false);
        });
        xtdSlider.addEventListener('change', () => {
            triggerRouteRecalculation(true);
        });
    }
}

// -----------------------------------------------------------------------------
// Map Layer Setup
// -----------------------------------------------------------------------------
map.on('load', () => {
    // Safety Corridor Swath (XTD Buffer)
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
            'line-width': 1.5,
            'line-opacity': 0.45,
            'line-dasharray': [3, 3]
        }
    });

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

    // -------------------------------------------------------------------------
    // S-57 / ENC Fairways, TSS, & Navigational Restrictions Layers
    // -------------------------------------------------------------------------
    fetch('/api/fairways')
        .then(res => res.json())
        .then(fairwaysData => {
            map.addSource('fairways-data', {
                type: 'geojson',
                data: fairwaysData
            });

            // Fairways & TSS Corridors (Fill)
            map.addLayer({
                id: 'fairways-fill',
                type: 'fill',
                source: 'fairways-data',
                filter: ['!=', ['get', 'category'], 'restricted'],
                paint: {
                    'fill-color': '#06b6d4',
                    'fill-opacity': 0.12
                }
            }, 'route-layer');

            // Fairways & TSS Corridors (Nautical Dashed Boundary)
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
            }, 'route-layer');

            // Restricted / Danger Areas (Fill)
            map.addLayer({
                id: 'restricted-fill',
                type: 'fill',
                source: 'fairways-data',
                filter: ['==', ['get', 'category'], 'restricted'],
                paint: {
                    'fill-color': '#ef4444',
                    'fill-opacity': 0.18
                }
            }, 'route-layer');

            // Restricted / Danger Areas (Boundary)
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
            }, 'route-layer');

            // Interactive Nautical Feature Inspection Popup
            const navPopup = new maplibregl.Popup({
                closeButton: true,
                closeOnClick: true
            });

            ['fairways-fill', 'restricted-fill'].forEach(layerId => {
                map.on('click', layerId, (e) => {
                    if (e.features && e.features.length) {
                        const f = e.features[0];
                        const p = f.properties;
                        const isRestricted = p.category === 'restricted';
                        const tagColor = isRestricted ? '#ef4444' : '#06b6d4';
                        const tagTitle = isRestricted ? 'RESTRICTED / DANGER AREA' : (p.category === 'tss' ? 'TRAFFIC SEPARATION SCHEME' : 'NAVIGATION FAIRWAY');

                        navPopup.setLngLat(e.lngLat)
                            .setHTML(`
                                <div style="font-size: 11px; line-height: 1.4; min-width: 200px;">
                                    <div style="font-weight: 700; color: ${tagColor}; font-size: 10px; letter-spacing: 0.5px; margin-bottom: 2px;">
                                        [${p.obj_type || 'NAV'}] ${tagTitle}
                                    </div>
                                    <div style="font-size: 12px; font-weight: 600; color: #fff; margin-bottom: 3px;">
                                        ${p.name || 'Navigational Area'}
                                    </div>
                                    <div style="color: #94a3b8; font-size: 11px;">
                                        ${p.description || p.restriction || ''}
                                    </div>
                                    ${p.min_depth_m ? `<div style="margin-top: 4px; font-family: monospace; color: #38bdf8;">Depth: ${p.min_depth_m}m</div>` : ''}
                                </div>
                            `)
                            .addTo(map);
                    }
                });

                map.on('mouseenter', layerId, () => {
                    map.getCanvas().style.cursor = 'pointer';
                });
                map.on('mouseleave', layerId, () => {
                    map.getCanvas().style.cursor = '';
                });
            });
        })
        .catch(err => console.warn('Could not load vector fairways:', err));

    // Navigational Constraints Checkbox Handlers
    if (toggleFairwaysCheckbox) {
        toggleFairwaysCheckbox.addEventListener('change', (e) => {
            const vis = e.target.checked ? 'visible' : 'none';
            ['fairways-fill', 'fairways-line', 'restricted-fill', 'restricted-line'].forEach(id => {
                if (map.getLayer(id)) {
                    map.setLayoutProperty(id, 'visibility', vis);
                }
            });
        });
    }

    if (prioritizeFairwaysCheckbox) {
        prioritizeFairwaysCheckbox.addEventListener('change', (e) => {
            fairwayPreference = e.target.checked ? 1.0 : 0.0;
            if (startPoint && goalPoint) {
                calculateRoute();
            }
        });
    }

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
    if (activeWpMarker) activeWpMarker.remove();

    startPoint = null;
    goalPoint = null;
    startMarker = null;
    goalMarker = null;
    activeWpMarker = null;
    currentRouteData = null;

    startCoordTxt.innerText = 'A: Not set';
    goalCoordTxt.innerText = 'B: Not set';

    map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
    if (map.getSource('route-corridor')) {
        map.getSource('route-corridor').setData({ type: 'FeatureCollection', features: [] });
    }
    if (bottomDrawer) bottomDrawer.style.display = 'none';
    if (exportActions) exportActions.style.display = 'none';
    if (waypointsTbody) waypointsTbody.innerHTML = '';
    if (wpBadge) wpBadge.innerText = '0 WP';
    if (safetyDashboard) safetyDashboard.innerHTML = '';
    if (safetyBadge) {
        safetyBadge.innerText = 'OK';
        safetyBadge.className = 'badge-safe';
    }
    statusMsg.innerText = '';
});

// -----------------------------------------------------------------------------
// Drawer Tabs & Panel Controls
// -----------------------------------------------------------------------------
const drawerTabs = [
    { btn: tabProfileBtn, pane: paneProfile },
    { btn: tabWaypointsBtn, pane: paneWaypoints },
    { btn: tabSafetyBtn, pane: paneSafety }
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
// Waypoints Table Rendering (Passage Plan)
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

            map.flyTo({
                center: [wp.lon, wp.lat],
                zoom: Math.max(map.getZoom(), 10.0),
                speed: 1.2
            });
        });

        waypointsTbody.appendChild(tr);
    });
}

// -----------------------------------------------------------------------------
// Route Export Helpers (GPX & RTZ)
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
            ukc: baseUkc,
            turning_radius_m: turningRadius,
            fairway_preference: fairwayPreference,
            block_coefficient: currentCb,
            port_xtd_m: portXtdM,
            stbd_xtd_m: portXtdM
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
            renderSafetyDashboard(data.safety_check);

            if (data.profile && data.profile.length) {
                renderProfileChart(data.profile, payload.draft + dynamicSquat + payload.ukc);
            }

            if (bottomDrawer) bottomDrawer.style.display = 'flex';
            if (exportActions) exportActions.style.display = 'inline-flex';
        } else {
            const err = await res.json().catch(() => ({}));
            statusMsg.innerText = '❌ ' + (err.detail || 'No navigable passage found.');
            statusMsg.style.color = 'var(--status-danger)';
            map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('route-corridor')) {
                map.getSource('route-corridor').setData({ type: 'FeatureCollection', features: [] });
            }
            renderSafetyDashboard(null);
            if (bottomDrawer) bottomDrawer.style.display = 'none';
            if (exportActions) exportActions.style.display = 'none';
            currentRouteData = null;
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
// ECDIS Route Safety Verification Dashboard Rendering (IEC 61174)
// -----------------------------------------------------------------------------
function renderSafetyDashboard(safetyData) {
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
            <span style="font-size: 10px; opacity: 0.8;">IEC 61174:2015 §6.8</span>
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
    } else {
        html += `
            <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.2); padding: 14px; border-radius: 4px; color: #a7f3d0; line-height: 1.5;">
                ✓ <b>Safety Contour & Corridor Verified:</b> Route line and swept cross-track corridor (XTD) maintain required Under-Keel Clearance (UKC) across all soundings. No restricted or danger zones violated. Rate of Turn conforms to bridge maneuvering limits.
            </div>
        `;
    }

    safetyDashboard.innerHTML = html;
}
