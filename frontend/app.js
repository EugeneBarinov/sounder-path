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
// 1. Инициализация морской карты MapLibre GL с темами IHO S-52
// -----------------------------------------------------------------------------
const MAP_THEMES = {
    'ecdis-night': {
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}'],
        contourColor: '#0369a1',
        contourIndexColor: '#38bdf8',
        contourHalo: 'rgba(15, 23, 42, 0.9)',
        labelColor: '#93c5fd',
        soundingColor: '#94a3b8',
        maxzoom: 16
    },
    'ecdis-day': {
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}'],
        contourColor: '#0284c7',
        contourIndexColor: '#0369a1',
        contourHalo: 'rgba(255, 255, 255, 0.95)',
        labelColor: '#0c4a6e',
        soundingColor: '#1e293b',
        maxzoom: 16
    },
    'ocean': {
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}'],
        contourColor: '#0284c7',
        contourIndexColor: '#0369a1',
        contourHalo: 'rgba(255, 255, 255, 0.9)',
        labelColor: '#075985',
        soundingColor: '#0f172a',
        maxzoom: 13
    },
    'satellite': {
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        contourColor: '#38bdf8',
        contourIndexColor: '#7dd3fc',
        contourHalo: 'rgba(0, 0, 0, 0.85)',
        labelColor: '#bae6fd',
        soundingColor: '#f1f5f9',
        maxzoom: 18
    }
};

let currentChartTheme = 'ecdis-night';

const map = new maplibregl.Map({
    container: 'map',
    style: {
        version: 8,
        glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
        sources: {
            'base-night': { type: 'raster', tiles: MAP_THEMES['ecdis-night'].tiles, tileSize: 256, maxzoom: 16 },
            'base-day': { type: 'raster', tiles: MAP_THEMES['ecdis-day'].tiles, tileSize: 256, maxzoom: 16 },
            'base-ocean': { type: 'raster', tiles: MAP_THEMES['ocean'].tiles, tileSize: 256, maxzoom: 13 },
            'base-satellite': { type: 'raster', tiles: MAP_THEMES['satellite'].tiles, tileSize: 256, maxzoom: 18 }
        },
        layers: [
            { id: 'layer-base-night', type: 'raster', source: 'base-night', layout: { visibility: 'visible' } },
            { id: 'layer-base-day', type: 'raster', source: 'base-day', layout: { visibility: 'none' } },
            { id: 'layer-base-ocean', type: 'raster', source: 'base-ocean', layout: { visibility: 'none' } },
            { id: 'layer-base-satellite', type: 'raster', source: 'base-satellite', layout: { visibility: 'none' } }
        ]
    },
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
const fuelSlider = document.getElementById('fuel-slider');
const fuelVal = document.getElementById('fuel-val');

const toggleFairwaysCheckbox = document.getElementById('toggle-fairways');
const prioritizeFairwaysCheckbox = document.getElementById('prioritize-fairways');
const xtdSlider = document.getElementById('xtd-slider');
const xtdVal = document.getElementById('xtd-val');

const chartThemeSelect = document.getElementById('chart-theme-select');
const toggleHillshadeCheckbox = document.getElementById('toggle-hillshade');
const toggleContoursCheckbox = document.getElementById('toggle-contours');
const toggleSafetyContourCheckbox = document.getElementById('toggle-safety-contour');
const toggleSoundingsCheckbox = document.getElementById('toggle-soundings');
const toggleSeamarksCheckbox = document.getElementById('toggle-seamarks');
const toggleAnchoragesCheckbox = document.getElementById('toggle-anchorages');

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
const toolMuteAlarmBtn = document.getElementById('tool-mute-alarm-btn');
const toolRecenterBtn = document.getElementById('tool-recenter-btn');
const hudCursorCoords = document.getElementById('hud-cursor-coords');
const hudCursorDepth = document.getElementById('hud-cursor-depth');
const topbarAlarmPill = document.getElementById('topbar-alarm-pill');
const topbarAlarmText = document.getElementById('topbar-alarm-text');
const sidebarToggleBtn = document.getElementById('sidebar-toggle-btn');
const sidebar = document.getElementById('sidebar');

if (sidebarToggleBtn && sidebar) {
    sidebarToggleBtn.addEventListener('click', () => {
        sidebar.classList.toggle('open');
    });
}

if (topbarAlarmPill && tabSafetyBtn) {
    topbarAlarmPill.addEventListener('click', () => {
        tabSafetyBtn.click();
    });
}

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

// Элементы быстрого планирования перехода между портами
const portDepartSelect = document.getElementById('port-depart-select');
const portArriveSelect = document.getElementById('port-arrive-select');
const btnSwapPorts = document.getElementById('btn-swap-ports');
const portDepthAdvisory = document.getElementById('port-depth-advisory');
const btnBuildVoyage = document.getElementById('btn-build-voyage');
const togglePortsCheckbox = document.getElementById('toggle-ports');

// Элементы онлайн-погоды
const btnFetchLiveWeather = document.getElementById('btn-fetch-live-weather');
const liveWeatherInfo = document.getElementById('live-weather-info');

// Элементы плавающей карточки перехода (Voyage Summary Card)
const voyageCard = document.getElementById('voyage-card');
const voyageCardTitle = document.getElementById('voyage-card-title');
const voyageStatusBadge = document.getElementById('voyage-status-badge');
const voyageCardClose = document.getElementById('voyage-card-close');
const voyageDistVal = document.getElementById('voyage-dist-val');
const voyageEtaVal = document.getElementById('voyage-eta-val');
const voyageDepthVal = document.getElementById('voyage-depth-val');
const voyageUkcVal = document.getElementById('voyage-ukc-val');
const voyageDraftVal = document.getElementById('voyage-draft-val');
const voyageFuelVal = document.getElementById('voyage-fuel-val');

const voyageBtnProfile = document.getElementById('voyage-btn-profile');
const voyageBtnWaypoints = document.getElementById('voyage-btn-waypoints');
const voyageBtnPassagePlan = document.getElementById('voyage-btn-passage-plan');
const voyageBtnSafety = document.getElementById('voyage-btn-safety');
const voyageBtnSim = document.getElementById('voyage-btn-sim');
const voyageBtnGpx = document.getElementById('voyage-btn-gpx');
const voyageBtnRtz = document.getElementById('voyage-btn-rtz');

// Элементы съемки эхолотом (Sonar Survey)
const sonarSurveyDetails = document.getElementById('sonar-survey-details');
const sonarFileInput = document.getElementById('sonar-file-input');
const btnTriggerSonarUpload = document.getElementById('btn-trigger-sonar-upload');
const btnDemoSonarData = document.getElementById('btn-demo-sonar-data');
const sonarSurveyStats = document.getElementById('sonar-survey-stats');
const toggleCustomBathymetryCheckbox = document.getElementById('toggle-custom-bathymetry');
const toggleCustomSoundingsCheckbox = document.getElementById('toggle-custom-soundings');

// Элементы модального окна официального плана перехода (IMO Passage Plan)
const exportPassagePlanBtn = document.getElementById('export-passage-plan-btn');
const passagePlanModal = document.getElementById('passage-plan-modal');
const passagePlanContent = document.getElementById('passage-plan-content');
const btnPrintPassagePlan = document.getElementById('btn-print-passage-plan');
const btnClosePassagePlan = document.getElementById('btn-close-passage-plan');

let blackSeaPorts = [];
let blackSeaAnchorages = [];
let isAudioMuted = false;
let audioAlarmCtx = null;
let lastAudioBeepTime = 0;

// -----------------------------------------------------------------------------
// 3. Пресеты судов и состояние навигации
const VESSEL_PROFILES = {
    yacht: { draft: 1.2, speed: 10, ukc: 0.5, radius: 50, cb: 0.50, fuel: 18.0 },
    ferry: { draft: 4.5, speed: 18, ukc: 1.0, radius: 250, cb: 0.65, fuel: 420.0 },
    cargo: { draft: 10.0, speed: 12, ukc: 2.0, radius: 500, cb: 0.82, fuel: 1200.0 }
};

let draft = parseFloat(draftSlider.value);
let speedKnots = parseFloat(speedSlider.value);
let baseUkc = parseFloat(ukcSlider.value);
let turningRadius = radiusSlider ? parseFloat(radiusSlider.value) : 50.0;
let tideOffsetM = tideSlider ? parseFloat(tideSlider.value) : 0.0;
let waveHeightM = waveSlider ? parseFloat(waveSlider.value) : 0.0;
let fuelRateLh = fuelSlider ? parseFloat(fuelSlider.value) : 18.0;
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
const CONTOUR_LEVELS = [1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 1500.0, 2000.0];

function getClosestSafetyContourLevel(depthM) {
    for (let lvl of CONTOUR_LEVELS) {
        if (lvl >= depthM) return lvl;
    }
    return 2000.0;
}

function updateSafetyContour(requiredDepth) {
    const safeLvl = getClosestSafetyContourLevel(requiredDepth);
    if (map && map.getLayer && map.getLayer('safety-contour-line')) {
        map.setFilter('safety-contour-line', ['==', ['get', 'depth'], safeLvl]);
    }
}

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

    updateSafetyContour(requiredChartDepth);
    if (typeof checkPortDepthsAgainstDraft === 'function') {
        checkPortDepthsAgainstDraft();
    }
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
            if (profile.fuel && fuelSlider) {
                fuelSlider.value = profile.fuel;
                fuelRateLh = profile.fuel;
                if (fuelVal) fuelVal.innerText = `${Math.round(fuelRateLh)} л/ч`;
            }
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
            updateVoyageFuelDisplay();
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
        updateVoyageFuelDisplay();
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

    if (fuelSlider) {
        fuelSlider.addEventListener('input', (e) => {
            fuelRateLh = parseFloat(e.target.value);
            if (fuelVal) fuelVal.innerText = `${Math.round(fuelRateLh)} л/ч`;
            markCustomProfile();
            updateVoyageFuelDisplay();
        });
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

function updateVoyageFuelDisplay() {
    if (!voyageFuelVal) return;
    if (!currentRouteData || !currentRouteData.properties) {
        voyageFuelVal.innerText = '—';
        return;
    }
    const p = currentRouteData.properties;
    const etaHours = (p.eta_hours !== null && p.eta_hours !== undefined) ? p.eta_hours : 0;
    if (etaHours <= 0) {
        voyageFuelVal.innerText = '—';
        return;
    }
    const nominalFuel = etaHours * fuelRateLh;
    const reserveFuel = nominalFuel * 1.20;
    if (nominalFuel >= 1000) {
        voyageFuelVal.innerText = `${(nominalFuel / 1000).toFixed(2)} т (+20%: ${(reserveFuel / 1000).toFixed(2)} т)`;
    } else {
        voyageFuelVal.innerText = `${Math.round(nominalFuel)} л (+20%: ${Math.round(reserveFuel)} л)`;
    }
}

// -----------------------------------------------------------------------------
// 7. Слои навигационной карты (Батиметрия, Фарватеры, Коридор XTD, Дуги циркуляции)
// -----------------------------------------------------------------------------
map.on('load', () => {
    // Гарантируем масштабирование холста и центрирование на акватории плавания
    setTimeout(() => {
        map.resize();
        map.fitBounds([
            [32.8, 44.1],
            [36.6, 45.6]
        ], { padding: 40, duration: 0 });
    }, 50);

    fetch('/api/health')
        .then(res => res.json())
        .then(h => {
            if (map.getLayer('depth-hillshade-layer')) {
                map.setLayoutProperty('depth-hillshade-layer', 'visibility', h.is_synthetic ? 'none' : 'visible');
            }
        })
        .catch(() => {});

    // 0. Сектор предупреждения посадки на мель (Look-Ahead Anti-Grounding Guard Zone)
    map.addSource('lookahead-guard', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'lookahead-guard-fill',
        type: 'fill',
        source: 'lookahead-guard',
        paint: {
            'fill-color': ['coalesce', ['get', 'color'], 'rgba(6, 182, 212, 0.15)'],
            'fill-opacity': 0.85
        }
    });

    map.addLayer({
        id: 'lookahead-guard-line',
        type: 'line',
        source: 'lookahead-guard',
        paint: {
            'line-color': ['coalesce', ['get', 'lineColor'], '#06b6d4'],
            'line-width': 1.8,
            'line-dasharray': [3, 2],
            'line-opacity': 0.95
        }
    });

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
            'line-color': [
                'case',
                ['==', ['get', 'maneuver_feasible'], false], '#ef4444',
                '#f59e0b'
            ],
            'line-width': 3.5,
            'line-opacity': 0.95
        }
    });

    map.addLayer({
        id: 'wop-markers-layer',
        type: 'circle',
        source: 'turn-arcs',
        filter: ['==', ['get', 'role'], 'wop_marker'],
        paint: {
            'circle-color': [
                'case',
                ['==', ['get', 'maneuver_feasible'], false], '#ef4444',
                '#fbbf24'
            ],
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

    // 4.1 Маркеры отмелей внутри полосы безопасности XTD (ALM-04)
    map.addSource('xtd-hazards', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'xtd-hazards-layer',
        type: 'circle',
        source: 'xtd-hazards',
        paint: {
            'circle-color': '#f97316',
            'circle-radius': 6.5,
            'circle-stroke-width': 2,
            'circle-stroke-color': '#ffffff'
        }
    });

    // 4.2 Линии привязки точек отправления/прибытия к безопасной воде (Snap Offsets)
    map.addSource('snap-offsets', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'snap-offsets-line',
        type: 'line',
        source: 'snap-offsets',
        paint: {
            'line-color': '#f59e0b',
            'line-width': 2.5,
            'line-dasharray': [3, 2],
            'line-opacity': 0.95
        }
    });

    // 5. Профессиональная морская батиметрия и картография (IHO S-52 / S-57 / EMODnet)
    const exactHeatmapCoords = [
        [33.122917, 47.300000], // Top-Left: Северо-Западная точка морского сектора
        [41.000046, 47.300000], // Top-Right: Северо-Восточная точка (Тамань / Азов)
        [41.000046, 43.122706], // Bottom-Right: Юго-Восточная точка
        [33.122917, 43.122706]  // Bottom-Left: Юго-Западная точка
    ];

    // 5.1. 3D Теневой рельеф дна (Bathymetric Shaded Relief / Hillshade)
    map.addSource('depth-hillshade', {
        type: 'image',
        url: '/bathymetry_hillshade.png',
        coordinates: exactHeatmapCoords
    });

    map.addLayer({
        id: 'depth-hillshade-layer',
        type: 'raster',
        source: 'depth-hillshade',
        paint: { 'raster-opacity': 0.70 }
    }, 'route-corridor-fill');

    // 5.2. Векторные изобаты глубин (Isobaths 1 - 2000м)
    map.addSource('bathymetry-contours', {
        type: 'geojson',
        data: '/contours.geojson'
    });

    map.addLayer({
        id: 'contours-line-layer',
        type: 'line',
        source: 'bathymetry-contours',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
            'line-color': [
                'case',
                ['get', 'is_index'],
                MAP_THEMES[currentChartTheme].contourIndexColor,
                MAP_THEMES[currentChartTheme].contourColor
            ],
            'line-width': [
                'interpolate', ['linear'], ['zoom'],
                7, ['case', ['get', 'is_index'], 0.8, 0.0],
                10, ['case', ['get', 'is_index'], 1.1, 0.55],
                13, ['case', ['get', 'is_index'], 1.5, 0.8]
            ],
            'line-opacity': [
                'interpolate', ['linear'], ['zoom'],
                7, ['case', ['get', 'is_index'], 0.35, 0.0],
                10, ['case', ['get', 'is_index'], 0.50, 0.22],
                13, ['case', ['get', 'is_index'], 0.65, 0.35]
            ]
        }
    }, 'route-corridor-fill');

    map.addLayer({
        id: 'contours-label-layer',
        type: 'symbol',
        source: 'bathymetry-contours',
        filter: ['==', ['get', 'is_index'], true],
        minzoom: 8.5,
        layout: {
            'symbol-placement': 'line',
            'text-field': ['get', 'label'],
            'text-size': 9.0,
            'text-font': ['literal', ['Open Sans Regular', 'Arial Unicode MS Regular']],
            'text-letter-spacing': 0.05,
            'text-max-angle': 30,
            'symbol-spacing': 550
        },
        paint: {
            'text-color': MAP_THEMES[currentChartTheme].labelColor,
            'text-halo-color': MAP_THEMES[currentChartTheme].contourHalo,
            'text-halo-width': 1.2
        }
    }, 'route-corridor-fill');

    // 5.3. Динамическая изолиния безопасности ЭКНИС (Safety Contour)
    map.addLayer({
        id: 'safety-contour-line',
        type: 'line',
        source: 'bathymetry-contours',
        filter: ['==', ['get', 'depth'], 2.0],
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
            'line-color': '#f43f5e',
            'line-width': 2.0,
            'line-dasharray': [5, 2.5],
            'line-opacity': 0.85
        }
    }, 'route-corridor-fill');

    // 5.4. Морские отметки глубин (Spot Soundings)
    map.addSource('bathymetry-soundings', {
        type: 'geojson',
        data: '/soundings.geojson'
    });

    map.addLayer({
        id: 'soundings-layer',
        type: 'symbol',
        source: 'bathymetry-soundings',
        minzoom: 9.5,
        layout: {
            'text-field': ['get', 'label'],
            'text-size': ['case', ['get', 'is_peak'], 10, 8.5],
            'text-font': ['literal', ['Open Sans Regular', 'Arial Unicode MS Regular']],
            'text-allow-overlap': false,
            'text-ignore-placement': false
        },
        paint: {
            'text-color': [
                'case',
                ['get', 'is_peak'],
                '#f59e0b',
                MAP_THEMES[currentChartTheme].soundingColor
            ],
            'text-halo-color': MAP_THEMES[currentChartTheme].contourHalo,
            'text-halo-width': 1.2
        }
    }, 'route-corridor-fill');

    // 5.5. Навигационные знаки и обстановка IHO S-52 / INT 1 (OpenSeaMap AtoN)
    map.addSource('openseamap-seamarks', {
        type: 'raster',
        tiles: ['https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png'],
        tileSize: 256,
        minzoom: 9,
        maxzoom: 18
    });

    map.addLayer({
        id: 'seamarks-layer',
        type: 'raster',
        source: 'openseamap-seamarks',
        paint: { 'raster-opacity': 0.75 }
    }, 'route-corridor-fill');

    // Первичная синхронизация изолинии безопасности с текущей осадкой
    if (typeof updateSafetyContour === 'function') {
        const initSafetyDepth = Math.max(0.5, draft + dynamicSquat + (0.35 * waveHeightM) + baseUkc - tideOffsetM);
        updateSafetyContour(initSafetyDepth);
    }

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
                paint: { 'fill-color': '#06b6d4', 'fill-opacity': 0.05 }
            }, 'route-corridor-fill');

            map.addLayer({
                id: 'fairways-line',
                type: 'line',
                source: 'fairways-data',
                filter: ['!=', ['get', 'category'], 'restricted'],
                paint: {
                    'line-color': '#06b6d4',
                    'line-width': 1.0,
                    'line-dasharray': [4, 3],
                    'line-opacity': 0.45
                }
            }, 'route-corridor-fill');

            map.addLayer({
                id: 'restricted-fill',
                type: 'fill',
                source: 'fairways-data',
                filter: ['==', ['get', 'category'], 'restricted'],
                paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.10 }
            }, 'route-corridor-fill');

            map.addLayer({
                id: 'restricted-line',
                type: 'line',
                source: 'fairways-data',
                filter: ['==', ['get', 'category'], 'restricted'],
                paint: {
                    'line-color': '#ef4444',
                    'line-width': 1.5,
                    'line-dasharray': [6, 3],
                    'line-opacity': 0.85
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

    // 6.1. Управление темами и слоями морской картографии ЭКНИС
    function applyChartTheme(themeName) {
        if (!MAP_THEMES[themeName]) return;
        currentChartTheme = themeName;
        const cfg = MAP_THEMES[themeName];

        const baseLayerMap = {
            'ecdis-night': 'layer-base-night',
            'ecdis-day': 'layer-base-day',
            'ocean': 'layer-base-ocean',
            'satellite': 'layer-base-satellite'
        };
        for (const [key, layerId] of Object.entries(baseLayerMap)) {
            if (map.getLayer(layerId)) {
                map.setLayoutProperty(layerId, 'visibility', key === themeName ? 'visible' : 'none');
            }
        }

        if (map.getLayer('contours-line-layer')) {
            map.setPaintProperty('contours-line-layer', 'line-color', [
                'case',
                ['get', 'is_index'],
                cfg.contourIndexColor,
                cfg.contourColor
            ]);
        }
        if (map.getLayer('contours-label-layer')) {
            map.setPaintProperty('contours-label-layer', 'text-color', cfg.labelColor);
            map.setPaintProperty('contours-label-layer', 'text-halo-color', cfg.contourHalo);
        }
        if (map.getLayer('soundings-layer')) {
            map.setPaintProperty('soundings-layer', 'text-color', [
                'case',
                ['get', 'is_peak'],
                '#f59e0b',
                cfg.soundingColor
            ]);
            map.setPaintProperty('soundings-layer', 'text-halo-color', cfg.contourHalo);
        }
    }

    if (chartThemeSelect) {
        chartThemeSelect.addEventListener('change', (e) => {
            applyChartTheme(e.target.value);
        });
    }

    if (toggleHillshadeCheckbox) {
        toggleHillshadeCheckbox.addEventListener('change', (e) => {
            if (map.getLayer('depth-hillshade-layer')) {
                map.setLayoutProperty('depth-hillshade-layer', 'visibility', e.target.checked ? 'visible' : 'none');
            }
        });
    }

    if (toggleContoursCheckbox) {
        toggleContoursCheckbox.addEventListener('change', (e) => {
            const vis = e.target.checked ? 'visible' : 'none';
            if (map.getLayer('contours-line-layer')) map.setLayoutProperty('contours-line-layer', 'visibility', vis);
            if (map.getLayer('contours-label-layer')) map.setLayoutProperty('contours-label-layer', 'visibility', vis);
        });
    }

    if (toggleSafetyContourCheckbox) {
        toggleSafetyContourCheckbox.addEventListener('change', (e) => {
            if (map.getLayer('safety-contour-line')) {
                map.setLayoutProperty('safety-contour-line', 'visibility', e.target.checked ? 'visible' : 'none');
            }
        });
    }

    if (toggleSoundingsCheckbox) {
        toggleSoundingsCheckbox.addEventListener('change', (e) => {
            if (map.getLayer('soundings-layer')) {
                map.setLayoutProperty('soundings-layer', 'visibility', e.target.checked ? 'visible' : 'none');
            }
        });
    }

    if (toggleSeamarksCheckbox) {
        toggleSeamarksCheckbox.addEventListener('change', (e) => {
            if (map.getLayer('seamarks-layer')) {
                map.setLayoutProperty('seamarks-layer', 'visibility', e.target.checked ? 'visible' : 'none');
            }
        });
    }

    // 6.2. Порты, марины и рейдовые стоянки Черного моря
    fetch('/api/ports')
        .then(res => res.json())
        .then(ports => {
            blackSeaPorts = ports || [];
            initPortsUI(blackSeaPorts);

            const portFeatures = blackSeaPorts.map(p => ({
                type: 'Feature',
                properties: {
                    id: p.id,
                    name: p.name,
                    short_name: p.short_name || p.name,
                    depth_m: p.depth_m,
                    vhf_channel: p.vhf_channel,
                    type: p.type,
                    description: p.description
                },
                geometry: {
                    type: 'Point',
                    coordinates: p.port_coords
                }
            }));

            map.addSource('ports-data', {
                type: 'geojson',
                data: {
                    type: 'FeatureCollection',
                    features: portFeatures
                }
            });

            map.addLayer({
                id: 'ports-circle-layer',
                type: 'circle',
                source: 'ports-data',
                paint: {
                    'circle-radius': [
                        'interpolate', ['linear'], ['zoom'],
                        7, 4.5,
                        10, 7.0,
                        13, 9.5
                    ],
                    'circle-color': [
                        'match', ['get', 'type'],
                        'marina', '#38bdf8',
                        'commercial', '#f59e0b',
                        'passenger', '#ec4899',
                        '#06b6d4'
                    ],
                    'circle-stroke-width': 2,
                    'circle-stroke-color': '#ffffff'
                }
            });

            map.addLayer({
                id: 'ports-label-layer',
                type: 'symbol',
                source: 'ports-data',
                minzoom: 8.0,
                layout: {
                    'text-field': ['get', 'short_name'],
                    'text-font': ['literal', ['Open Sans Regular', 'Arial Unicode MS Regular']],
                    'text-size': 11,
                    'text-offset': [0, 1.2],
                    'text-anchor': 'top',
                    'text-allow-overlap': false
                },
                paint: {
                    'text-color': '#ffffff',
                    'text-halo-color': '#0f172a',
                    'text-halo-width': 2
                }
            });

            // Интерактивный клик на порт: всплывающая карточка
            map.on('mouseenter', 'ports-circle-layer', () => {
                if (!isEblVrmActive && !isAddingViaMode) map.getCanvas().style.cursor = 'pointer';
            });
            map.on('mouseleave', 'ports-circle-layer', () => {
                if (!isEblVrmActive && !isAddingViaMode) map.getCanvas().style.cursor = '';
            });

            let activePortPopup = null;
            map.on('click', 'ports-circle-layer', (e) => {
                if (isEblVrmActive || isAddingViaMode) return;
                const props = e.features[0].properties;
                const coords = e.features[0].geometry.coordinates.slice();

                if (activePortPopup) activePortPopup.remove();

                activePortPopup = new maplibregl.Popup({ offset: 12, closeButton: true })
                    .setLngLat(coords)
                    .setHTML(`
                        <div class="port-popup">
                            <div class="port-popup-title">${props.name}</div>
                            <div class="port-popup-info">Глубина: ${props.depth_m} м &nbsp;|&nbsp; ${props.vhf_channel}</div>
                            <div class="port-popup-desc">${props.description}</div>
                            <div class="port-popup-buttons">
                                <button class="port-popup-btn btn-depart" onclick="window.selectPortAsDepart('${props.id}')">Отход отсюда</button>
                                <button class="port-popup-btn btn-arrive" onclick="window.selectPortAsArrive('${props.id}')">Приход сюда</button>
                            </div>
                        </div>
                    `)
                    .addTo(map);
            });
        })
        .catch(err => console.warn('Не удалось загрузить базу портов:', err));

    if (togglePortsCheckbox) {
        togglePortsCheckbox.addEventListener('change', (e) => {
            const vis = e.target.checked ? 'visible' : 'none';
            if (map.getLayer('ports-circle-layer')) map.setLayoutProperty('ports-circle-layer', 'visibility', vis);
            if (map.getLayer('ports-label-layer')) map.setLayoutProperty('ports-label-layer', 'visibility', vis);
        });
    }

    // 6.3. Рейдовые якорные стоянки Черного моря (Roadstead Anchorages)
    fetch('/api/anchorages')
        .then(res => res.json())
        .then(anchorages => {
            blackSeaAnchorages = anchorages || [];
            const anchorageFeatures = blackSeaAnchorages.map(a => ({
                type: 'Feature',
                properties: {
                    id: a.id,
                    name: a.name,
                    depth_range: `${a.depth_min_m}-${a.depth_max_m} м`,
                    max_draft: `${a.max_draft_m} м`,
                    holding_ground: a.bottom_nature,
                    shelter_from: a.sheltered_from_winds ? a.sheltered_from_winds.join(', ') : 'Открытая',
                    vhf_channel: a.vhf_channel,
                    notes: a.notes
                },
                geometry: {
                    type: 'Point',
                    coordinates: a.coords
                }
            }));

            map.addSource('anchorages-data', {
                type: 'geojson',
                data: {
                    type: 'FeatureCollection',
                    features: anchorageFeatures
                }
            });

            map.addLayer({
                id: 'anchorages-circle-layer',
                type: 'circle',
                source: 'anchorages-data',
                paint: {
                    'circle-radius': [
                        'interpolate', ['linear'], ['zoom'],
                        7, 4.0,
                        10, 6.5,
                        13, 8.5
                    ],
                    'circle-color': '#10b981',
                    'circle-stroke-width': 2,
                    'circle-stroke-color': '#064e3b'
                }
            }, 'route-corridor-fill');

            map.addLayer({
                id: 'anchorages-label-layer',
                type: 'symbol',
                source: 'anchorages-data',
                minzoom: 8.5,
                layout: {
                    'text-field': ['concat', '[ЯКОРЬ] ', ['get', 'name']],
                    'text-font': ['literal', ['Open Sans Regular', 'Arial Unicode MS Regular']],
                    'text-size': 10,
                    'text-offset': [0, 1.2],
                    'text-anchor': 'top',
                    'text-allow-overlap': false
                },
                paint: {
                    'text-color': '#6ee7b7',
                    'text-halo-color': '#064e3b',
                    'text-halo-width': 1.5
                }
            }, 'route-corridor-fill');

            let activeAnchoragePopup = null;
            map.on('mouseenter', 'anchorages-circle-layer', () => {
                if (!isEblVrmActive && !isAddingViaMode) map.getCanvas().style.cursor = 'pointer';
            });
            map.on('mouseleave', 'anchorages-circle-layer', () => {
                if (!isEblVrmActive && !isAddingViaMode) map.getCanvas().style.cursor = '';
            });

            map.on('click', 'anchorages-circle-layer', (e) => {
                if (isEblVrmActive || isAddingViaMode) return;
                const props = e.features[0].properties;
                const coords = e.features[0].geometry.coordinates.slice();

                if (activeAnchoragePopup) activeAnchoragePopup.remove();

                activeAnchoragePopup = new maplibregl.Popup({ offset: 12, closeButton: true })
                    .setLngLat(coords)
                    .setHTML(`
                        <div class="port-popup">
                            <div class="port-popup-title" style="color: #34d399;">Якорная стоянка: ${props.name}</div>
                            <div class="port-popup-info">Глубины: ${props.depth_range} &nbsp;|&nbsp; Макс. осадка: ${props.max_draft}</div>
                            <div class="port-popup-desc">Грунт: ${props.holding_ground} | Укрытие: ${props.shelter_from} | ${props.vhf_channel}</div>
                            <div style="font-size: 11px; color: #94a3b8; margin-top: 4px;">${props.notes}</div>
                            <div class="port-popup-buttons">
                                <button class="port-popup-btn btn-arrive" onclick="window.addAnchorageAsWaypoint(${coords[0]}, ${coords[1]}, '${props.name}')">Встать на якорь (РТ)</button>
                            </div>
                        </div>
                    `)
                    .addTo(map);
            });
        })
        .catch(err => console.warn('Не удалось загрузить базу якорных стоянок:', err));

    if (toggleAnchoragesCheckbox) {
        toggleAnchoragesCheckbox.addEventListener('change', (e) => {
            const vis = e.target.checked ? 'visible' : 'none';
            if (map.getLayer('anchorages-circle-layer')) map.setLayoutProperty('anchorages-circle-layer', 'visibility', vis);
            if (map.getLayer('anchorages-label-layer')) map.setLayoutProperty('anchorages-label-layer', 'visibility', vis);
        });
    }

    // 6.4. Пользовательская батиметрия съемки эхолота (Sonar Survey)
    map.addSource('custom-bathymetry', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'custom-bathymetry-line',
        type: 'line',
        source: 'custom-bathymetry',
        filter: ['==', ['get', 'role'], 'custom_isobath'],
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
            'line-color': '#06b6d4',
            'line-width': 2.2,
            'line-dasharray': [3, 1.5],
            'line-opacity': 0.95
        }
    }, 'route-corridor-fill');

    map.addLayer({
        id: 'custom-bathymetry-label',
        type: 'symbol',
        source: 'custom-bathymetry',
        filter: ['==', ['get', 'role'], 'custom_isobath'],
        layout: {
            'symbol-placement': 'line',
            'text-field': ['get', 'label'],
            'text-size': 9.5,
            'text-font': ['literal', ['Open Sans Regular', 'Arial Unicode MS Regular']],
            'symbol-spacing': 400
        },
        paint: {
            'text-color': '#22d3ee',
            'text-halo-color': '#083344',
            'text-halo-width': 1.5
        }
    }, 'route-corridor-fill');

    map.addSource('custom-soundings', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'custom-soundings-layer',
        type: 'symbol',
        source: 'custom-soundings',
        minzoom: 10.5,
        layout: {
            'text-field': ['get', 'label'],
            'text-size': 9.0,
            'text-font': ['literal', ['Open Sans Regular', 'Arial Unicode MS Regular']],
            'text-allow-overlap': false
        },
        paint: {
            'text-color': '#a5f3fc',
            'text-halo-color': '#083344',
            'text-halo-width': 1.2
        }
    }, 'route-corridor-fill');

    if (toggleCustomBathymetryCheckbox) {
        toggleCustomBathymetryCheckbox.addEventListener('change', (e) => {
            const vis = e.target.checked ? 'visible' : 'none';
            if (map.getLayer('custom-bathymetry-line')) map.setLayoutProperty('custom-bathymetry-line', 'visibility', vis);
            if (map.getLayer('custom-bathymetry-label')) map.setLayoutProperty('custom-bathymetry-label', 'visibility', vis);
        });
    }

    if (toggleCustomSoundingsCheckbox) {
        toggleCustomSoundingsCheckbox.addEventListener('change', (e) => {
            const vis = e.target.checked ? 'visible' : 'none';
            if (map.getLayer('custom-soundings-layer')) map.setLayoutProperty('custom-soundings-layer', 'visibility', vis);
        });
    }

    const recenterBtn = document.getElementById('tool-recenter-btn');
    if (recenterBtn) {
        recenterBtn.addEventListener('click', () => {
            if (activeWaterbodyBounds && activeWaterbodyBounds.length === 4) {
                map.fitBounds([
                    [activeWaterbodyBounds[0], activeWaterbodyBounds[1]],
                    [activeWaterbodyBounds[2], activeWaterbodyBounds[3]]
                ], { padding: 40, duration: 600 });
            } else {
                map.fitBounds([
                    [32.8, 44.1],
                    [36.6, 45.6]
                ], { padding: 40, duration: 600 });
            }
        });
    }

    // 6.5. Слой живой телеметрии эхолота и трека судна (Quickdraw / Chartplotter)
    map.addSource('live-telemetry-track', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'live-telemetry-line',
        type: 'line',
        source: 'live-telemetry-track',
        filter: ['==', '$type', 'LineString'],
        paint: {
            'line-color': '#38bdf8',
            'line-width': 2.5,
            'line-dasharray': [2, 1]
        }
    }, 'route-corridor-fill');

    map.addLayer({
        id: 'live-telemetry-points',
        type: 'circle',
        source: 'live-telemetry-track',
        filter: ['==', ['get', 'role'], 'live_sounding_point'],
        paint: {
            'circle-radius': 4.5,
            'circle-color': [
                'interpolate', ['linear'], ['get', 'depth_m'],
                2.0, '#ef4444',
                5.0, '#f59e0b',
                10.0, '#10b981',
                20.0, '#06b6d4'
            ],
            'circle-stroke-width': 1.5,
            'circle-stroke-color': '#ffffff'
        }
    }, 'route-corridor-fill');

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
                        } else if (data.is_nodata) {
                            hudCursorDepth.innerText = 'Нет данных';
                            hudCursorDepth.style.color = 'var(--text-muted)';
                        } else if (data.is_land) {
                            hudCursorDepth.innerText = 'Суша (0.0 м)';
                            hudCursorDepth.style.color = 'var(--status-danger)';
                        } else {
                            hudCursorDepth.innerText = formatDepthVal(data.depth);
                            const safeLimit = (currentRouteData && currentRouteData.properties && currentRouteData.properties.min_chart_depth_m)
                                ? currentRouteData.properties.min_chart_depth_m
                                : (draft + dynamicSquat + baseUkc - tideOffsetM);
                            const isSafe = data.depth >= safeLimit;
                            hudCursorDepth.style.color = isSafe ? 'var(--status-success)' : 'var(--status-danger)';
                        }
                    })
                    .catch(() => {});
            }, 80);
        }
    });

    updateVesselPhysics();
    loadWaterbodiesList();
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
    if (map.getSource('xtd-hazards')) map.getSource('xtd-hazards').setData({ type: 'FeatureCollection', features: [] });
    if (topbarAlarmPill) topbarAlarmPill.style.display = 'none';
    clearRouteProfileHighlight();

    renderWaypointsListUI();
    renderWaypointsTable([]);
    renderSafetyDashboard(null, null, null);

    if (bottomDrawer) bottomDrawer.style.display = 'none';
    if (exportActions) exportActions.style.display = 'none';
    if (voyageCard) voyageCard.style.display = 'none';
    statusMsg.innerText = '';
    stopSimulator();
}

// -----------------------------------------------------------------------------
// 8.1. Быстрое планирование перехода между портами и управление карточкой рейса
// -----------------------------------------------------------------------------
function initPortsUI(ports) {
    if (!portDepartSelect || !portArriveSelect) return;

    if (!ports || ports.length === 0) {
        portDepartSelect.innerHTML = '<option value="">-- Кликните на карте (Точка А) --</option>';
        portArriveSelect.innerHTML = '<option value="">-- Кликните на карте (Точка Б) --</option>';
        portDepartSelect.value = '';
        portArriveSelect.value = '';
        if (portDepthAdvisory) portDepthAdvisory.style.display = 'none';
        return;
    }

    portDepartSelect.innerHTML = '<option value="">-- Выберите порт отхода --</option>';
    portArriveSelect.innerHTML = '<option value="">-- Выберите порт прибытия --</option>';

    ports.forEach(p => {
        const optA = document.createElement('option');
        optA.value = p.id;
        optA.innerText = p.name;
        portDepartSelect.appendChild(optA);

        const optB = document.createElement('option');
        optB.value = p.id;
        optB.innerText = p.name;
        portArriveSelect.appendChild(optB);
    });

    if (ports.some(p => p.id === 'sevastopol_kamyshovaya')) {
        portDepartSelect.value = 'sevastopol_kamyshovaya';
    } else if (ports.length >= 1) {
        portDepartSelect.value = ports[0].id;
    }

    if (ports.some(p => p.id === 'yalta_port')) {
        portArriveSelect.value = 'yalta_port';
    } else if (ports.length >= 2) {
        portArriveSelect.value = ports[1].id;
    }

    portDepartSelect.addEventListener('change', () => {
        checkPortDepthsAgainstDraft();
    });

    portArriveSelect.addEventListener('change', () => {
        checkPortDepthsAgainstDraft();
    });

    checkPortDepthsAgainstDraft();
}

function checkPortDepthsAgainstDraft() {
    if (!portDepthAdvisory) return;

    const waveAllowance = 0.35 * waveHeightM;
    const dynamicDraft = draft + dynamicSquat + waveAllowance;
    const requiredDepth = Math.max(0.5, dynamicDraft + baseUkc - tideOffsetM);

    const depId = portDepartSelect ? portDepartSelect.value : '';
    const arrId = portArriveSelect ? portArriveSelect.value : '';

    const portA = blackSeaPorts.find(p => p.id === depId);
    const portB = blackSeaPorts.find(p => p.id === arrId);

    const warnings = [];

    if (portA && typeof portA.depth_m === 'number') {
        if (portA.depth_m < requiredDepth) {
            const deficit = (requiredDepth - portA.depth_m).toFixed(1);
            warnings.push(`Порт отхода <b>${portA.short_name || portA.name}</b>: глубина акватории <b>${portA.depth_m.toFixed(1)} м</b> &lt; требуемой <b>${requiredDepth.toFixed(1)} м</b> (дефицит: <b>${deficit} м</b>)`);
        }
    }

    if (portB && typeof portB.depth_m === 'number') {
        if (portB.depth_m < requiredDepth) {
            const deficit = (requiredDepth - portB.depth_m).toFixed(1);
            warnings.push(`Порт прибытия <b>${portB.short_name || portB.name}</b>: глубина акватории <b>${portB.depth_m.toFixed(1)} м</b> &lt; требуемой <b>${requiredDepth.toFixed(1)} м</b> (дефицит: <b>${deficit} м</b>)`);
        }
    }

    if (warnings.length > 0) {
        portDepthAdvisory.style.display = 'block';
        portDepthAdvisory.innerHTML = `
            <div class="advisory-title" style="font-weight: 600; margin-bottom: 3px; color: #f87171;">ВНИМАНИЕ: ОСАДКА СУДНА ПРЕВЫШАЕТ ГЛУБИНУ ПОРТА!</div>
            <div class="advisory-body">${warnings.join('<br>')}</div>
            <div class="advisory-hint" style="font-size: 10px; margin-top: 4px; opacity: 0.85;">Опасность посадки на мель при швартовке. Снизьте осадку/скорость или дождитесь прилива.</div>
        `;
    } else {
        portDepthAdvisory.style.display = 'none';
        portDepthAdvisory.innerHTML = '';
    }
}

function swapPortsAndReverseRoute() {
    if (!portDepartSelect || !portArriveSelect) return;

    // 1. Поменять местами выбранные порты в выпадающих списках
    const oldDep = portDepartSelect.value;
    const oldArr = portArriveSelect.value;
    portDepartSelect.value = oldArr;
    portArriveSelect.value = oldDep;

    checkPortDepthsAgainstDraft();

    // 2. Если на карте уже проложен маршрут из путевых точек — инвертировать их
    if (plannedWaypoints.length >= 2) {
        plannedWaypoints.forEach(w => w.marker && w.marker.remove());
        plannedWaypoints.reverse();
        plannedWaypoints.forEach((wp, idx) => {
            if (idx === 0) {
                wp.role = 'start';
            } else if (idx === plannedWaypoints.length - 1) {
                wp.role = 'goal';
            } else {
                wp.role = 'via';
            }
            wp.marker = createWaypointMarker(wp);
        });
        renderWaypointsListUI();
        calculateRoute();
    } else if (portDepartSelect.value && portArriveSelect.value) {
        // Если точек на карте еще нет, но порты выбраны — построить обратный переход
        executePortToPortVoyage();
    }
}

if (btnSwapPorts) {
    btnSwapPorts.addEventListener('click', swapPortsAndReverseRoute);
}

async function fetchLiveMarineWeather() {
    if (!btnFetchLiveWeather) return;

    let targetLon = 34.5;
    let targetLat = 44.5;

    if (plannedWaypoints.length >= 2) {
        const start = plannedWaypoints[0].coords;
        const goal = plannedWaypoints[plannedWaypoints.length - 1].coords;
        targetLon = (start[0] + goal[0]) / 2.0;
        targetLat = (start[1] + goal[1]) / 2.0;
    } else if (currentRouteData && currentRouteData.geometry && currentRouteData.geometry.coordinates.length > 0) {
        const coords = currentRouteData.geometry.coordinates;
        const mid = coords[Math.floor(coords.length / 2)];
        targetLon = mid[0];
        targetLat = mid[1];
    } else {
        const depId = portDepartSelect ? portDepartSelect.value : '';
        const portA = blackSeaPorts.find(p => p.id === depId);
        if (portA) {
            const c = portA.approach_coords || portA.port_coords;
            targetLon = c[0];
            targetLat = c[1];
        }
    }

    const origBtnText = btnFetchLiveWeather.innerText;
    btnFetchLiveWeather.disabled = true;
    btnFetchLiveWeather.innerText = 'Запрос...';

    const url = `https://marine-api.open-meteo.com/v1/marine?latitude=${targetLat.toFixed(4)}&longitude=${targetLon.toFixed(4)}&current=wave_height,wave_direction,wave_period`;

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const cur = data.current || {};

        const waveHeight = typeof cur.wave_height === 'number' ? cur.wave_height : 0.0;
        const wavePeriod = typeof cur.wave_period === 'number' ? cur.wave_period : null;
        const waveDir = typeof cur.wave_direction === 'number' ? Math.round(cur.wave_direction) : null;

        const clampedHs = Math.min(3.5, Math.max(0.0, waveHeight));
        waveSlider.value = clampedHs.toFixed(1);
        waveHeightM = parseFloat(waveSlider.value);
        if (waveVal) waveVal.innerText = waveHeightM.toFixed(1) + 'м';

        if (liveWeatherInfo) {
            liveWeatherInfo.style.display = 'block';
            const dirStr = waveDir !== null ? `${waveDir}°` : '—';
            const periodStr = wavePeriod !== null ? `${wavePeriod.toFixed(1)} с` : '—';
            liveWeatherInfo.innerHTML = `
                <div><b>Open-Meteo Marine (${targetLat.toFixed(2)}°N, ${targetLon.toFixed(2)}°E):</b></div>
                <div>Высота волны Hs: <b>${waveHeight.toFixed(2)} м</b> | Период: <b>${periodStr}</b> | Ветер/волна: <b>${dirStr}</b></div>
            `;
        }

        updateVesselPhysics();
        checkPortDepthsAgainstDraft();
        triggerRouteRecalculation(true);
    } catch (err) {
        console.warn('Live marine weather error:', err);
        if (liveWeatherInfo) {
            liveWeatherInfo.style.display = 'block';
            liveWeatherInfo.innerHTML = `<span style="color: var(--status-warn);">Сбой получения онлайн-метео: ${err.message || 'сеть недоступна'}</span>`;
        }
    } finally {
        btnFetchLiveWeather.disabled = false;
        btnFetchLiveWeather.innerText = origBtnText;
    }
}

if (btnFetchLiveWeather) {
    btnFetchLiveWeather.addEventListener('click', fetchLiveMarineWeather);
}

window.selectPortAsDepart = function(portId) {
    if (portDepartSelect) portDepartSelect.value = portId;
    checkPortDepthsAgainstDraft();
    const p = blackSeaPorts.find(x => x.id === portId);
    if (!p) return;
    const coords = p.approach_coords || p.port_coords;
    if (plannedWaypoints.length === 0) {
        addWaypoint(coords, 'start');
    } else {
        plannedWaypoints[0].coords = coords;
        if (plannedWaypoints[0].marker) plannedWaypoints[0].marker.setLngLat(coords);
        renderWaypointsListUI();
        if (plannedWaypoints.length >= 2) calculateRoute();
    }
    map.flyTo({ center: coords, zoom: Math.max(map.getZoom(), 10.5), speed: 1.2 });
};

window.selectPortAsArrive = function(portId) {
    if (portArriveSelect) portArriveSelect.value = portId;
    checkPortDepthsAgainstDraft();
    const p = blackSeaPorts.find(x => x.id === portId);
    if (!p) return;
    const coords = p.approach_coords || p.port_coords;
    if (plannedWaypoints.length === 0) {
        statusMsg.innerText = 'Сначала укажите порт или точку отхода (A).';
        statusMsg.style.color = '#38bdf8';
        return;
    }
    if (plannedWaypoints.length === 1) {
        addWaypoint(coords, 'goal');
    } else {
        const lastIdx = plannedWaypoints.length - 1;
        plannedWaypoints[lastIdx].coords = coords;
        if (plannedWaypoints[lastIdx].marker) plannedWaypoints[lastIdx].marker.setLngLat(coords);
        renderWaypointsListUI();
        calculateRoute();
    }
    map.flyTo({ center: coords, zoom: Math.max(map.getZoom(), 10.5), speed: 1.2 });
};

function executePortToPortVoyage() {
    const depId = portDepartSelect ? portDepartSelect.value : '';
    const arrId = portArriveSelect ? portArriveSelect.value : '';
    if (!depId || !arrId) {
        statusMsg.innerText = 'Пожалуйста, выберите порт отхода и порт прибытия.';
        statusMsg.style.color = '#f59e0b';
        return;
    }
    if (depId === arrId) {
        statusMsg.innerText = 'Порт прибытия должен отличаться от порта отхода.';
        statusMsg.style.color = '#f59e0b';
        return;
    }
    const portA = blackSeaPorts.find(p => p.id === depId);
    const portB = blackSeaPorts.find(p => p.id === arrId);
    if (!portA || !portB) return;

    checkPortDepthsAgainstDraft();
    clearRouteData();

    // Прокладываем маршрут между безопасными фарватерными подходами портов
    const startCoords = portA.approach_coords || portA.port_coords;
    const goalCoords = portB.approach_coords || portB.port_coords;

    addWaypoint(startCoords, 'start');
    addWaypoint(goalCoords, 'goal');

    const minLon = Math.min(startCoords[0], goalCoords[0]);
    const maxLon = Math.max(startCoords[0], goalCoords[0]);
    const minLat = Math.min(startCoords[1], goalCoords[1]);
    const maxLat = Math.max(startCoords[1], goalCoords[1]);

    map.fitBounds([
        [minLon, minLat],
        [maxLon, maxLat]
    ], { padding: 80, duration: 1000 });
}

if (btnBuildVoyage) {
    btnBuildVoyage.addEventListener('click', executePortToPortVoyage);
}

// Навешивание действий на интерактивные кнопки Voyage Summary Card
if (voyageCardClose) {
    voyageCardClose.addEventListener('click', () => {
        if (voyageCard) voyageCard.style.display = 'none';
    });
}

if (voyageBtnProfile && tabProfileBtn) {
    voyageBtnProfile.addEventListener('click', () => {
        if (bottomDrawer) bottomDrawer.style.display = 'flex';
        tabProfileBtn.click();
    });
}

if (voyageBtnWaypoints && tabWaypointsBtn) {
    voyageBtnWaypoints.addEventListener('click', () => {
        if (bottomDrawer) bottomDrawer.style.display = 'flex';
        tabWaypointsBtn.click();
    });
}

if (voyageBtnSafety && tabSafetyBtn) {
    voyageBtnSafety.addEventListener('click', () => {
        if (bottomDrawer) bottomDrawer.style.display = 'flex';
        tabSafetyBtn.click();
    });
}

if (voyageBtnSim && tabSimulatorBtn) {
    voyageBtnSim.addEventListener('click', () => {
        if (bottomDrawer) bottomDrawer.style.display = 'flex';
        tabSimulatorBtn.click();
        if (simPlayBtn && !simPlayBtn.disabled) {
            simPlayBtn.click();
        }
    });
}

if (voyageBtnGpx && exportGpxBtn) {
    voyageBtnGpx.addEventListener('click', () => exportGpxBtn.click());
}

if (voyageBtnRtz && exportRtzBtn) {
    voyageBtnRtz.addEventListener('click', () => exportRtzBtn.click());
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
    if (window.innerWidth <= 860 && sidebar && sidebar.classList.contains('open')) {
        sidebar.classList.remove('open');
    }
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

        const heelVal = wp.heel_sinkage_m !== undefined ? wp.heel_sinkage_m : 0.0;
        const heelTxt = (idx === 0 || isLast || heelVal <= 0.001) ? '—' : `+${heelVal.toFixed(2)}м`;
        const isManeuverFeasible = wp.maneuver_feasible !== false;
        const maneuverTxt = (idx === 0 || isLast || Math.abs(turnVal) < 1.0)
            ? '—'
            : (isManeuverFeasible
                ? '<span class="val-maneuver-ok">НОРМА</span>'
                : '<span class="val-maneuver-violation" title="Радиус не вписывается в геометрию плеча">НАРУШЕН</span>');

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
            <td>${heelTxt}</td>
            <td>${maneuverTxt}</td>
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

            // Обновление индикатора опасности / демо-статуса в Topbar и коридоре XTD
            const sc = data.safety_check || {};
            const isHazard = sc.status === 'CRITICAL_HAZARD';
            const isDemo = sc.status === 'DEMO_UNVERIFIED' || (data.properties && data.properties.is_executable_passage_plan === false);
            const isUnsafe = (sc.is_safe === false) || isHazard || isDemo;

            if (topbarAlarmPill) {
                if (isUnsafe) {
                    topbarAlarmPill.style.display = 'flex';
                    if (isDemo) {
                        topbarAlarmPill.style.background = 'rgba(239, 68, 68, 0.25)';
                        topbarAlarmPill.style.borderColor = '#ef4444';
                        if (topbarAlarmText) {
                            topbarAlarmText.innerText = 'ДЕМО-РЕЖИМ (СИНТЕТИКА — НЕ ДЛЯ НАВИГАЦИИ)';
                        }
                    } else {
                        topbarAlarmPill.style.background = 'rgba(239, 68, 68, 0.2)';
                        topbarAlarmPill.style.borderColor = '#ef4444';
                        const critAlm = (sc.alarms || []).find(a => a.severity === 'CRITICAL');
                        if (topbarAlarmText) {
                            topbarAlarmText.innerText = critAlm ? critAlm.code : 'КРИТИЧЕСКИЙ АЛАРМ';
                        }
                    }
                } else if (sc.status === 'WARNING_ADVISORY') {
                    topbarAlarmPill.style.display = 'flex';
                    topbarAlarmPill.style.background = 'rgba(245, 158, 11, 0.2)';
                    topbarAlarmPill.style.borderColor = '#f59e0b';
                    if (topbarAlarmText) {
                        topbarAlarmText.innerText = `ПРЕДУПРЕЖДЕНИЯ (${sc.alarms_count || 1})`;
                    }
                } else {
                    topbarAlarmPill.style.display = 'none';
                }
            }

            // Цветовая индикация коридора XTD (красный при критической опасности/демо, янтарный при предупреждениях, зеленый при норме)
            if (map.getLayer('route-corridor-line')) {
                const corridorColor = isUnsafe ? '#ef4444' : (sc.status === 'WARNING_ADVISORY' ? '#f59e0b' : '#10b981');
                map.setPaintProperty('route-corridor-line', 'line-color', corridorColor);
                map.setPaintProperty('route-corridor-fill', 'fill-color', corridorColor);
            }

            // Отображение отмелей внутри коридора XTD на карте (ALM-04)
            if (map.getSource('xtd-hazards')) {
                const hazards = (data.safety_check && data.safety_check.corridor_hazards) || [];
                const hazardFeatures = hazards.map(h => ({
                    type: 'Feature',
                    properties: { depth: h.depth, offset: h.xtd_offset_m },
                    geometry: { type: 'Point', coordinates: [h.lon, h.lat] }
                }));
                map.getSource('xtd-hazards').setData({ type: 'FeatureCollection', features: hazardFeatures });
            }

            // Отображение линий смещения привязки к безопасной воде (Snap Offsets)
            if (map.getSource('snap-offsets')) {
                const snapFeatures = [];
                (data.waypoints || []).forEach(wp => {
                    if (wp.snap_offset_m && wp.original_coords && wp.original_coords.length === 2) {
                        snapFeatures.push({
                            type: 'Feature',
                            properties: {
                                offset_m: wp.snap_offset_m,
                                reason: wp.snap_reason || 'Shoreline berth snap'
                            },
                            geometry: {
                                type: 'LineString',
                                coordinates: [wp.original_coords, [wp.lon, wp.lat]]
                            }
                        });
                    }
                });
                map.getSource('snap-offsets').setData({ type: 'FeatureCollection', features: snapFeatures });
            }

            statusMsg.innerHTML =
                `<b>${distNm}</b> &nbsp;|&nbsp; Время перехода: <b>${eta}</b>` +
                ` &nbsp;|&nbsp; Мин. UKC: <b>${clearance}</b>` +
                ` &nbsp;|&nbsp; ${waypointsCount} РТ${isDemo ? ' &nbsp;|&nbsp; <span style="color:#ef4444;font-weight:600;">[СИНТЕТИКА]</span>' : ''}`;
            statusMsg.style.color = isUnsafe ? 'var(--status-danger)' : (sc.status === 'WARNING_ADVISORY' ? '#f59e0b' : 'var(--status-success)');

            renderWaypointsTable(data.waypoints || []);
            renderSafetyDashboard(data.safety_check, data.bottleneck, data.dukc);

            if (data.profile && data.profile.length) {
                const safeDepthLimit = (p && p.min_chart_depth_m) ? p.min_chart_depth_m : (payload.draft + dynamicSquat + payload.ukc - tideOffsetM);
                renderProfileChart(data.profile, safeDepthLimit);
            }

            if (bottomDrawer) bottomDrawer.style.display = 'flex';
            if (exportActions) exportActions.style.display = 'inline-flex';

            // Обновление и показ плавающей карточки перехода (Voyage Summary Card)
            if (voyageCard) {
                voyageCard.style.display = 'block';

                const depPort = blackSeaPorts.find(p => p.id === (portDepartSelect ? portDepartSelect.value : ''));
                const arrPort = blackSeaPorts.find(p => p.id === (portArriveSelect ? portArriveSelect.value : ''));
                if (depPort && arrPort && plannedWaypoints.length === 2) {
                    if (voyageCardTitle) voyageCardTitle.innerText = `${depPort.short_name || depPort.name} → ${arrPort.short_name || arrPort.name}`;
                } else if (voyageCardTitle) {
                    voyageCardTitle.innerText = `Переход (${waypointsCount} РТ)`;
                }

                if (voyageDistVal) voyageDistVal.innerText = distNm;
                if (voyageEtaVal) voyageEtaVal.innerText = eta;
                if (voyageDepthVal) voyageDepthVal.innerText = (p && p.min_chart_depth_m !== undefined) ? `${p.min_chart_depth_m.toFixed(1)} м` : '—';
                if (voyageUkcVal) voyageUkcVal.innerText = clearance;
                if (voyageDraftVal) voyageDraftVal.innerText = `${draft.toFixed(1)} м`;
                updateVoyageFuelDisplay();

                if (voyageStatusBadge) {
                    if (isUnsafe) {
                        voyageStatusBadge.innerText = isDemo ? 'ДЕМО' : 'ОПАСНОСТЬ';
                        voyageStatusBadge.className = 'badge-danger';
                    } else if (sc.status === 'WARNING_ADVISORY') {
                        voyageStatusBadge.innerText = 'ВНИМАНИЕ';
                        voyageStatusBadge.className = 'badge-warn';
                    } else {
                        voyageStatusBadge.innerText = 'БЕЗОПАСНО';
                        voyageStatusBadge.className = 'badge-safe';
                    }
                }
            }

            initSimulator(data);
        } else {
            const err = await res.json().catch(() => ({}));
            statusMsg.innerText = (err.detail || 'Безопасный проход не найден при текущих параметрах осадки.');
            statusMsg.style.color = 'var(--status-danger)';
            if (voyageCard) voyageCard.style.display = 'none';
            map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('route-corridor')) map.getSource('route-corridor').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('turn-arcs')) map.getSource('turn-arcs').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('bottleneck-point')) map.getSource('bottleneck-point').setData({ type: 'FeatureCollection', features: [] });
            if (map.getSource('xtd-hazards')) map.getSource('xtd-hazards').setData({ type: 'FeatureCollection', features: [] });
            if (topbarAlarmPill) topbarAlarmPill.style.display = 'none';
            renderSafetyDashboard(null, null, null);
            currentRouteData = null;
            stopSimulator();
        }
    } catch (err) {
        console.error('Ошибка расчета маршрута:', err);
        if (voyageCard) voyageCard.style.display = 'none';
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
        } else if (safetyData.status === 'DEMO_UNVERIFIED') {
            safetyBadge.innerText = 'ДЕМО / НЕ СЕРТИФИЦИРОВАН';
            safetyBadge.className = 'badge-danger';
        } else if (safetyData.status === 'WARNING_ADVISORY') {
            safetyBadge.innerText = `${safetyData.alarms_count} ПРЕДУПР.`;
            safetyBadge.className = 'badge-warn';
        } else {
            safetyBadge.innerText = 'НОРМА';
            safetyBadge.className = 'badge-safe';
        }
    }

    const bannerClass = (safetyData.status === 'CRITICAL_HAZARD' || safetyData.status === 'DEMO_UNVERIFIED')
        ? 'safety-banner-danger'
        : (safetyData.status === 'WARNING_ADVISORY' ? 'safety-banner-warning' : 'safety-banner-passed');

    let summaryText = safetyData.summary;
    if (safetyData.status === 'CRITICAL_HAZARD') {
        summaryText = 'Проверка безопасности ECDIS: ОТКЛОНЕНО. Обнаружен пробой безопасной глубины под килем.';
    } else if (safetyData.status === 'DEMO_UNVERIFIED') {
        summaryText = 'Проверка безопасности ECDIS: ДЕМО-РЕЖИМ. Маршрут построен на синтетических данных и НЕ сертифицирован для навигации.';
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
                    <span>${isHazard ? 'КРИТИЧЕСКИЙ ЛИМИТИРУЮЩИЙ СТВОР (ОТМЕЛЬ)' : 'КОНТРОЛЬНЫЙ ЛИМИТИРУЮЩИЙ СТВОР МАРШРУТА'}</span>
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
                    <b>Рекомендация по адаптации скорости:</b>
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
                    <b>Расчет приливного окна M2:</b> ${tw.notes}
                </div>
            `;
        }

        html += `</div>`;
    }

    // Гидродинамический баланс DUKC
    if (dukcData) {
        html += `
            <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 4px; padding: 10px 12px; margin-bottom: 10px; font-size: 11.5px;">
                <div style="font-weight: 600; color: #38bdf8; margin-bottom: 6px;">ГИДРОДИНАМИЧЕСКИЙ БАЛАНС ДВИЖЕНИЯ PIANC DUKC</div>
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
            } else if (a.code === 'ALM-04-XTD-CORRIDOR-HAZARD') {
                ruTitle = 'Отмель внутри полосы безопасности (XTD)';
            } else if (a.code === 'ALM-05-MANEUVERING-LIMIT-VIOLATION') {
                ruTitle = `Нарушение маневренных пределов судна на РТ${a.waypoint_index || ''}`;
            }

            let flyBtn = '';
            if (a.hazard_location && a.hazard_location.length === 2) {
                flyBtn = `<button class="alarm-btn-fly" onclick="map.flyTo({center: [${a.hazard_location[0]}, ${a.hazard_location[1]}], zoom: 12, speed: 1.2})">На карте (${a.hazard_depth_m ? a.hazard_depth_m.toFixed(1) + 'м' : ''})</button>`;
            } else if (a.waypoint_index && currentRouteData && currentRouteData.waypoints && currentRouteData.waypoints[a.waypoint_index - 1]) {
                const wpTarget = currentRouteData.waypoints[a.waypoint_index - 1];
                flyBtn = `<button class="alarm-btn-fly" onclick="map.flyTo({center: [${wpTarget.lon}, ${wpTarget.lat}], zoom: 12, speed: 1.2})">К РТ${a.waypoint_index}</button>`;
            }

            html += `
                <div class="alarm-card ${cardClass}">
                    <div class="alarm-header">
                        <span class="alarm-title">${ruTitle} ${flyBtn}</span>
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
                <b>Аудит безопасности пройден:</b> Линия перехода и полоса безопасности (XTD) сохраняют требуемый запас под килем (UKC) на всех батиметрических промерах. Запретные зоны не нарушены. Угловые скорости поворотов соответствуют мостиковым нормам.
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

    if (map.getSource('lookahead-guard')) {
        map.getSource('lookahead-guard').setData({ type: 'FeatureCollection', features: [] });
    }

    if (simPlayBtn) {
        simPlayBtn.innerText = 'СТАРТ';
        simPlayBtn.disabled = false;
    }
    if (simPauseBtn) simPauseBtn.disabled = true;
    if (simAlarmBanner) simAlarmBanner.style.display = 'none';
}

function calculateLookaheadSectorPolygon(centerLon, centerLat, headingDeg, distanceM, halfAngleDeg = 15) {
    const R = 6371000.0;
    const d = distanceM / R;
    const lat1 = (centerLat * Math.PI) / 180.0;
    const lon1 = (centerLon * Math.PI) / 180.0;

    const ring = [[centerLon, centerLat]];
    const startAngle = headingDeg - halfAngleDeg;
    const endAngle = headingDeg + halfAngleDeg;
    const steps = 14;

    for (let i = 0; i <= steps; i++) {
        const brgDeg = startAngle + (i / steps) * (endAngle - startAngle);
        const brgRad = (brgDeg * Math.PI) / 180.0;

        const lat2 = Math.asin(
            Math.sin(lat1) * Math.cos(d) +
            Math.cos(lat1) * Math.sin(d) * Math.cos(brgRad)
        );
        const lon2 = lon1 + Math.atan2(
            Math.sin(brgRad) * Math.sin(d) * Math.cos(lat1),
            Math.cos(d) - Math.sin(lat1) * Math.sin(lat2)
        );

        ring.push([(lon2 * 180.0) / Math.PI, (lat2 * 180.0) / Math.PI]);
    }
    ring.push([centerLon, centerLat]);

    return {
        type: 'Feature',
        geometry: {
            type: 'Polygon',
            coordinates: [ring]
        },
        properties: {}
    };
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
    let isApproachingWop = false;
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
        isApproachingWop = (distToNextWp <= wopDistM + 250 && distToNextWp >= wopDistM - 50);
    }

    // Динамический расчет упреждающего сектора безопасности (Look-Ahead Anti-Grounding Guard Zone)
    const lookaheadSec = 180; // 3 минуты хода
    const vMs = Math.max(1.0, speedKnots * 0.514444);
    const lookaheadDistM = Math.max(400.0, vMs * lookaheadSec);

    const guardFeature = calculateLookaheadSectorPolygon(pos.lon, pos.lat, pos.heading, lookaheadDistM, 15);

    // Поиск лимитирующих глубин впереди по профилю
    let minClearanceAhead = 999.0;
    let minDepthAhead = 999.0;
    let hazardDistM = null;

    if (simState.profile && simState.profile.length > 0) {
        for (let i = 0; i < simState.profile.length; i++) {
            const p = simState.profile[i];
            if (p.distance_from_start_m >= distM && p.distance_from_start_m <= distM + lookaheadDistM) {
                if (p.clearance < minClearanceAhead) {
                    minClearanceAhead = p.clearance;
                    minDepthAhead = p.depth;
                    if (p.clearance < 0.0 && hazardDistM === null) {
                        hazardDistM = Math.max(0, p.distance_from_start_m - distM);
                    }
                }
            }
        }
    }

    let guardColor = 'rgba(6, 182, 212, 0.15)';
    let guardLineColor = '#06b6d4';

    if (minClearanceAhead < 0.0) {
        // КРИТИЧЕСКИЙ АЛАРМ: Опасность посадки на мель внутри сектора упреждения (IEC 62288)
        playBridgeAlarmTone();
        guardColor = 'rgba(239, 68, 68, 0.40)';
        guardLineColor = '#ef4444';

        const hazTimeSec = hazardDistM !== null ? Math.round(hazardDistM / vMs) : 60;
        const hazTimeMin = Math.max(0.1, hazTimeSec / 60.0).toFixed(1);

        if (simAlarmBanner) {
            simAlarmBanner.style.display = 'block';
            simAlarmBanner.style.background = 'rgba(239, 68, 68, 0.25)';
            simAlarmBanner.style.borderColor = 'var(--status-danger)';
            simAlarmBanner.style.color = '#fca5a5';
            simAlarmBanner.innerHTML = `
                <b>КРИТИЧЕСКИЙ АЛАРМ (ALM-01): ОПАСНОСТЬ ПОСАДКИ НА МЕЛЬ ЧЕРЕЗ ${hazTimeMin} МИН!</b>
                В секторе упреждения обнаружена глубина <b>${minDepthAhead.toFixed(1)} м</b> (Отрицательный запас: <b>${minClearanceAhead.toFixed(2)} м</b>)!
            `;
        }
    } else if (isApproachingWop && simAlarmBanner && nextWp && Math.abs(nextWp.turn_angle_deg || 0) >= 4.0) {
        // ОПЕРАЦИОННЫЙ СИГНАЛ: Точка перекладки руля (WOP)
        simAlarmBanner.style.display = 'block';
        simAlarmBanner.style.background = 'rgba(56, 189, 248, 0.2)';
        simAlarmBanner.style.borderColor = 'var(--accent-blue)';
        simAlarmBanner.style.color = '#bae6fd';
        simAlarmBanner.innerHTML = `
            <b>ПЕРЕКЛАДКА РУЛЯ (WOP):</b> Подход к РТ${nextWp.index}!
            Поворот на ${nextWp.rot_deg_min > 0 ? 'Правый' : 'Левый'} борт (${nextWp.leg_bearing_deg}°T) —
            Угловая скорость ROT: <b>${Math.abs(nextWp.rot_deg_min)}°/мин</b> (Радиус: ${nextWp.turn_radius_m}м)
        `;
    } else if (minClearanceAhead < 1.0) {
        // ПРЕДУПРЕЖДЕНИЕ: Малый запас под килем впереди
        guardColor = 'rgba(245, 158, 11, 0.28)';
        guardLineColor = '#f59e0b';

        if (simAlarmBanner) {
            simAlarmBanner.style.display = 'block';
            simAlarmBanner.style.background = 'rgba(245, 158, 11, 0.2)';
            simAlarmBanner.style.borderColor = 'var(--status-warn)';
            simAlarmBanner.style.color = '#fde68a';
            simAlarmBanner.innerHTML = `
                <b>ВНИМАНИЕ: МЕЛКОВОДЬЕ ВПЕРЕДИ ПО КУРСУ (${(lookaheadDistM / 1852.0).toFixed(2)} ММ)!</b>
                Минимальная глубина: <b>${minDepthAhead.toFixed(1)} м</b> (Запас под килем: <b>${minClearanceAhead.toFixed(2)} м</b>).
            `;
        }
    } else if (simAlarmBanner && !isApproachingWop) {
        simAlarmBanner.style.display = 'none';
    }

    guardFeature.properties = {
        color: guardColor,
        lineColor: guardLineColor
    };

    if (map.getSource('lookahead-guard')) {
        map.getSource('lookahead-guard').setData({
            type: 'FeatureCollection',
            features: [guardFeature]
        });
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
            simPlayBtn.innerText = 'ПОВТОР';
            simPlayBtn.disabled = false;
        }
        if (simPauseBtn) simPauseBtn.disabled = true;
        if (simAlarmBanner) {
            simAlarmBanner.style.display = 'block';
            simAlarmBanner.style.background = 'rgba(16, 185, 129, 0.2)';
            simAlarmBanner.style.borderColor = 'var(--status-success)';
            simAlarmBanner.style.color = '#6ee7b7';
            simAlarmBanner.innerHTML = '<b>ПЕРЕХОД ЗАВЕРШЕН:</b> Судно благополучно прибыло в порт назначения.';
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
        simPlayBtn.innerText = 'В ХОДУ';
        if (simPauseBtn) simPauseBtn.disabled = false;
        simState.animFrameId = requestAnimationFrame(simAnimationFrame);
    });
}

if (simPauseBtn) {
    simPauseBtn.addEventListener('click', () => {
        simState.isRunning = false;
        if (simState.animFrameId) cancelAnimationFrame(simState.animFrameId);
        simPlayBtn.disabled = false;
        simPlayBtn.innerText = 'ПРОДОЛЖИТЬ';
        simPauseBtn.disabled = true;
    });
}

if (simResetBtn) {
    simResetBtn.addEventListener('click', () => {
        simState.isRunning = false;
        simState.currentDistanceM = 0;
        if (simState.animFrameId) cancelAnimationFrame(simState.animFrameId);
        simPlayBtn.disabled = false;
        simPlayBtn.innerText = 'СТАРТ';
        if (simPauseBtn) simPauseBtn.disabled = true;
        updateSimulatorHUD(0);
        if (simAlarmBanner) simAlarmBanner.style.display = 'none';
        if (map.getSource('lookahead-guard')) {
            map.getSource('lookahead-guard').setData({ type: 'FeatureCollection', features: [] });
        }
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
        if (!currentRouteData) return;
        const p = currentRouteData.properties || {};
        const sc = currentRouteData.safety_check || {};
        const planId = currentRouteData.plan_id || p.plan_id;
        const payload = planId ? {
            plan_id: planId,
            route_name: 'SeaPath_Passage_Plan'
        } : {
            waypoints: currentRouteData.waypoints || [],
            route: currentRouteData.geometry ? currentRouteData.geometry.coordinates : [],
            draft: draft,
            speed_knots: speedKnots,
            ukc: baseUkc,
            safety_contour: p.min_chart_depth_m,
            safety_status: sc.status,
            is_safe: sc.is_safe,
            route_name: 'SeaPath_Passage_Plan'
        };
        triggerDownload('/api/export/gpx', 'seapath_passage_plan.gpx', payload);
    });
}

if (exportRtzBtn) {
    exportRtzBtn.addEventListener('click', () => {
        if (!currentRouteData) return;
        const p = currentRouteData.properties || {};
        const sc = currentRouteData.safety_check || {};
        const planId = currentRouteData.plan_id || p.plan_id;
        const payload = planId ? {
            plan_id: planId,
            route_name: 'SeaPath_Passage_Plan'
        } : {
            waypoints: currentRouteData.waypoints || [],
            draft: draft,
            speed_knots: speedKnots,
            ukc: baseUkc,
            safety_contour: p.min_chart_depth_m,
            safety_status: sc.status,
            is_safe: sc.is_safe,
            route_name: 'SeaPath_Passage_Plan'
        };
        triggerDownload('/api/export/rtz', 'seapath_passage_plan.rtz', payload);
    });
}

// -----------------------------------------------------------------------------
// 15. Аудио-зуммер ходового мостика ЭКНИС (IEC 62288 Bridge Audio Alarm)
// -----------------------------------------------------------------------------
function playBridgeAlarmTone() {
    if (isAudioMuted) return;
    const now = Date.now();
    if (now - lastAudioBeepTime < 2500) return;
    lastAudioBeepTime = now;

    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        if (!audioAlarmCtx) audioAlarmCtx = new AudioCtx();
        if (audioAlarmCtx.state === 'suspended') {
            audioAlarmCtx.resume();
        }

        const osc = audioAlarmCtx.createOscillator();
        const gain = audioAlarmCtx.createGain();
        osc.type = 'square';

        // IEC 62288: чередующиеся импульсы повышенной бдительности (960 Гц / 770 Гц)
        const t0 = audioAlarmCtx.currentTime;
        osc.frequency.setValueAtTime(960, t0);
        osc.frequency.setValueAtTime(770, t0 + 0.12);
        osc.frequency.setValueAtTime(960, t0 + 0.24);
        osc.frequency.setValueAtTime(770, t0 + 0.36);

        gain.gain.setValueAtTime(0.08, t0);
        gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.48);

        osc.connect(gain);
        gain.connect(audioAlarmCtx.destination);
        osc.start(t0);
        osc.stop(t0 + 0.48);
    } catch (e) {
        console.warn('Bridge Web Audio error:', e);
    }
}

if (toolMuteAlarmBtn) {
    toolMuteAlarmBtn.addEventListener('click', () => {
        isAudioMuted = !isAudioMuted;
        if (isAudioMuted) {
            toolMuteAlarmBtn.innerText = 'ЗВУК: ВЫКЛ';
            toolMuteAlarmBtn.style.color = '#ef4444';
            toolMuteAlarmBtn.style.borderColor = '#ef4444';
        } else {
            toolMuteAlarmBtn.innerText = 'ЗУММЕР';
            toolMuteAlarmBtn.style.color = '';
            toolMuteAlarmBtn.style.borderColor = '';
            playBridgeAlarmTone();
        }
    });
}

// -----------------------------------------------------------------------------
// 16. Съемка эхолотом (Sonar Survey & IHO S-44 Shoal-Bias Fusion)
// -----------------------------------------------------------------------------
const DEMO_SEVASTOPOL_CSV = `lat,lon,depth_m
44.6150,33.5250,18.5
44.6155,33.5280,17.8
44.6160,33.5310,16.9
44.6165,33.5340,16.2
44.6170,33.5370,15.5
44.6175,33.5400,14.8
44.6180,33.5430,14.1
44.6185,33.5460,13.5
44.6190,33.5490,12.8
44.6195,33.5520,12.0
44.6200,33.5550,11.2
44.6205,33.5580,10.5
44.6210,33.5610,9.8
44.6215,33.5640,9.1
44.6220,33.5670,8.4
44.6225,33.5700,7.6
44.6230,33.5730,6.8
44.6120,33.5260,19.2
44.6130,33.5300,17.5
44.6140,33.5350,16.0
44.6145,33.5400,14.2
44.6150,33.5450,13.0
44.6160,33.5500,11.5
44.6170,33.5550,10.2
44.6180,33.5600,8.8
44.6190,33.5650,7.5
44.6200,33.5700,6.2
44.6210,33.5750,5.1
44.6180,33.5250,17.0
44.6190,33.5300,15.8
44.6200,33.5350,14.5
44.6210,33.5400,13.1
44.6220,33.5450,11.9
44.6230,33.5500,10.5
44.6240,33.5550,9.0
44.6250,33.5600,7.8
44.6260,33.5650,6.5
44.6270,33.5700,5.4
44.6162,33.5385,4.2
44.6164,33.5390,3.8
44.6166,33.5395,4.0
44.6202,33.5582,3.5
44.6205,33.5588,3.2
44.6208,33.5592,3.6`;

function handleSonarSurveyResponse(data) {
    if (!data || !data.success) {
        alert(data && data.error ? data.error : 'Не удалось обработать батиметрическую съемку.');
        return;
    }

    if (map.getSource('custom-bathymetry') && data.geojson) {
        map.getSource('custom-bathymetry').setData(data.geojson);
    }
    if (map.getSource('custom-soundings') && data.geojson) {
        const pts = {
            type: 'FeatureCollection',
            features: (data.geojson.features || []).filter(f => f.geometry && f.geometry.type === 'Point')
        };
        map.getSource('custom-soundings').setData(pts);
    }

    const s = data.statistics || {};
    if (sonarSurveyStats) {
        sonarSurveyStats.style.display = 'block';
        sonarSurveyStats.innerHTML = `
            <div style="font-weight: 600; color: #38bdf8; margin-bottom: 4px;">Съемка эхолотом интегрирована</div>
            <div>Промеров: <b>${s.soundings_count || 0}</b> | Диапазон глубин: <b>${s.min_depth_m !== undefined ? s.min_depth_m.toFixed(1) : '—'} - ${s.max_depth_m !== undefined ? s.max_depth_m.toFixed(1) : '—'} м</b></div>
            <div>Площадь: <b>${s.area_sq_km !== undefined ? s.area_sq_km.toFixed(3) : '—'} км²</b> | Обновлено ячеек: <b>${s.cells_fused_into_grid || 0}</b></div>
            <div style="color: #10b981; font-size: 11px; margin-top: 4px;">Сетка глубин обновлена по правилу наименьшей глубины (IHO S-44 Shoal-bias)</div>
        `;
    }

    if (s.bounds && s.bounds.length === 4) {
        map.fitBounds([
            [s.bounds[0], s.bounds[1]],
            [s.bounds[2], s.bounds[3]]
        ], { padding: 50, duration: 800 });
    }

    statusMsg.innerText = `Съемка эхолота (${s.soundings_count || 0} промеров) внедрена в батиметрическую сетку.`;
    statusMsg.style.color = '#38bdf8';

    if (plannedWaypoints.length >= 2) {
        triggerRouteRecalculation(true);
    }
}

if (btnTriggerSonarUpload && sonarFileInput) {
    btnTriggerSonarUpload.addEventListener('click', () => {
        sonarFileInput.click();
    });

    sonarFileInput.addEventListener('change', async (e) => {
        const file = e.target.files && e.target.files[0];
        if (!file) return;

        btnTriggerSonarUpload.disabled = true;
        btnTriggerSonarUpload.innerText = 'Обработка...';

        try {
            const rawContent = await file.text();
            const res = await fetch('/api/bathymetry/upload-sonar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    raw_content: rawContent,
                    filename: file.name,
                    is_dredged: false,
                    grid_size: 70,
                    power: 2.0,
                    search_radius_m: 1200.0
                })
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Ошибка сервера (HTTP ${res.status})`);
            }

            const data = await res.json();
            handleSonarSurveyResponse(data);
        } catch (err) {
            console.error('Ошибка загрузки лога эхолота:', err);
            alert(`Ошибка импорта промеров эхолота: ${err.message}`);
        } finally {
            btnTriggerSonarUpload.disabled = false;
            btnTriggerSonarUpload.innerText = 'Загрузить лог эхолота (CSV/NMEA/GPX)';
            sonarFileInput.value = '';
        }
    });
}

if (btnDemoSonarData) {
    btnDemoSonarData.addEventListener('click', async () => {
        btnDemoSonarData.disabled = true;
        btnDemoSonarData.innerText = 'Интеграция...';

        try {
            const res = await fetch('/api/bathymetry/upload-sonar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    raw_content: DEMO_SEVASTOPOL_CSV,
                    filename: 'sevastopol_survey.csv',
                    is_dredged: false,
                    grid_size: 70,
                    power: 2.0,
                    search_radius_m: 1200.0
                })
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Ошибка сервера (HTTP ${res.status})`);
            }

            const data = await res.json();
            handleSonarSurveyResponse(data);
        } catch (err) {
            console.error('Ошибка загрузки демо-промеров:', err);
            alert(`Ошибка импорта демо-промеров: ${err.message}`);
        } finally {
            btnDemoSonarData.disabled = false;
            btnDemoSonarData.innerText = 'Демо: Промеры Севастопольской бухты';
        }
    });
}

// -----------------------------------------------------------------------------
// 17. Официальный план перехода IMO Res. A.893(21) / SOLAS V-34
// -----------------------------------------------------------------------------
async function openPassagePlanModal() {
    if (!currentRouteData || plannedWaypoints.length < 2) {
        alert('Пожалуйста, сначала проложите маршрут на карте (минимум 2 точки).');
        return;
    }

    if (passagePlanModal) {
        passagePlanModal.style.display = 'flex';
    }

    if (passagePlanContent) {
        passagePlanContent.innerHTML = '<div style="padding: 40px; text-align: center; color: #94a3b8;">Генерация официального плана перехода IMO Res. A.893(21)...</div>';
    }

    try {
        const payload = {
            route_data: currentRouteData,
            vessel_profile: vesselSelect.value || 'yacht',
            vessel_name: vesselSelect.value === 'cargo' ? 'M/V BLACK SEA TRADER' : (vesselSelect.value === 'ferry' ? 'M/F CRIMEAN STAR' : 'S/Y SEAPATH NAVIGATOR'),
            draft: draft,
            speed_knots: speedKnots,
            ukc: baseUkc,
            wave_height_m: waveHeightM,
            tide_offset_m: tideOffsetM,
            xtd_m: portXtdM,
            fuel_burn_l_h: fuelRateLh
        };

        const res = await fetch('/api/passage-plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Ошибка сервера (HTTP ${res.status})`);
        }

        const plan = await res.json();
        renderPassagePlanDocument(plan);
    } catch (err) {
        console.error('Ошибка создания плана перехода:', err);
        if (passagePlanContent) {
            passagePlanContent.innerHTML = `<div style="padding: 30px; color: #ef4444;">Не удалось сформировать план перехода: ${err.message}</div>`;
        }
    }
}

function renderPassagePlanDocument(plan) {
    if (!passagePlanContent || !plan) return;

    const v = plan.vessel || {};
    const voy = plan.voyage_summary || {};
    const d = plan.dukc_safety_budget || {};
    const f = plan.bunkering_fuel_budget || {};
    const wps = plan.waypoints || [];
    const bot = plan.critical_bottleneck;
    const ref = plan.ports_of_refuge || [];

    const isSafe = d.safety_status === 'SAFE';

    let html = `
        <div class="passage-report-header">
            <div>
                <h2 class="passage-report-title">OFFICIAL PASSAGE PLAN / ПЛАН МОРСКОГО ПЕРЕХОДА</h2>
                <div class="passage-report-sub">Standard: IMO Res. A.893(21) / SOLAS V Reg. 34 / PIANC MarCom WG 121</div>
            </div>
            <div class="passage-report-meta">
                <div>План №: <b>${plan.passage_plan_id}</b></div>
                <div>Дата/время: <b>${plan.timestamp_utc}</b></div>
                <div>Статус: <b style="color: ${isSafe ? '#10b981' : '#ef4444'};">${isSafe ? 'ОДОБРЕНО К ПЕРЕХОДУ' : 'ТРЕБУЕТ ВНИМАНИЯ'}</b></div>
            </div>
        </div>

        <div class="passage-grid-4">
            <div class="passage-card">
                <div class="passage-card-title">1. Сведения о судне</div>
                <div class="passage-row"><span>Название:</span> <b>${v.name}</b></div>
                <div class="passage-row"><span>Тип судна:</span> <b>${v.type_profile ? v.type_profile.toUpperCase() : 'YACHT'}</b></div>
                <div class="passage-row"><span>Статическая осадка:</span> <b>${v.static_draft_m ? v.static_draft_m.toFixed(2) + ' м' : '—'}</b></div>
                <div class="passage-row"><span>Скорость хода:</span> <b>${v.speed_knots ? v.speed_knots.toFixed(1) + ' уз' : '—'}</b></div>
                <div class="passage-row"><span>Коэфф. полноты Cb:</span> <b>${v.block_coefficient ? v.block_coefficient.toFixed(2) : '—'}</b></div>
            </div>

            <div class="passage-card">
                <div class="passage-card-title">2. Параметры рейса</div>
                <div class="passage-row"><span>Порт отхода:</span> <b>${voy.departure} (${voy.departure_vhf})</b></div>
                <div class="passage-row"><span>Порт прихода:</span> <b>${voy.destination} (${voy.destination_vhf})</b></div>
                <div class="passage-row"><span>Дистанция пути:</span> <b>${voy.total_distance_nm ? voy.total_distance_nm.toFixed(2) + ' ММ' : '—'}</b></div>
                <div class="passage-row"><span>Ходовое время (ETE):</span> <b>${voy.estimated_time_enroute || '—'}</b></div>
                <div class="passage-row"><span>Ширина полосы XTD:</span> <b>±${Math.round(voy.corridor_xtd_m || 185)} м</b></div>
            </div>

            <div class="passage-card">
                <div class="passage-card-title">3. Гидродинамический бюджет DUKC</div>
                <div class="passage-row"><span>Статическая осадка:</span> <b>${d.static_draft_m ? d.static_draft_m.toFixed(2) + ' м' : '—'}</b></div>
                <div class="passage-row"><span>Squat (Баррасс):</span> <b>+${d.barrass_squat_m ? d.barrass_squat_m.toFixed(2) + ' м' : '0.00 м'}</b></div>
                <div class="passage-row"><span>Волновой запас (0.35Hs):</span> <b>+${d.wave_allowance_m ? d.wave_allowance_m.toFixed(2) + ' м' : '0.00 м'}</b></div>
                <div class="passage-row"><span>Динамическая осадка:</span> <b>${d.dynamic_draft_m ? d.dynamic_draft_m.toFixed(2) + ' м' : '—'}</b></div>
                <div class="passage-row"><span>Требуемый UKC:</span> <b>${d.base_ukc_required_m ? d.base_ukc_required_m.toFixed(2) + ' м' : '—'}</b></div>
                <div class="passage-row" style="border-top: 1px solid rgba(255,255,255,0.1); margin-top: 4px; padding-top: 4px;">
                    <span style="color: #38bdf8;">Изобата безопасности:</span> <b style="color: #38bdf8;">${d.minimum_safety_chart_depth_m ? d.minimum_safety_chart_depth_m.toFixed(2) + ' м' : '—'}</b>
                </div>
            </div>

            <div class="passage-card">
                <div class="passage-card-title">4. Бункеровка и расход топлива</div>
                <div class="passage-row"><span>Норма расхода:</span> <b>${Math.round(v.fuel_burn_rate_l_h || 18)} л/ч</b></div>
                <div class="passage-row"><span>Номинальный расход:</span> <b>${f.nominal_consumption_liters ? Math.round(f.nominal_consumption_liters) + ' л' : '—'}</b></div>
                <div class="passage-row"><span>Резерв IMO (+20%):</span> <b>${f.reserve_contingency_liters ? Math.round(f.reserve_contingency_liters) + ' л' : '—'}</b></div>
                <div class="passage-row" style="border-top: 1px solid rgba(255,255,255,0.1); margin-top: 4px; padding-top: 4px;">
                    <span style="color: #10b981;">Всего к бункеровке:</span> <b style="color: #10b981;">${f.total_fuel_required_liters ? Math.round(f.total_fuel_required_liters) + ' л' : '—'}</b>
                </div>
                ${(f.total_fuel_required_liters && f.total_fuel_required_liters >= 1000) ? `<div style="font-size: 11px; color: #94a3b8; text-align: right;">(${(f.total_fuel_required_liters / 1000).toFixed(2)} метрич. тонн)</div>` : ''}
            </div>
        </div>
    `;

    if (bot && bot.depth_m) {
        html += `
            <div style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.4); padding: 12px 16px; border-radius: 6px; margin-bottom: 18px;">
                <div style="font-weight: 700; color: #fca5a5; font-size: 13px; margin-bottom: 4px;">
                    ЛИМИТИРУЮЩИЙ УЧАСТОК ПЕРЕХОДА (SHALLOW BOTTLENECK)
                </div>
                <div style="color: #cbd5e1; font-size: 12px; line-height: 1.5;">
                    Координаты: <b>${bot.coords_dmm || '—'}</b> &nbsp;|&nbsp;
                    Глубина по карте: <b>${bot.depth_m.toFixed(2)} м</b> &nbsp;|&nbsp;
                    Остаточный UKC: <b>${bot.clearance_m.toFixed(2)} м</b> &nbsp;|&nbsp;
                    Дистанция от отхода: <b>${bot.distance_from_start_nm ? bot.distance_from_start_nm.toFixed(1) + ' ММ' : '—'}</b>
                    ${bot.recommended_speed_knots ? `<br><span style="color: #fbbf24;">Рекомендация: снизить скорость до <b>${bot.recommended_speed_knots.toFixed(1)} уз</b> для уменьшения проседания корпуса (Squat).</span>` : ''}
                </div>
            </div>
        `;
    }

    html += `
        <div style="margin-bottom: 18px;">
            <div class="passage-section-title">ТАБЛИЦА ПУТЕВЫХ ТОЧЕК И ПЛЕЧ ПЕРЕХОДА (WAYPOINTS SCHEDULE)</div>
            <table class="passage-table">
                <thead>
                    <tr>
                        <th>№</th>
                        <th>Роль</th>
                        <th>Широта (DMM)</th>
                        <th>Долгота (DMM)</th>
                        <th>Курс (°T)</th>
                        <th>Дист (ММ)</th>
                        <th>Сумм (ММ)</th>
                        <th>Скорость</th>
                        <th>ETE</th>
                        <th>Радиус</th>
                        <th>WOP</th>
                    </tr>
                </thead>
                <tbody>
    `;

    wps.forEach(wp => {
        const brg = wp.bearing_true !== null && wp.bearing_true !== undefined ? `${wp.bearing_true.toFixed(1)}°` : '—';
        const dist = wp.leg_dist_nm !== null && wp.leg_dist_nm !== undefined ? wp.leg_dist_nm.toFixed(2) : '—';
        const cum = wp.cum_dist_nm !== null && wp.cum_dist_nm !== undefined ? wp.cum_dist_nm.toFixed(2) : '—';
        const ete = wp.leg_ete_min !== undefined ? `${wp.leg_ete_min} мин` : '—';
        const rad = wp.turn_radius_m ? `${Math.round(wp.turn_radius_m)}м` : '—';
        const wop = wp.wop_dist_m ? `${Math.round(wp.wop_dist_m)}м` : '—';

        html += `
            <tr>
                <td><b>${wp.index}</b></td>
                <td>${wp.role}</td>
                <td><code>${wp.lat_dmm}</code></td>
                <td><code>${wp.lon_dmm}</code></td>
                <td><b>${brg}</b></td>
                <td>${dist}</td>
                <td>${cum}</td>
                <td>${wp.speed_knots ? wp.speed_knots.toFixed(1) + ' уз' : '—'}</td>
                <td>${ete}</td>
                <td>${rad}</td>
                <td>${wop}</td>
            </tr>
        `;
    });

    html += `
                </tbody>
            </table>
        </div>
    `;

    if (ref && ref.length) {
        html += `
            <div style="margin-bottom: 18px;">
                <div class="passage-section-title">МЕСТА УБЕЖИЩА И РЕЙДОВЫЕ СТОЯНКИ (PORTS OF REFUGE / ROADSTEADS)</div>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px;">
        `;
        ref.forEach(r => {
            html += `
                <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255,255,255,0.08); padding: 10px; border-radius: 4px; font-size: 12px;">
                    <div style="font-weight: 600; color: #34d399; margin-bottom: 2px;">${r.name}</div>
                    <div>Глубины: <b>${r.depth_range_m}</b> | ${r.vhf_channel || 'VHF 16'}</div>
                    <div style="color: #94a3b8; font-size: 11px;">Грунт: ${r.holding_ground || 'Ил/песок'} | Укрытие: ${r.shelter || 'Все ветры'}</div>
                </div>
            `;
        });
        html += `
                </div>
            </div>
        `;
    }

    html += `
        <div class="passage-signoff-box">
            <div class="passage-signoff-col">
                <div style="font-weight: 600; color: #cbd5e1; margin-bottom: 24px;">СОСТАВИЛ (Prepared by OOW):</div>
                <div style="border-bottom: 1px dashed #64748b; margin-bottom: 6px;"></div>
                <div style="font-size: 11px; color: #94a3b8;">Вахтенный помощник капитана / Подпись / Дата</div>
            </div>
            <div class="passage-signoff-col">
                <div style="font-weight: 600; color: #cbd5e1; margin-bottom: 24px;">УТВЕРДИЛ (Approved by Master):</div>
                <div style="border-bottom: 1px dashed #64748b; margin-bottom: 6px;"></div>
                <div style="font-size: 11px; color: #94a3b8;">Капитан судна / Личная подпись / Дата</div>
            </div>
            <div class="passage-signoff-col" style="text-align: center;">
                <div style="display: inline-block; width: 110px; height: 110px; border: 2px dashed rgba(255,255,255,0.2); border-radius: 50%; padding-top: 38px; box-sizing: border-box; font-size: 11px; color: #64748b;">
                    М.П. / SEAL
                </div>
            </div>
        </div>
    `;

    passagePlanContent.innerHTML = html;
}

if (voyageBtnPassagePlan) {
    voyageBtnPassagePlan.addEventListener('click', openPassagePlanModal);
}

if (exportPassagePlanBtn) {
    exportPassagePlanBtn.addEventListener('click', openPassagePlanModal);
}

if (btnPrintPassagePlan) {
    btnPrintPassagePlan.addEventListener('click', () => {
        window.print();
    });
}

if (btnClosePassagePlan) {
    btnClosePassagePlan.addEventListener('click', () => {
        if (passagePlanModal) passagePlanModal.style.display = 'none';
    });
}

if (passagePlanModal) {
    passagePlanModal.addEventListener('click', (e) => {
        if (e.target === passagePlanModal) {
            passagePlanModal.style.display = 'none';
        }
    });
}

// -----------------------------------------------------------------------------
// 19. Модуль единиц глубин, водоемов, GPS и живого эхолота
// -----------------------------------------------------------------------------

// 19.1. Единицы измерения глубин (Метры / Футы)
let currentDepthUnit = 'm'; // 'm' | 'ft'
const toolUnitToggleBtn = document.getElementById('tool-unit-toggle');

function formatDepthVal(depthM, decimals = 1) {
    if (depthM === null || depthM === undefined || isNaN(depthM)) return '--';
    if (currentDepthUnit === 'ft') {
        const ft = Number(depthM) * 3.28084;
        return `${ft.toFixed(decimals)} ft`;
    }
    return `${Number(depthM).toFixed(decimals)} м`;
}

if (toolUnitToggleBtn) {
    toolUnitToggleBtn.addEventListener('click', () => {
        currentDepthUnit = (currentDepthUnit === 'm') ? 'ft' : 'm';
        toolUnitToggleBtn.innerText = `Глубины: ${currentDepthUnit === 'm' ? 'м' : 'ft'}`;
        if (currentRouteData && currentRouteData.properties) {
            updateVoyageSummaryCard(currentRouteData.properties);
        }
    });
}

// 19.2. Управление водоемами (Морские бухты, реки, озера, водохранилища)
const waterbodySelect = document.getElementById('waterbody-select');
const activeWbCategoryBadge = document.getElementById('wb-category-badge');
const activeWbTitleText = document.getElementById('wb-title-text');
const activeWbMetaText = document.getElementById('wb-meta-text');
let activeWaterbodyId = 'black_sea';
let activeWaterbodyBounds = [27.0, 40.5, 42.0, 47.0];

function loadWaterbodiesList() {
    fetch('/api/waterbodies')
        .then(r => r.json())
        .then(list => {
            if (!waterbodySelect || !Array.isArray(list)) return;
            waterbodySelect.innerHTML = '';
            list.forEach(wb => {
                const opt = document.createElement('option');
                opt.value = wb.id;
                opt.innerText = wb.name;
                if (wb.is_active) {
                    opt.selected = true;
                    activeWaterbodyId = wb.id;
                    activeWaterbodyBounds = wb.bounds;
                    updateWaterbodyInfoUI(wb);
                }
                waterbodySelect.appendChild(opt);
            });
        })
        .catch(err => console.warn('Ошибка загрузки списка водоемов:', err));
}

function updateWaterbodyInfoUI(wb) {
    if (!wb) return;
    if (activeWbCategoryBadge) {
        const catMap = {
            'sea': 'МОРЕ',
            'bay': 'БУХТА',
            'river': 'РЕКА',
            'lake': 'ОЗЕРО',
            'reservoir': 'ВОДОХРАНИЛИЩЕ',
            'custom': 'ПРОЕКТ'
        };
        activeWbCategoryBadge.innerText = catMap[wb.category] || (wb.category || 'ВОДОЕМ').toUpperCase();
        activeWbCategoryBadge.className = `wb-badge wb-badge-${wb.category || 'sea'}`;
    }
    if (activeWbTitleText) {
        activeWbTitleText.innerText = wb.name;
    }
    if (activeWbMetaText) {
        const gridDim = (wb.grid_dimensions && wb.grid_dimensions.length === 2) ? `${wb.grid_dimensions[0]}x${wb.grid_dimensions[1]} ячеек` : '';
        const maxD = wb.max_depth_m ? `макс. глубина ${formatDepthVal(wb.max_depth_m)}` : '';
        activeWbMetaText.innerText = wb.description || [gridDim, maxD].filter(Boolean).join(' | ');
    }
}

function switchWaterbody(wbId, fly = true) {
    fetch('/api/waterbody/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ waterbody_id: wbId })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success && data.active_waterbody) {
            const wb = data.active_waterbody;
            activeWaterbodyId = wb.id;
            activeWaterbodyBounds = wb.bounds;
            updateWaterbodyInfoUI(wb);
            if (waterbodySelect) waterbodySelect.value = wb.id;

            clearRouteData();

            if (fly && wb.bounds && wb.bounds.length === 4) {
                map.fitBounds([
                    [wb.bounds[0], wb.bounds[1]],
                    [wb.bounds[2], wb.bounds[3]]
                ], { padding: 50, duration: 800 });
            } else if (fly && wb.center && wb.center.length === 2) {
                map.flyTo({ center: wb.center, zoom: wb.zoom || 11.0, duration: 800 });
            }

            reloadPortsAndAnchorages();
        }
    })
    .catch(err => console.error('Ошибка активации водоема:', err));
}

if (waterbodySelect) {
    waterbodySelect.addEventListener('change', (e) => {
        switchWaterbody(e.target.value, true);
    });
}

function reloadPortsAndAnchorages() {
    fetch('/api/ports')
        .then(res => res.json())
        .then(ports => {
            blackSeaPorts = ports || [];
            initPortsUI(blackSeaPorts);

            const portFeatures = blackSeaPorts.map(p => ({
                type: 'Feature',
                properties: {
                    id: p.id,
                    name: p.name,
                    short_name: p.short_name || p.name,
                    depth_m: p.depth_m,
                    vhf_channel: p.vhf_channel,
                    type: p.type,
                    description: p.description
                },
                geometry: {
                    type: 'Point',
                    coordinates: p.port_coords
                }
            }));

            if (map.getSource('ports-data')) {
                map.getSource('ports-data').setData({
                    type: 'FeatureCollection',
                    features: portFeatures
                });
            }
        })
        .catch(err => console.warn('Не удалось обновить порты водоема:', err));

    fetch('/api/anchorages')
        .then(res => res.json())
        .then(anchorages => {
            blackSeaAnchorages = anchorages || [];
            const anchorageFeatures = blackSeaAnchorages.map(a => ({
                type: 'Feature',
                properties: {
                    id: a.id,
                    name: a.name,
                    depth_range: `${a.depth_min_m}-${a.depth_max_m} м`,
                    max_draft: `${a.max_draft_m} м`,
                    holding_ground: a.bottom_nature,
                    shelter_from: a.sheltered_from_winds ? a.sheltered_from_winds.join(', ') : 'Открытая',
                    vhf_channel: a.vhf_channel,
                    notes: a.notes
                },
                geometry: {
                    type: 'Point',
                    coordinates: a.coords
                }
            }));

            if (map.getSource('anchorages-data')) {
                map.getSource('anchorages-data').setData({
                    type: 'FeatureCollection',
                    features: anchorageFeatures
                });
            }
        })
        .catch(err => console.warn('Не удалось обновить якорные стоянки водоема:', err));
}

// 19.3. Глобальный поиск водоемов в мире (OSM / Nominatim)
const geoSearchInput = document.getElementById('geo-search-input');
const btnGeoSearch = document.getElementById('btn-geo-search');
const geoSearchResults = document.getElementById('geo-search-results');

function executeGeoSearch() {
    const q = geoSearchInput ? geoSearchInput.value.trim() : '';
    if (!q || q.length < 2) return;

    if (btnGeoSearch) btnGeoSearch.innerText = '...';
    let vbParam = '';
    if (typeof map !== 'undefined' && map && map.getBounds) {
        const b = map.getBounds();
        vbParam = `&viewbox=${b.getWest().toFixed(4)},${b.getSouth().toFixed(4)},${b.getEast().toFixed(4)},${b.getNorth().toFixed(4)}`;
    }

    fetch(`/api/geo/search?q=${encodeURIComponent(q)}${vbParam}`)
        .then(r => r.json())
        .then(results => {
            if (btnGeoSearch) btnGeoSearch.innerText = 'Поиск';
            renderGeoSearchResults(results);
        })
        .catch(err => {
            if (btnGeoSearch) btnGeoSearch.innerText = 'Поиск';
            console.error('Ошибка поиска водоема:', err);
        });
}

function renderGeoSearchResults(results) {
    if (!geoSearchResults) return;
    geoSearchResults.innerHTML = '';
    if (!results || results.length === 0) {
        geoSearchResults.innerHTML = '<div style="padding: 10px; font-size: 11px; color: #94a3b8;">Водоемы не найдены. Уточните запрос (например: "р. Чёрная", "Балаклава", "Волга", "Ладога").</div>';
        geoSearchResults.style.display = 'block';
        return;
    }

    results.forEach(res => {
        const item = document.createElement('div');
        item.className = 'geo-search-item';
        if (res.is_preset) {
            item.innerHTML = `
                <div class="geo-search-item-title">${res.name} <span class="wb-badge" style="background: #10b981; color: #ffffff; margin-left: 4px; font-weight: 600;">ГОТОВАЯ КАРТА</span></div>
                <div class="geo-search-item-sub" style="color: #6ee7b7;">${res.display_name}</div>
            `;
            item.addEventListener('click', () => {
                geoSearchResults.style.display = 'none';
                switchWaterbody(res.preset_id, true);
            });
        } else {
            const cat = res.category || 'river';
            item.innerHTML = `
                <div class="geo-search-item-title">${res.name} <span class="wb-badge wb-badge-${cat}" style="margin-left: 4px;">${cat.toUpperCase()}</span></div>
                <div class="geo-search-item-sub">${res.display_name}</div>
            `;
            item.addEventListener('click', () => {
                geoSearchResults.style.display = 'none';
                createAndActivateWaterbodyFromSearch(res);
            });
        }
        geoSearchResults.appendChild(item);
    });
    geoSearchResults.style.display = 'block';
}

function createAndActivateWaterbodyFromSearch(res) {
    fetch('/api/waterbody/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name: res.name,
            category: res.category || 'river',
            bbox: res.bbox
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success && data.created_waterbody) {
            loadWaterbodiesList();
            switchWaterbody(data.created_waterbody.id, true);
        }
    })
    .catch(err => console.error('Ошибка создания акватории:', err));
}

if (btnGeoSearch) {
    btnGeoSearch.addEventListener('click', executeGeoSearch);
}
if (geoSearchInput) {
    geoSearchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            executeGeoSearch();
        }
    });
}
document.addEventListener('click', (e) => {
    if (geoSearchResults && !geoSearchResults.contains(e.target) && e.target !== geoSearchInput && e.target !== btnGeoSearch) {
        geoSearchResults.style.display = 'none';
    }
});

// 19.4. Картплоттер и GPS отслеживание положения судна
const toolGpsBoatBtn = document.getElementById('tool-gps-boat-btn');
let isGpsWatchActive = false;
let gpsWatchId = null;
let boatMarker = null;
let lastBoatCoords = null;
let lastBoatHdg = 0.0;
let lastBoatSog = 0.0;

function toggleGpsChartplotter() {
    if (isGpsWatchActive) {
        stopGpsChartplotter();
    } else {
        startGpsChartplotter();
    }
}

function startGpsChartplotter() {
    isGpsWatchActive = true;
    if (toolGpsBoatBtn) {
        toolGpsBoatBtn.innerText = 'GPS: Активен';
        toolGpsBoatBtn.style.background = '#0284c7';
        toolGpsBoatBtn.style.color = '#ffffff';
    }

    if (!navigator.geolocation) {
        fallbackSimulatedGpsPosition();
        return;
    }

    gpsWatchId = navigator.geolocation.watchPosition(
        (pos) => {
            const lat = pos.coords.latitude;
            const lon = pos.coords.longitude;
            const heading = (pos.coords.heading !== null && !isNaN(pos.coords.heading)) ? pos.coords.heading : lastBoatHdg;
            const speedKnots = (pos.coords.speed !== null && !isNaN(pos.coords.speed)) ? (pos.coords.speed * 1.94384) : 0.0;
            updateBoatPosition(lon, lat, heading, speedKnots);
        },
        (err) => {
            console.warn('GPS Geolocation warning / fallback to waterbody center:', err.message);
            fallbackSimulatedGpsPosition();
        },
        { enableHighAccuracy: true, maximumAge: 1000, timeout: 6000 }
    );
}

function fallbackSimulatedGpsPosition() {
    const center = map.getCenter();
    updateBoatPosition(center.lng, center.lat, 60.0, 7.5);
}

function stopGpsChartplotter() {
    isGpsWatchActive = false;
    if (gpsWatchId !== null && navigator.geolocation) {
        navigator.geolocation.clearWatch(gpsWatchId);
        gpsWatchId = null;
    }
    if (toolGpsBoatBtn) {
        toolGpsBoatBtn.innerText = 'Картплоттер: GPS';
        toolGpsBoatBtn.style.background = '';
        toolGpsBoatBtn.style.color = '';
    }
    if (boatMarker) {
        boatMarker.remove();
        boatMarker = null;
    }
}

function updateBoatPosition(lon, lat, heading, sogKnots) {
    lastBoatCoords = [lon, lat];
    lastBoatHdg = heading || 0.0;
    lastBoatSog = sogKnots || 0.0;

    if (!boatMarker) {
        const el = document.createElement('div');
        el.className = 'boat-gps-marker';
        el.innerHTML = '<div class="boat-gps-pulse"></div><div class="boat-gps-marker-body"></div>';
        boatMarker = new maplibregl.Marker({ element: el })
            .setLngLat([lon, lat])
            .addTo(map);
    } else {
        boatMarker.setLngLat([lon, lat]);
    }

    const bodyEl = boatMarker.getElement().querySelector('.boat-gps-marker-body');
    if (bodyEl) {
        bodyEl.style.transform = `rotate(${lastBoatHdg}deg)`;
    }

    if (isLiveSonarActive) {
        queryAndRecordLivePoint(lat, lon, lastBoatSog, lastBoatHdg);
    }
}

if (toolGpsBoatBtn) {
    toolGpsBoatBtn.addEventListener('click', toggleGpsChartplotter);
}

// 19.5. Живая съемка эхолотом (Quickdraw / Genesis Live)
let isLiveSonarActive = false;
let liveSonarTimer = null;
const btnToggleLiveSonar = document.getElementById('btn-toggle-live-sonar');
const btnSimulateSounderPing = document.getElementById('btn-simulate-sounder-ping');
const liveSonarHud = document.getElementById('live-sonar-hud');
const livePtsCount = document.getElementById('live-pts-count');
const liveLastDepth = document.getElementById('live-last-depth');

function toggleLiveSonar() {
    isLiveSonarActive = !isLiveSonarActive;
    if (isLiveSonarActive) {
        if (btnToggleLiveSonar) {
            btnToggleLiveSonar.innerText = 'Запись эхолота: ВКЛ';
            btnToggleLiveSonar.classList.add('active');
        }
        if (liveSonarHud) liveSonarHud.style.display = 'block';

        if (!isGpsWatchActive) {
            startGpsChartplotter();
        }

        liveSonarTimer = setInterval(() => {
            if (lastBoatCoords) {
                const dLon = (Math.random() - 0.5) * 0.0003;
                const dLat = (Math.random() - 0.5) * 0.0003;
                const newLon = lastBoatCoords[0] + dLon;
                const newLat = lastBoatCoords[1] + dLat;
                updateBoatPosition(newLon, newLat, lastBoatHdg, lastBoatSog);
            }
        }, 3000);
    } else {
        if (btnToggleLiveSonar) {
            btnToggleLiveSonar.innerText = 'Запись эхолота: Выкл';
            btnToggleLiveSonar.classList.remove('active');
        }
        if (liveSonarTimer) {
            clearInterval(liveSonarTimer);
            liveSonarTimer = null;
        }
    }
}

function queryAndRecordLivePoint(lat, lon, sog, cog) {
    fetch(`/api/depth?lon=${lon.toFixed(5)}&lat=${lat.toFixed(5)}`)
        .then(r => r.json())
        .then(d => {
            const measuredDepth = (d && d.depth > 0.0) ? d.depth : (3.5 + Math.random() * 6.0);
            sendLiveTelemetryPoint(lat, lon, measuredDepth, sog, cog);
        })
        .catch(() => {
            sendLiveTelemetryPoint(lat, lon, 5.0, sog, cog);
        });
}

function sendLiveTelemetryPoint(lat, lon, depthM, sog, cog) {
    fetch('/api/telemetry/live-point', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            lat: lat,
            lon: lon,
            depth_m: depthM,
            sog_knots: sog || 0.0,
            cog_deg: cog || 0.0
        })
    })
    .then(r => r.json())
    .then(data => {
        if (livePtsCount) livePtsCount.innerText = data.total_track_points;
        if (liveLastDepth && depthM) liveLastDepth.innerText = formatDepthVal(depthM);
        refreshLiveTrackOnMap();
    })
    .catch(err => console.warn('Ошибка отправки промера эхолота:', err));
}

function refreshLiveTrackOnMap() {
    fetch('/api/telemetry/live-track')
        .then(r => r.json())
        .then(geo => {
            if (map.getSource('live-telemetry-track')) {
                map.getSource('live-telemetry-track').setData(geo);
            }
        })
        .catch(() => {});
}

if (btnToggleLiveSonar) {
    btnToggleLiveSonar.addEventListener('click', toggleLiveSonar);
}

if (btnSimulateSounderPing) {
    btnSimulateSounderPing.addEventListener('click', () => {
        const center = map.getCenter();
        const lat = center.lat;
        const lon = center.lng;
        queryAndRecordLivePoint(lat, lon, 6.0, 90.0);
    });
}
