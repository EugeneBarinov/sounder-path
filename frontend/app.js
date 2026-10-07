/**
 * SeaPath ECDIS - Профессиональный модуль морской навигации и планирования переходов
 * =================================================================================
 * Соответствует стандартам IMO Res. A.893(21), IEC 61174:2015 и PIANC MarCom WG 121:
 * - Реактивная многоточечная штурманская прокладка с перетаскиванием путевых точек на карте.
 * - Гидродинамический бюджет глубин (PIANC DUKC): проседание (Squat), крен на циркуляции, волнение, прилив.
 * - Полоса безопасности (XTD) и сопрягающие дуги циркуляции с точками перекладки руля (WOP).
 * - Локализация лимитирующих отмелей (Bottleneck) с рекомендациями адаптации скорости и приливными окнами.
 * - Навигационный симулятор мостика в реальном времени с виртуальным эхолотом и телеметрией.
 */

// -----------------------------------------------------------------------------
// 1. Инициализация морской карты MapLibre GL
// -----------------------------------------------------------------------------
const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: [34.5, 44.8], // Региональный обзор побережья Крыма и Черного моря
    zoom: 8.0,
    pitch: 0,
    attributionControl: false
});

// -----------------------------------------------------------------------------
// 2. Ссылки на элементы интерфейса
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

// Приборы симулятора мостика
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

// Элементы плавающей панели ECDIS Topbar
const topbarAddWpBtn = document.getElementById('topbar-add-wp');
const toolEblVrmBtn = document.getElementById('tool-ebl-vrm');
const hudCursorCoords = document.getElementById('hud-cursor-coords');
const hudCursorDepth = document.getElementById('hud-cursor-depth');

// Элементы панели EBL / VRM
const eblVrmOverlay = document.getElementById('ebl-vrm-overlay');
const eblCloseBtn = document.getElementById('ebl-close-btn');
const eblBearingVal = document.getElementById('ebl-bearing-val');
const eblRecipVal = document.getElementById('ebl-recip-val');
const eblDistVal = document.getElementById('ebl-dist-val');
const eblDistMVal = document.getElementById('ebl-dist-m-val');
const eblEtaVal = document.getElementById('ebl-eta-val');
const eblSpdRef = document.getElementById('ebl-spd-ref');
const eblPromptText = document.getElementById('ebl-prompt-text');

// -----------------------------------------------------------------------------
// 3. Пресеты судов и состояние навигации
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

// Список путевых точек
let plannedWaypoints = []; // [{ id: number, coords: [lon, lat], marker: Marker, role: 'start'|'via'|'goal' }]
let nextWpId = 1;
let isAddingViaMode = false;
let currentRouteData = null;
let activeWpMarker = null;

// -----------------------------------------------------------------------------
// 4. График батиметрического профиля (Chart.js)
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
                label: 'Рельеф морского дна (м)',
                data: [],
                borderColor: '#38bdf8',
                backgroundColor: 'rgba(56, 189, 248, 0.15)',
                fill: true,
                tension: 0.3,
                pointRadius: 0
            },
            {
                label: 'Изобат безопасности киля (Осадка + Squat + UKC)',
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
        onHover: (event, activeElements) => {
            if (activeElements && activeElements.length > 0 && currentRouteData && currentRouteData.profile) {
                const idx = activeElements[0].index;
                const pt = currentRouteData.profile[idx];
                if (pt && pt.lon !== undefined && pt.lat !== undefined) {
                    highlightRouteProfilePoint([pt.lon, pt.lat], pt);
                }
            } else {
                clearRouteProfileHighlight();
            }
        },
        scales: {
            y: {
                reverse: true,
                title: { display: true, text: 'Глубина (Метры от нуля глубин LAT)' },
                grid: { color: 'rgba(255, 255, 255, 0.05)' }
            },
            x: {
                title: { display: true, text: 'Пройденная дистанция по маршруту' },
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

let profileHoverPopup = null;

function highlightRouteProfilePoint(coords, pt) {
    if (!map.getSource('profile-hover')) return;
    map.getSource('profile-hover').setData({
        type: 'Feature',
        properties: {},
        geometry: { type: 'Point', coordinates: coords }
    });
    const distNm = (pt.distance_from_start_m / 1852.0).toFixed(1);
    const depthM = pt.depth.toFixed(1);
    const ukcM = pt.clearance.toFixed(2);
    
    if (!profileHoverPopup) {
        profileHoverPopup = new maplibregl.Popup({
            closeButton: false,
            closeOnClick: false,
            offset: 14
        });
    }
    profileHoverPopup
        .setLngLat(coords)
        .setHTML(`
            <div class="map-profile-tooltip">
                <div><b>Дистанция:</b> ${distNm} ММ</div>
                <div><b>Глубина:</b> ${depthM} м | <b>UKC:</b> ${ukcM} м</div>
            </div>
        `)
        .addTo(map);
}

function clearRouteProfileHighlight() {
    if (map.getSource('profile-hover')) {
        map.getSource('profile-hover').setData({ type: 'FeatureCollection', features: [] });
    }
    if (profileHoverPopup) {
        profileHoverPopup.remove();
    }
}

const depthChartCanvas = document.getElementById('depth-chart');
if (depthChartCanvas) {
    depthChartCanvas.addEventListener('mouseleave', clearRouteProfileHighlight);
}

// -----------------------------------------------------------------------------
// 5. Расчет гидродинамических параметров судна
// -----------------------------------------------------------------------------
function updateVesselPhysics() {
    // Формула проседания Баррасса: (Cb * V^2) / 100
    dynamicSquat = (currentCb * Math.pow(speedKnots, 2)) / 100.0;
    const waveAllowance = 0.35 * waveHeightM;
    const dynamicDraft = draft + dynamicSquat + waveAllowance;
    const requiredChartDepth = Math.max(0.5, dynamicDraft + baseUkc - tideOffsetM);

    if (squatVal) squatVal.innerText = dynamicSquat.toFixed(2) + 'м';
    if (cbVal) cbVal.innerText = currentCb.toFixed(2);
    if (dynDraftVal) dynDraftVal.innerText = dynamicDraft.toFixed(2) + 'м';
    if (dangerVal) dangerVal.innerText = requiredChartDepth.toFixed(2) + 'м';
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
// 6. Обработчики изменений параметров
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

            draftVal.innerText = draft.toFixed(1) + 'м';
            speedVal.innerText = speedKnots + ' уз';
            ukcVal.innerText = baseUkc.toFixed(1) + 'м';
            if (radiusVal) radiusVal.innerText = Math.round(turningRadius) + 'м';

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
        draftVal.innerText = draft.toFixed(1) + 'м';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    draftSlider.addEventListener('change', () => triggerRouteRecalculation(true));

    speedSlider.addEventListener('input', (e) => {
        speedKnots = parseFloat(e.target.value);
        speedVal.innerText = speedKnots + ' уз';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    speedSlider.addEventListener('change', () => triggerRouteRecalculation(true));

    ukcSlider.addEventListener('input', (e) => {
        baseUkc = parseFloat(e.target.value);
        ukcVal.innerText = baseUkc.toFixed(1) + 'м';
        markCustomProfile();
        updateVesselPhysics();
        triggerRouteRecalculation(false);
    });
    ukcSlider.addEventListener('change', () => triggerRouteRecalculation(true));

    if (radiusSlider) {
        radiusSlider.addEventListener('input', (e) => {
            turningRadius = parseFloat(e.target.value);
            if (radiusVal) radiusVal.innerText = Math.round(turningRadius) + 'м';
            markCustomProfile();
            triggerRouteRecalculation(false);
        });
        radiusSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }

    if (tideSlider) {
        tideSlider.addEventListener('input', (e) => {
            tideOffsetM = parseFloat(e.target.value);
            if (tideVal) tideVal.innerText = `${tideOffsetM >= 0 ? '+' : ''}${tideOffsetM.toFixed(1)}м`;
            updateVesselPhysics();
            triggerRouteRecalculation(false);
        });
        tideSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }

    if (waveSlider) {
        waveSlider.addEventListener('input', (e) => {
            waveHeightM = parseFloat(e.target.value);
            if (waveVal) waveVal.innerText = `${waveHeightM.toFixed(1)}м`;
            updateVesselPhysics();
            triggerRouteRecalculation(false);
        });
        waveSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }

    if (xtdSlider) {
        xtdSlider.addEventListener('input', (e) => {
            portXtdM = parseFloat(e.target.value);
            const nm = (portXtdM / 1852.0).toFixed(2);
            if (xtdVal) xtdVal.innerText = `${Math.round(portXtdM)}м (${nm} ММ)`;
            triggerRouteRecalculation(false);
        });
        xtdSlider.addEventListener('change', () => triggerRouteRecalculation(true));
    }
}

// -----------------------------------------------------------------------------
// 7. Слои навигационной карты (Батиметрия, Фарватеры, Коридор XTD, Дуги циркуляции)
// -----------------------------------------------------------------------------
map.on('load', () => {
    // 1. Слой полосы безопасности (XTD)
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

    // 2. Основная линия маршрута
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

    // 3. Дуги циркуляции и точки перекладки руля (WOP)
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

    // 4. Маркер лимитирующей банки (Bottleneck)
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

    // 5. Батиметрическая растровая подложка с ТОЧНЫМИ географическими координатами
    // Координаты рассчитаны строго из метаданных GeoTIFF E8_2024.tif (EPSG:3857)
    const exactHeatmapCoords = [
        [33.122917, 47.300000], // Top-Left: Северо-Западная точка морского сектора
        [41.000046, 47.300000], // Top-Right: Северо-Восточная точка (Тамань / Азов)
        [41.000046, 43.122706], // Bottom-Right: Юго-Восточная точка
        [33.122917, 43.122706]  // Bottom-Left: Юго-Западная точка
    ];

    map.addSource('depth-heatmap', {
        type: 'image',
        url: '/heatmap.png',
        coordinates: exactHeatmapCoords
    });

    map.addLayer({
        id: 'depth-heatmap-layer',
        type: 'raster',
        source: 'depth-heatmap',
        paint: { 'raster-opacity': 0.82 }
    }, 'route-corridor-fill');

    // 6. Векторные фарватеры, системы разделения движения (TSS) и запретные зоны
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
        .catch(err => console.warn('Не удалось загрузить векторные фарватеры:', err));

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

    // 7. Слой интерактивного указателя профиля глубин на карте
    map.addSource('profile-hover', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'profile-hover-circle',
        type: 'circle',
        source: 'profile-hover',
        paint: {
            'circle-color': '#38bdf8',
            'circle-radius': 7.5,
            'circle-stroke-width': 3,
            'circle-stroke-color': '#ffffff'
        }
    });

    // 8. Слой электронного пеленгатора и дальности EBL / VRM
    map.addSource('ebl-vrm', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'ebl-vrm-range-circle',
        type: 'line',
        source: 'ebl-vrm',
        filter: ['==', ['get', 'role'], 'vrm_circle'],
        paint: {
            'line-color': '#38bdf8',
            'line-width': 1.5,
            'line-dasharray': [4, 2],
            'line-opacity': 0.85
        }
    });

    map.addLayer({
        id: 'ebl-vrm-bearing-line',
        type: 'line',
        source: 'ebl-vrm',
        filter: ['==', ['get', 'role'], 'ebl_line'],
        paint: {
            'line-color': '#38bdf8',
            'line-width': 2.0,
            'line-opacity': 0.95
        }
    });

    map.addLayer({
        id: 'ebl-vrm-center-point',
        type: 'circle',
        source: 'ebl-vrm',
        filter: ['==', ['get', 'role'], 'ebl_center'],
        paint: {
            'circle-color': '#38bdf8',
            'circle-radius': 5.0,
            'circle-stroke-width': 2,
            'circle-stroke-color': '#0f172a'
        }
    });

    // Интерактивная вставка путевой точки по клику на линию маршрута
    map.on('mouseenter', 'route-layer', () => {
        if (!isEblVrmActive && !isAddingViaMode) {
            map.getCanvas().style.cursor = 'copy';
        }
    });

    map.on('mouseleave', 'route-layer', () => {
        if (!isEblVrmActive && !isAddingViaMode) {
            map.getCanvas().style.cursor = '';
        }
    });

    map.on('click', 'route-layer', (e) => {
        if (isEblVrmActive || isAddingViaMode) return;
        if (plannedWaypoints.length < 2) return;

        const clickPt = [e.lngLat.lng, e.lngLat.lat];
        let bestLegIndex = 0;
        let minPerpDist = Infinity;

        for (let i = 0; i < plannedWaypoints.length - 1; i++) {
            const p1 = plannedWaypoints[i].coords;
            const p2 = plannedWaypoints[i + 1].coords;
            const d = distToSegmentSquared(clickPt, p1, p2);
            if (d < minPerpDist) {
                minPerpDist = d;
                bestLegIndex = i;
            }
        }

        const newWp = {
            id: nextWpId++,
            coords: clickPt,
            role: 'via',
            marker: null
        };
        newWp.marker = createWaypointMarker(newWp);
        plannedWaypoints.splice(bestLegIndex + 1, 0, newWp);

        renderWaypointsListUI();
        calculateRoute();
    });

    // Интерактивный курсор: координаты и глубина дна под курсором в реальном времени
    let depthQueryTimer = null;
    map.on('mousemove', (e) => {
        const lng = e.lngLat.lng;
        const lat = e.lngLat.lat;

        if (hudCursorCoords) {
            hudCursorCoords.innerText = formatCoordinates(lat, lng);
        }

        if (isEblVrmActive && eblOrigin && !isEblLocked) {
            updateEblVrm([lng, lat]);
        }

        if (hudCursorDepth) {
            if (depthQueryTimer) clearTimeout(depthQueryTimer);
            depthQueryTimer = setTimeout(() => {
                fetch(`/api/depth?lon=${lng.toFixed(5)}&lat=${lat.toFixed(5)}`)
                    .then(r => r.json())
                    .then(data => {
                        if (!data || !data.in_bounds) {
                            hudCursorDepth.innerText = 'Вне зоны';
                            hudCursorDepth.style.color = 'var(--text-muted)';
                        } else if (data.is_land) {
                            hudCursorDepth.innerText = 'Суша (0.0 м)';
                            hudCursorDepth.style.color = 'var(--status-danger)';
                        } else {
                            hudCursorDepth.innerText = `${data.depth.toFixed(1)} м`;
                            const isSafe = data.depth >= (draft + dynamicSquat + baseUkc - tideOffsetM);
                            hudCursorDepth.style.color = isSafe ? 'var(--status-success)' : 'var(--status-danger)';
                        }
                    })
                    .catch(() => {});
            }, 80);
        }
    });

    updateVesselPhysics();
});

// -----------------------------------------------------------------------------
// 8. Управление путевыми точками и перетаскивание (Drag-and-Drop)
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
            <div class="wp wp-start"><div class="wp-indicator"></div><span>A (Отход): Кликните на карте</span></div>
            <div class="wp wp-goal"><div class="wp-indicator"></div><span>B (Приход): Кликните на карте</span></div>
        `;
        return;
    }

    plannedWaypoints.forEach((wp, idx) => {
        const row = document.createElement('div');
        row.className = 'wp-item-row';

        let badgeColor = '#f59e0b';
        let label = `РТ${idx + 1}`;
        if (wp.role === 'start') {
            badgeColor = '#10b981';
            label = 'A (Отход)';
        } else if (wp.role === 'goal') {
            badgeColor = '#ef4444';
            label = 'B (Приход)';
        }

        const coordStr = formatCoordinates(wp.coords[1], wp.coords[0]);
        row.innerHTML = `
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${badgeColor};"></span>
                <span style="font-weight: 600;">${label}</span>
                <span style="color: #94a3b8; font-family: monospace;">${coordStr}</span>
            </div>
            ${plannedWaypoints.length > 2 ? `<button class="wp-item-del-btn" data-id="${wp.id}" title="Удалить путевую точку">✕</button>` : ''}
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
    clearRouteProfileHighlight();

    renderWaypointsListUI();
    renderWaypointsTable([]);
    renderSafetyDashboard(null, null, null);

    if (bottomDrawer) bottomDrawer.style.display = 'none';
    if (exportActions) exportActions.style.display = 'none';
    statusMsg.innerText = '';
    stopSimulator();
}

function distToSegmentSquared(p, v, w) {
    const l2 = (v[0] - w[0]) * (v[0] - w[0]) + (v[1] - w[1]) * (v[1] - w[1]);
    if (l2 === 0) return (p[0] - v[0]) * (p[0] - v[0]) + (p[1] - v[1]) * (p[1] - v[1]);
    let t = ((p[0] - v[0]) * (w[0] - v[0]) + (p[1] - v[1]) * (w[1] - v[1])) / l2;
    t = Math.max(0, Math.min(1, t));
    const proj = [v[0] + t * (w[0] - v[0]), v[1] + t * (w[1] - v[1])];
    return (p[0] - proj[0]) * (p[0] - proj[0]) + (p[1] - proj[1]) * (p[1] - proj[1]);
}

// -----------------------------------------------------------------------------
// Электронный пеленгатор EBL & маркер дистанции VRM (IMO MSC.192(79))
// -----------------------------------------------------------------------------
let isEblVrmActive = false;
let eblOrigin = null;
let isEblLocked = false;

function toggleEblVrmMode() {
    isEblVrmActive = !isEblVrmActive;
    if (isEblVrmActive) {
        if (toolEblVrmBtn) toolEblVrmBtn.classList.add('active');
        if (eblVrmOverlay) eblVrmOverlay.style.display = 'block';
        if (eblPromptText) eblPromptText.innerText = 'Кликните на карте для фиксации центра отсчета (Origin)';
        eblOrigin = null;
        isEblLocked = false;
        map.getCanvas().style.cursor = 'crosshair';
    } else {
        closeEblVrm();
    }
}

function closeEblVrm() {
    isEblVrmActive = false;
    eblOrigin = null;
    isEblLocked = false;
    if (toolEblVrmBtn) toolEblVrmBtn.classList.remove('active');
    if (eblVrmOverlay) eblVrmOverlay.style.display = 'none';
    map.getCanvas().style.cursor = '';
    clearEblVrmGraphics();
}

function clearEblVrmGraphics() {
    if (map.getSource('ebl-vrm')) {
        map.getSource('ebl-vrm').setData({ type: 'FeatureCollection', features: [] });
    }
}

function calculateGeodesicCircle(center, radiusM, numPoints = 64) {
    const coords = [];
    const lon1 = center[0] * Math.PI / 180;
    const lat1 = center[1] * Math.PI / 180;
    const dOverR = radiusM / 6371000.0;

    for (let i = 0; i <= numPoints; i++) {
        const brg = (i * 360.0 / numPoints) * Math.PI / 180;
        const lat2 = Math.asin(Math.sin(lat1) * Math.cos(dOverR) + Math.cos(lat1) * Math.sin(dOverR) * Math.cos(brg));
        const lon2 = lon1 + Math.atan2(
            Math.sin(brg) * Math.sin(dOverR) * Math.cos(lat1),
            Math.cos(dOverR) - Math.sin(lat1) * Math.sin(lat2)
        );
        coords.push([lon2 * 180 / Math.PI, lat2 * 180 / Math.PI]);
    }
    return coords;
}

function updateEblVrm(targetCoords) {
    if (!eblOrigin) return;

    const lon1 = eblOrigin[0], lat1 = eblOrigin[1];
    const lon2 = targetCoords[0], lat2 = targetCoords[1];

    const phi1 = lat1 * Math.PI / 180;
    const phi2 = lat2 * Math.PI / 180;
    const dLambda = (lon2 - lon1) * Math.PI / 180;
    const y = Math.sin(dLambda) * Math.cos(phi2);
    const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
    let brgDeg = (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    let recipDeg = (brgDeg + 180) % 360;

    const dPhi = (lat2 - lat1) * Math.PI / 180;
    const a = Math.sin(dPhi / 2) * Math.sin(dPhi / 2) +
              Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) * Math.sin(dLambda / 2);
    const distM = 6371000.0 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    const distNm = distM / 1852.0;

    const spd = Math.max(1.0, speedKnots);
    const etaHours = distNm / spd;
    const hrs = Math.floor(etaHours);
    const mins = Math.round((etaHours - hrs) * 60);
    const etaStr = hrs > 0 ? `${hrs}ч ${mins}мин` : `${mins} мин`;

    if (eblBearingVal) eblBearingVal.innerText = `${brgDeg.toFixed(1)}°T`;
    if (eblRecipVal) eblRecipVal.innerText = `${recipDeg.toFixed(1)}°T`;
    if (eblDistVal) eblDistVal.innerText = `${distNm.toFixed(2)} ММ`;
    if (eblDistMVal) eblDistMVal.innerText = `${Math.round(distM)} м`;
    if (eblSpdRef) eblSpdRef.innerText = speedKnots;
    if (eblEtaVal) eblEtaVal.innerText = etaStr;

    const circleCoords = calculateGeodesicCircle(eblOrigin, distM);
    const features = [
        {
            type: 'Feature',
            properties: { role: 'ebl_center' },
            geometry: { type: 'Point', coordinates: eblOrigin }
        },
        {
            type: 'Feature',
            properties: { role: 'ebl_line' },
            geometry: { type: 'LineString', coordinates: [eblOrigin, targetCoords] }
        },
        {
            type: 'Feature',
            properties: { role: 'vrm_circle' },
            geometry: { type: 'LineString', coordinates: circleCoords }
        }
    ];

    if (map.getSource('ebl-vrm')) {
        map.getSource('ebl-vrm').setData({ type: 'FeatureCollection', features });
    }
}

if (clearRouteBtn) {
    clearRouteBtn.addEventListener('click', clearRouteData);
}

function toggleAddWaypointMode() {
    isAddingViaMode = !isAddingViaMode;
    const label = isAddingViaMode ? '✕ Отмена РТ' : '+ Точка';
    const topbarLabel = isAddingViaMode ? '✕ Отмена РТ' : '+ Путевая точка';
    if (addWpBtn) {
        addWpBtn.innerText = label;
        addWpBtn.style.background = isAddingViaMode ? 'rgba(245, 158, 11, 0.25)' : '';
        addWpBtn.style.borderColor = isAddingViaMode ? '#f59e0b' : '';
        addWpBtn.style.color = isAddingViaMode ? '#fbbf24' : '';
    }
    if (topbarAddWpBtn) {
        topbarAddWpBtn.innerText = topbarLabel;
        topbarAddWpBtn.classList.toggle('active', isAddingViaMode);
    }
    map.getCanvas().style.cursor = isAddingViaMode ? 'crosshair' : '';
}

if (addWpBtn) addWpBtn.addEventListener('click', toggleAddWaypointMode);
if (topbarAddWpBtn) topbarAddWpBtn.addEventListener('click', toggleAddWaypointMode);

if (toolEblVrmBtn) toolEblVrmBtn.addEventListener('click', toggleEblVrmMode);
if (eblCloseBtn) eblCloseBtn.addEventListener('click', closeEblVrm);

window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (isEblVrmActive) closeEblVrm();
        if (isAddingViaMode) toggleAddWaypointMode();
    }
});

map.on('click', (e) => {
    const coords = [e.lngLat.lng, e.lngLat.lat];

    if (isEblVrmActive) {
        if (!eblOrigin) {
            eblOrigin = coords;
            if (eblPromptText) eblPromptText.innerText = 'Перемещайте курсор для замера. Клик — зафиксировать';
        } else if (!isEblLocked) {
            isEblLocked = true;
            if (eblPromptText) eblPromptText.innerText = 'Замер зафиксирован. Кликните для нового замера';
        } else {
            eblOrigin = coords;
            isEblLocked = false;
            if (eblPromptText) eblPromptText.innerText = 'Перемещайте курсор для замера. Клик — зафиксировать';
        }
        return;
    }

    if (isAddingViaMode) {
        addWaypoint(coords, 'via');
        toggleAddWaypointMode();
        return;
    }

    if (plannedWaypoints.length === 0) {
        addWaypoint(coords, 'start');
    } else if (plannedWaypoints.length === 1) {
        addWaypoint(coords, 'goal');
    }
});

// -----------------------------------------------------------------------------
// 9. Переключение вкладок нижней панели (Drawer)
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
// 10. Отрисовка таблицы путевых точек (План перехода)
// -----------------------------------------------------------------------------
function renderWaypointsTable(waypoints) {
    if (!waypointsTbody) return;
    waypointsTbody.innerHTML = '';

    if (!waypoints || !waypoints.length) {
        if (wpBadge) wpBadge.innerText = '0 РТ';
        return;
    }

    if (wpBadge) wpBadge.innerText = `${waypoints.length} РТ`;

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
        const radTxt = (idx === 0 || isLast || radVal <= 0) ? '—' : `${Math.round(radVal)}м`;
        const rotVal = wp.rot_deg_min !== undefined ? wp.rot_deg_min : 0.0;
        const wopVal = wp.wop_distance_m !== undefined ? wp.wop_distance_m : 0.0;

        let rotTxt = '—';
        if (!isLast && idx > 0 && Math.abs(turnVal) >= 0.5 && rotVal !== 0.0) {
            rotTxt = `${rotVal > 0 ? '+' : ''}${rotVal.toFixed(1)}°/мин`;
        }

        let wopTxt = '—';
        if (!isLast && idx > 0 && wopVal > 0) {
            wopTxt = `${Math.round(wopVal)}м`;
        }

        const depthTxt = `${depthVal.toFixed(1)}м`;
        let clrClass = 'val-clearance-safe';
        if (clrVal < 0.5) clrClass = 'val-clearance-crit';
        else if (clrVal < 1.5) clrClass = 'val-clearance-warn';
        const clrTxt = `<span class="${clrClass}">${clrVal.toFixed(2)}м</span>`;

        const latDmm = formatCoordinates(wp.lat, wp.lon).split(' ')[0];
        const lonDmm = formatCoordinates(wp.lat, wp.lon).split(' ')[1];

        tr.innerHTML = `
            <td><b>РТ${idx + 1}</b></td>
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
// 11. Вызов API и расчет безопасного маршрута
// -----------------------------------------------------------------------------
window.calculateRoute = async function () {
    if (plannedWaypoints.length < 2) {
        statusMsg.innerText = 'Пожалуйста, укажите точки отхода (A) и прихода (B) на карте.';
        statusMsg.style.color = 'var(--status-warning)';
        return;
    }

    statusMsg.innerText = 'Расчет безопасного маршрута перехода...';
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

            // Маркер лимитирующей банки (Bottleneck)
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
            const distNm = p.distance_nm !== undefined ? p.distance_nm.toFixed(1) + ' ММ' : '—';
            const eta = (p.eta_hours !== null && p.eta_hours !== undefined) ? p.eta_hours.toFixed(1) + ' ч' : '—';
            const clearance = p.min_clearance_m !== undefined ? p.min_clearance_m.toFixed(2) + ' м' : '—';
            const waypointsCount = p.waypoints || (data.waypoints ? data.waypoints.length : '—');

            statusMsg.innerHTML =
                `✓ <b>${distNm}</b> &nbsp;|&nbsp; Время перехода: <b>${eta}</b>` +
                ` &nbsp;|&nbsp; Мин. UKC: <b>${clearance}</b>` +
                ` &nbsp;|&nbsp; ${waypointsCount} РТ`;
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
            statusMsg.innerText = '❌ ' + (err.detail || 'Безопасный проход не найден при текущих параметрах осадки.');
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
        console.error('Ошибка расчета маршрута:', err);
        statusMsg.innerText = 'Ошибка связи с навигационным сервером.';
        statusMsg.style.color = 'var(--status-danger)';
    }
};

function renderProfileChart(profile, safeDepthLimit) {
    chartInstance.data.labels = profile.map((p) => {
        const nm = (p.distance_from_start_m / 1852.0).toFixed(1);
        return `${nm} ММ`;
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
// 12. Отрисовка панели аудита безопасности ECDIS (IEC 61174)
// -----------------------------------------------------------------------------
function renderSafetyDashboard(safetyData, bottleneckData, dukcData) {
    if (!safetyDashboard) return;
    if (!safetyData) {
        safetyDashboard.innerHTML = '<div style="color: #94a3b8; padding: 10px;">Данные проверки безопасности недоступны.</div>';
        if (safetyBadge) {
            safetyBadge.innerText = 'НОРМА';
            safetyBadge.className = 'badge-safe';
        }
        return;
    }

    if (safetyBadge) {
        if (safetyData.status === 'CRITICAL_HAZARD') {
            safetyBadge.innerText = 'ОПАСНОСТЬ';
            safetyBadge.className = 'badge-danger';
        } else if (safetyData.status === 'WARNING_ADVISORY') {
            safetyBadge.innerText = `${safetyData.alarms_count} ПРЕДУПР.`;
            safetyBadge.className = 'badge-warn';
        } else {
            safetyBadge.innerText = 'НОРМА';
            safetyBadge.className = 'badge-safe';
        }
    }

    const bannerClass = safetyData.status === 'CRITICAL_HAZARD'
        ? 'safety-banner-danger'
        : (safetyData.status === 'WARNING_ADVISORY' ? 'safety-banner-warning' : 'safety-banner-passed');

    let summaryText = safetyData.summary;
    if (safetyData.status === 'CRITICAL_HAZARD') {
        summaryText = 'Проверка безопасности ECDIS: ОТКЛОНЕНО. Обнаружен пробой безопасной глубины под килем.';
    } else if (safetyData.status === 'WARNING_ADVISORY') {
        summaryText = `Проверка безопасности ECDIS: ВНИМАНИЕ. Обнаружено ${safetyData.alarms_count} навигационных предупреждений.`;
    } else {
        summaryText = 'Проверка безопасности ECDIS: ПРОЙДЕНА. 100% соответствие запаса под килем и пределов маневрирования.';
    }

    const m = safetyData.metrics || {};
    let html = `
        <div class="safety-status-banner ${bannerClass}">
            <span>${summaryText}</span>
            <span style="font-size: 10px; opacity: 0.8;">Стандарты IEC 61174:2015 §6.8 / PIANC MarCom WG 121</span>
        </div>
        <div class="safety-metrics-grid">
            <div class="metric-box">
                <span class="metric-label">Глубина изобата безоп.</span>
                <span class="metric-val" style="color: #38bdf8;">${m.safety_depth_m !== undefined ? m.safety_depth_m.toFixed(2) + 'м' : '—'}</span>
            </div>
            <div class="metric-box">
                <span class="metric-label">Динам. Squat (PIANC)</span>
                <span class="metric-val" style="color: #38bdf8;">${m.dynamic_squat_m !== undefined ? m.dynamic_squat_m.toFixed(2) + 'м' : '—'}</span>
            </div>
            <div class="metric-box">
                <span class="metric-label">Мин. запас UKC над дном</span>
                <span class="metric-val ${m.min_clearance_m < 0 ? 'val-danger' : (m.min_clearance_m < 0.5 ? 'val-warn' : '')}" style="${m.min_clearance_m >= 0.5 ? 'color: #10b981;' : ''}">${m.min_clearance_m !== undefined ? m.min_clearance_m.toFixed(2) + 'м' : '—'}</span>
            </div>
            <div class="metric-box">
                <span class="metric-label">Мостиковый предел ROT</span>
                <span class="metric-val" style="color: #94a3b8;">${m.rot_threshold_deg_min !== undefined ? m.rot_threshold_deg_min + '°/мин' : '—'}</span>
            </div>
        </div>
    `;

    // Карточка лимитирующей банки (Bottleneck)
    if (bottleneckData && bottleneckData.shallowest_point) {
        const b = bottleneckData.shallowest_point;
        const isHazard = bottleneckData.is_grounding_hazard;
        html += `
            <div class="bottleneck-card ${isHazard ? '' : 'bottleneck-safe'}">
                <div class="bottleneck-header">
                    <span>${isHazard ? '⚠️ КРИТИЧЕСКИЙ ЛИМИТИРУЮЩИЙ СТВОР (ОТМЕЛЬ)' : '✓ КОНТРОЛЬНЫЙ ЛИМИТИРУЮЩИЙ СТВОР МАРШРУТА'}</span>
                    <span>Дистанция: ${b.distance_nm} ММ</span>
                </div>
                <div style="font-size: 11.5px; color: var(--text-secondary); line-height: 1.4;">
                    Глубина по карте: <b>${b.chart_depth_m}м</b> (с учетом прилива: <b>${b.effective_depth_m}м</b>) |
                    Чистый запас UKC: <b style="color: ${b.net_clearance_m < 0 ? '#ef4444' : '#10b981'};">${b.net_clearance_m}м</b> |
                    Макс. безопасная осадка: <b>${bottleneckData.max_allowable_draft_m}м</b>
                </div>
        `;

        if (bottleneckData.speed_adaptation && bottleneckData.speed_adaptation.is_viable) {
            const sa = bottleneckData.speed_adaptation;
            html += `
                <div class="advisory-box">
                    <b>💡 Рекомендация по адаптации скорости:</b>
                    Снизьте скорость с ${sa.current_speed_knots} уз до ${sa.recommended_speed_knots} уз на лимитирующем участке (${b.distance_nm} ММ от старта), чтобы уменьшить гидродинамическое проседание (Squat) на ${sa.squat_reduction_m}м и восстановить безопасный запас UKC.
                    <button id="apply-safe-speed-btn" style="margin-left: 8px; padding: 2px 8px; font-size: 11px; background: #38bdf8; color: #0f172a; border: none; border-radius: 2px; font-weight: 600; cursor: pointer;">
                        Применить ${sa.recommended_speed_knots} уз
                    </button>
                </div>
            `;
        }

        if (bottleneckData.tidal_window) {
            const tw = bottleneckData.tidal_window;
            html += `
                <div class="tidal-box">
                    <b>🌊 Расчет приливного окна M2:</b> ${tw.notes}
                </div>
            `;
        }

        html += `</div>`;
    }

    // Гидродинамический баланс DUKC
    if (dukcData) {
        html += `
            <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 4px; padding: 10px 12px; margin-bottom: 10px; font-size: 11.5px;">
                <div style="font-weight: 600; color: #38bdf8; margin-bottom: 6px;">📐 ГИДРОДИНАМИЧЕСКИЙ БАЛАНС ДВИЖЕНИЯ PIANC DUKC</div>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; color: var(--text-secondary);">
                    <div>Статическая осадка: <b>${dukcData.static_draft_m}м</b></div>
                    <div>Проседание Squat: <b>${dukcData.dynamic_squat_m}м</b></div>
                    <div>Циркуляционный крен: <b>+${dukcData.heel_sinkage_m}м</b> (${dukcData.heel_angle_deg}°)</div>
                    <div>Волновой запас (Hs): <b>+${dukcData.wave_allowance_m}м</b></div>
                    <div>Полная динамическая осадка: <b style="color: #f59e0b;">${dukcData.total_dynamic_draft_m}м</b></div>
                    <div>Эффективная глубина моря: <b style="color: #38bdf8;">${dukcData.effective_depth_m}м</b></div>
                </div>
            </div>
        `;
    }

    // Список навигационных алертов
    if (safetyData.alarms && safetyData.alarms.length) {
        html += '<div class="alarms-list">';
        safetyData.alarms.forEach(a => {
            const cardClass = a.severity === 'CRITICAL' ? 'alarm-critical' : 'alarm-warning';
            let ruTitle = a.title;
            let ruDetail = a.detail;

            if (a.code === 'ALM-01-GROUNDING-HAZARD') {
                ruTitle = 'Опасность посадки на мель';
                ruDetail = `Глубина дна пробивает изобат безопасности судна на дистанции ${a.distance_nm} ММ.`;
            } else if (a.code === 'ALM-02-CRITICAL-UKC') {
                ruTitle = 'Критически малый запас под килем';
                ruDetail = `Фактический клиренс под килем менее допустимого предела (0.35м) на дистанции ${a.distance_nm} ММ.`;
            } else if (a.code === 'ALM-03-EXCESSIVE-ROT') {
                ruTitle = `Высокая угловая скорость поворота (ROT) на РТ${a.waypoint_index || ''}`;
            }

            html += `
                <div class="alarm-card ${cardClass}">
                    <div class="alarm-header">
                        <span class="alarm-title">${ruTitle}</span>
                        <span class="alarm-code">[${a.code}]</span>
                    </div>
                    <div class="alarm-detail">${ruDetail}</div>
                </div>
            `;
        });
        html += '</div>';
    } else {
        html += `
            <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.2); padding: 14px; border-radius: 4px; color: #a7f3d0; line-height: 1.5;">
                ✓ <b>Аудит безопасности пройден:</b> Линия перехода и полоса безопасности (XTD) сохраняют требуемый запас под килем (UKC) на всех батиметрических промерах. Запретные зоны не нарушены. Угловые скорости поворотов соответствуют мостиковым нормам.
            </div>
        `;
    }

    safetyDashboard.innerHTML = html;

    const applySpeedBtn = document.getElementById('apply-safe-speed-btn');
    if (applySpeedBtn && bottleneckData && bottleneckData.speed_adaptation) {
        applySpeedBtn.addEventListener('click', () => {
            const safeV = bottleneckData.speed_adaptation.recommended_speed_knots;
            speedSlider.value = safeV;
            speedKnots = safeV;
            speedVal.innerText = safeV + ' уз';
            updateVesselPhysics();
            calculateRoute();
        });
    }
}

// -----------------------------------------------------------------------------
// 13. Симулятор проводки судна и приборы мостика
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
        simPlayBtn.innerText = '▶ Старт';
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

    // Интерполяция глубины по профилю
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

    if (hudDepthVal) hudDepthVal.innerText = `${(curDepth + tideOffsetM).toFixed(1)} м`;
    if (hudChartDepth) hudChartDepth.innerText = `${curDepth.toFixed(1)}м`;
    if (hudTide) hudTide.innerText = `${tideOffsetM >= 0 ? '+' : ''}${tideOffsetM.toFixed(1)}м`;
    if (hudUkcVal) {
        hudUkcVal.innerText = `${curClearance.toFixed(2)} м`;
        hudUkcVal.style.color = curClearance < 0.5 ? '#ef4444' : (curClearance < 1.5 ? '#f59e0b' : '#10b981');
    }
    if (hudUkcStatus) {
        hudUkcStatus.innerText = curClearance < 0 ? 'КРИТИЧЕСКАЯ ОПАСНОСТЬ МЕЛИ' : (curClearance < 0.5 ? 'МАЛЫЙ ЗАПАС' : 'БЕЗОПАСНЫЙ ХОД');
        hudUkcStatus.style.color = curClearance < 0.5 ? '#ef4444' : '#10b981';
    }
    if (hudHdgVal) hudHdgVal.innerText = `${Math.round(pos.heading).toString().padStart(3, '0')}°T`;
    if (hudSogVal) hudSogVal.innerText = `${speedKnots.toFixed(1)} уз`;

    // Расчет следующей путевой точки и точки перекладки руля (WOP)
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

        if (hudNextWpVal) hudNextWpVal.innerText = `РТ${nextWp.index || '—'}`;
        if (hudWpDist) hudWpDist.innerText = `${distNm} ММ`;
        if (hudWpEta) hudWpEta.innerText = etaStr;

        const wopDistM = nextWp.wop_distance_m || 0;
        const isApproachingWop = (distToNextWp <= wopDistM + 250 && distToNextWp >= wopDistM - 50);

        if (isApproachingWop && simAlarmBanner && Math.abs(nextWp.turn_angle_deg || 0) >= 4.0) {
            simAlarmBanner.style.display = 'block';
            simAlarmBanner.innerHTML = `
                ⚠️ <b>ПЕРЕКЛАДКА РУЛЯ (WOP):</b> Подход к РТ${nextWp.index}!
                Поворот на ${nextWp.rot_deg_min > 0 ? 'Правый' : 'Левый'} борт (${nextWp.leg_bearing_deg}°T) —
                Угловая скорость ROT: <b>${Math.abs(nextWp.rot_deg_min)}°/мин</b> (Радиус: ${nextWp.turn_radius_m}м)
            `;
        } else if (simAlarmBanner && !isApproachingWop) {
            simAlarmBanner.style.display = 'none';
        }
    }

    // Прогресс симуляции
    const progressPct = simState.totalDistanceM > 0 ? Math.min(100, (distM / simState.totalDistanceM) * 100) : 0;
    if (simProgressFill) simProgressFill.style.width = `${progressPct.toFixed(1)}%`;
    if (simProgressText) {
        const curNm = (distM / 1852.0).toFixed(1);
        const totNm = (simState.totalDistanceM / 1852.0).toFixed(1);
        simProgressText.innerText = `${curNm} / ${totNm} ММ (${Math.round(progressPct)}%)`;
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
            simPlayBtn.innerText = '▶ Повтор';
            simPlayBtn.disabled = false;
        }
        if (simPauseBtn) simPauseBtn.disabled = true;
        if (simAlarmBanner) {
            simAlarmBanner.style.display = 'block';
            simAlarmBanner.style.background = 'rgba(16, 185, 129, 0.2)';
            simAlarmBanner.style.borderColor = 'var(--status-success)';
            simAlarmBanner.style.color = '#6ee7b7';
            simAlarmBanner.innerHTML = '🏁 <b>ПЕРЕХОД ЗАВЕРШЕН:</b> Судно благополучно прибыло в порт назначения.';
        }
        return;
    }

    updateSimulatorHUD(simState.currentDistanceM);
    simState.animFrameId = requestAnimationFrame(simAnimationFrame);
}

if (simPlayBtn) {
    simPlayBtn.addEventListener('click', () => {
        if (!currentRouteData) {
            alert('Пожалуйста, постройте маршрут на карте перед запуском симулятора.');
            return;
        }
        if (simState.currentDistanceM >= simState.totalDistanceM) {
            simState.currentDistanceM = 0;
        }
        simState.isRunning = true;
        simState.lastTimestamp = null;
        simPlayBtn.disabled = true;
        simPlayBtn.innerText = '▶ В движении';
        if (simPauseBtn) simPauseBtn.disabled = false;
        simState.animFrameId = requestAnimationFrame(simAnimationFrame);
    });
}

if (simPauseBtn) {
    simPauseBtn.addEventListener('click', () => {
        simState.isRunning = false;
        if (simState.animFrameId) cancelAnimationFrame(simState.animFrameId);
        simPlayBtn.disabled = false;
        simPlayBtn.innerText = '▶ Продолжить';
        simPauseBtn.disabled = true;
    });
}

if (simResetBtn) {
    simResetBtn.addEventListener('click', () => {
        simState.isRunning = false;
        simState.currentDistanceM = 0;
        if (simState.animFrameId) cancelAnimationFrame(simState.animFrameId);
        simPlayBtn.disabled = false;
        simPlayBtn.innerText = '▶ Старт';
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
// 14. Экспорт маршрута (GPX и IEC 61174 RTZ 1.1)
// -----------------------------------------------------------------------------
async function triggerDownload(url, filename, payload) {
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error('Запрос экспорта завершился с ошибкой: ' + res.status);
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
        console.error('Ошибка экспорта:', e);
        alert('Не удалось экспортировать маршрут: ' + e.message);
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
