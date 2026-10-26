"""
SeaPath Navigation Engine - API Gateway
=======================================
FastAPI service providing bathymetric passage planning, fairway optimization,
and real-time under-keel clearance (UKC) analysis.
"""

import os
import time
from collections import deque, OrderedDict
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Set
import json
import uuid

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict

from backend.core.grid import BathymetricGrid
from backend.core.router import Router
from backend.core.fairways import rasterize_fairway_weights, load_fairways_geojson
from backend.core.export import export_to_gpx, export_to_rtz
from backend.core.safety import generate_safety_corridor_polygon, verify_ecdis_route_safety, compute_route_segment_xtds
from backend.core.dukc import compute_dukc_budget, calculate_tidal_window, compute_search_budget, calculate_turning_heel
from backend.core.bottleneck import analyze_route_bottlenecks
from backend.core.kinematics import (
    generate_turn_arcs_feature_collection,
    calculate_bearing_deg,
    calculate_distance_m,
    calculate_turn_arc_geometry,
    construct_filleted_path,
    splice_turn_arcs_into_route,
    point_to_polyline_distance_m,
    point_to_segment_distance_m,
)
from backend.core.sonar import (
    parse_sonar_payload,
    interpolate_sonar_bathymetry,
    fuse_soundings_into_grid,
)
from backend.core.passage_plan import generate_passage_plan
from backend.core.waterbodies import WaterbodyManager, Waterbody
from backend.core.nmea_receiver import NmeaManager, VesselLiveState
import urllib.request
import urllib.parse
import math
import asyncio
from contextlib import asynccontextmanager

nmea_mgr = NmeaManager()
connected_websockets: Set[WebSocket] = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan: starts background NMEA 0183 UDP receiver on marine port (10110)
    and manages real-time telemetry streaming to connected clients.
    """
    udp_port = int(os.environ.get("SEAPATH_NMEA_PORT", "10110"))
    udp_host = os.environ.get("SEAPATH_NMEA_HOST", "0.0.0.0")

    def on_vessel_telemetry(vessel: VesselLiveState):
        if vessel.lat is not None and vessel.lon is not None and vessel.gps_valid:
            try:
                waterbody_mgr.log_live_telemetry(
                    lat=vessel.lat,
                    lon=vessel.lon,
                    depth_m=vessel.depth_m if vessel.depth_valid else None,
                    sog_knots=vessel.sog_knots,
                    cog_deg=vessel.cog_deg
                )
            except Exception:
                pass

    nmea_mgr.receiver.add_listener(on_vessel_telemetry)
    await nmea_mgr.start_udp_listener(host=udp_host, port=udp_port)

    async def telemetry_broadcaster():
        while True:
            try:
                await asyncio.sleep(0.5)
                if connected_websockets:
                    state_dict = nmea_mgr.receiver.state.to_dict()
                    msg = json.dumps({"type": "vessel_telemetry", "data": state_dict})
                    for ws in list(connected_websockets):
                        try:
                            await ws.send_text(msg)
                        except Exception:
                            connected_websockets.discard(ws)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    broadcast_task = asyncio.create_task(telemetry_broadcaster())

    yield

    broadcast_task.cancel()
    await nmea_mgr.stop()

app = FastAPI(
    title="SeaPath ECDIS Engine",
    description="Maritime routing and bathymetric passage planning engine",
    version="1.0.0",
    lifespan=lifespan,
)

@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle validation errors, ensuring non-finite numbers (NaN, Inf) and exception objects
    in error details do not crash the JSON serializer when returning 422 Unprocessable Entity.
    """
    sanitized_errors = []
    for err in exc.errors():
        err_dict = {}
        for k, v in err.items():
            if k == "ctx" and isinstance(v, dict):
                err_dict[k] = {ck: str(cv) if isinstance(cv, (Exception, type)) else cv for ck, cv in v.items()}
            elif k == "input":
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    err_dict[k] = str(v)
                elif isinstance(v, dict):
                    err_dict[k] = {
                        dk: str(dv) if isinstance(dv, float) and (math.isnan(dv) or math.isinf(dv)) else dv
                        for dk, dv in v.items()
                    }
                else:
                    err_dict[k] = v
            else:
                err_dict[k] = v
        sanitized_errors.append(err_dict)
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_errors},
    )



# ---------------------------------------------------------------------------
# Development middleware: Prevent client-side caching of local static assets
# ---------------------------------------------------------------------------
import os

@app.middleware("http")
async def no_cache_header_middleware(request: Request, call_next):
    response = await call_next(request)
    static_prefixes = ("/static", "/app.js", "/style.css", "/favicon.ico", "/heatmap.png")
    if request.url.path in ("/", "/index.html") or request.url.path.startswith(static_prefixes):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

# ---------------------------------------------------------------------------
# Bathymetric Data Initialization
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR_OVERRIDE = os.environ.get("SEAPATH_DATA_DIR")
_RAW_DATA_DIR = Path(_DATA_DIR_OVERRIDE) if _DATA_DIR_OVERRIDE else (_BASE_DIR / "data" / "raw")
_GEOTIFF_PATH_ENV = os.environ.get("SEAPATH_GEOTIFF_PATH")
_IS_PRODUCTION = os.environ.get("SEAPATH_ENV", "").strip().lower() == "production"

if _IS_PRODUCTION:
    # Production Fail-Closed Policy:
    # 1. Automatic fallback to lightweight test sample (operational_sample.tif) is STRICTLY PROHIBITED.
    # 2. If SEAPATH_GEOTIFF_PATH is set explicitly to operational_sample.tif, reject it as test-only data.
    # 3. Requires full operational dataset E8_2024.tif or a dedicated verified production raster.
    if _GEOTIFF_PATH_ENV:
        candidate_path = Path(_GEOTIFF_PATH_ENV)
        if candidate_path.name == "operational_sample.tif":
            raise RuntimeError(
                "[PRODUCTION FAILURE] Critical: 'operational_sample.tif' is a lightweight CI/test sample "
                "and is strictly prohibited in production environment. Full regional bathymetry (e.g. E8_2024.tif) is required."
            )
        _GEOTIFF_PATH = candidate_path
    else:
        _GEOTIFF_PATH = _RAW_DATA_DIR / "E8_2024.tif"
else:
    # Development / Test / CI environment
    if _GEOTIFF_PATH_ENV:
        _GEOTIFF_PATH = Path(_GEOTIFF_PATH_ENV)
    elif (_RAW_DATA_DIR / "E8_2024.tif").exists():
        _GEOTIFF_PATH = _RAW_DATA_DIR / "E8_2024.tif"
    elif (_RAW_DATA_DIR / "operational_sample.tif").exists():
        _GEOTIFF_PATH = _RAW_DATA_DIR / "operational_sample.tif"
    else:
        _GEOTIFF_PATH = _RAW_DATA_DIR / "E8_2024.tif"

is_synthetic_mode = False
if _GEOTIFF_PATH.exists():
    try:
        print(f"Loading primary bathymetric grid: {_GEOTIFF_PATH.name}...")
        full_grid = BathymetricGrid.load_from_geotiff(_GEOTIFF_PATH)
        is_synthetic_mode = False
    except Exception as _load_err:
        if _IS_PRODUCTION:
            raise RuntimeError(
                f"[PRODUCTION FAILURE] Critical: failed to load operational bathymetry from {_GEOTIFF_PATH}: {_load_err}"
            )
        print(f"[WARN] Failed to load GeoTIFF ({_load_err}). Falling back to synthetic regional bathymetry.")
        full_grid = BathymetricGrid.create_synthetic()
        is_synthetic_mode = True
else:
    if _IS_PRODUCTION:
        raise RuntimeError(
            f"[PRODUCTION FAILURE] Operational GeoTIFF dataset {_GEOTIFF_PATH} is required in production environment."
        )
    print(
        f"[DEMO MODE] {_GEOTIFF_PATH.name} not found in data/raw/. "
        "Initialized synthetic regional bathymetry grid for demonstration. "
        "Place operational EMODnet GeoTIFF in data/raw/ for real-world navigation."
    )
    full_grid = BathymetricGrid.create_synthetic()
    is_synthetic_mode = True

full_grid.is_synthetic = is_synthetic_mode

if _IS_PRODUCTION:
    # 1. Physical dataset matrix dimensions check (minimum 1000x1000 cells)
    if full_grid.cols < 1000 or full_grid.rows < 1000 or (full_grid.cols * full_grid.rows < 2_000_000):
        raise RuntimeError(
            f"[PRODUCTION FAILURE] Loaded bathymetric raster '{_GEOTIFF_PATH.name}' dimensions "
            f"({full_grid.cols}x{full_grid.rows}, {full_grid.cols * full_grid.rows} cells) are insufficient for operational navigation. "
            f"Full regional bathymetry (minimum 1000x1000 cells) is required in production."
        )

    # 2. Geographic coverage (Bounding Box) validation for regional navigation domain
    lat_span = abs(full_grid.lat_max - full_grid.lat_min)
    lon_span = abs(full_grid.lon_max - full_grid.lon_min)
    if lat_span < 3.0 or lon_span < 8.0:
        raise RuntimeError(
            f"[PRODUCTION FAILURE] Loaded bathymetric raster '{_GEOTIFF_PATH.name}' geographic span "
            f"(dLat={lat_span:.2f}°, dLon={lon_span:.2f}°) is insufficient for operational regional navigation. "
            f"Expected regional coverage: dLat >= 3.0°, dLon >= 8.0°."
        )

    # 3. Spatial resolution validation: cell step must not exceed 0.003 degrees (~300m)
    d_lat_cell = lat_span / max(1, full_grid.rows)
    d_lon_cell = lon_span / max(1, full_grid.cols)
    if d_lat_cell > 0.003 or d_lon_cell > 0.003:
        raise RuntimeError(
            f"[PRODUCTION FAILURE] Loaded bathymetric raster '{_GEOTIFF_PATH.name}' resolution "
            f"({d_lat_cell:.5f}° x {d_lon_cell:.5f}°) is too coarse for operational passage planning. "
            f"High-resolution bathymetry (<= 0.003° per cell) is required in production."
        )


# 1. Optional local high-resolution soundings fusion (before downsampling)
_LOCAL_SURVEY_CSV = _RAW_DATA_DIR / "yevpatoriya_channel.csv"
if _LOCAL_SURVEY_CSV.exists():
    full_grid.fuse_csv_data(_LOCAL_SURVEY_CSV)

# 2. Downsample for routing AFTER soundings fusion
if not is_synthetic_mode and _GEOTIFF_PATH.exists():
    _downsample_factor = 10 if full_grid.rows >= 2000 else 1
    routing_grid = full_grid.downsample(factor=_downsample_factor) if _downsample_factor > 1 else full_grid
else:
    routing_grid = full_grid
routing_grid.is_synthetic = is_synthetic_mode

# Rasterize navigational fairways & restricted areas (RESARE) for full resolution grid
full_fairway_weights = rasterize_fairway_weights(
    full_grid.rows, full_grid.cols,
    full_grid.lat_min, full_grid.lat_max,
    full_grid.lon_min, full_grid.lon_max
)
full_grid.attach_fairways(full_fairway_weights)

# Rasterize navigational fairways for routing resolution grid
if routing_grid is full_grid:
    fairway_weights = full_fairway_weights
else:
    fairway_weights = rasterize_fairway_weights(
        routing_grid.rows, routing_grid.cols,
        routing_grid.lat_min, routing_grid.lat_max,
        routing_grid.lon_min, routing_grid.lon_max
    )
    routing_grid.attach_fairways(fairway_weights)

print(f"Grid active: Full resolution {full_grid.rows}x{full_grid.cols} | Routing {routing_grid.rows}x{routing_grid.cols}")

try:
    import seapath_native
    native_grid = seapath_native.NativeGrid(
        routing_grid.rows, routing_grid.cols,
        routing_grid.lat_min, routing_grid.lat_max,
        routing_grid.lon_min, routing_grid.lon_max,
        routing_grid.depths,
        fairway_weights
    )
    router = seapath_native.NativeRouter(native_grid)
    _HAS_NATIVE = True
    print("[ROUTER] Activated high-performance C++ native navigation core (seapath_native)")
except Exception as _err:
    router = Router(routing_grid)
    _HAS_NATIVE = False
    print(f"[ROUTER] Running Python reference router ({_err})")

waterbody_mgr = WaterbodyManager(full_grid)

def activate_waterbody_instance(wb_id: str) -> Optional[Waterbody]:
    """
    Activates the specified water body (sea, bay, river, lake, or custom survey).
    Synchronously updates full_grid, routing_grid, and router.
    """
    global full_grid, routing_grid, router, _HAS_NATIVE
    wb = waterbody_mgr.set_active(wb_id)
    if not wb:
        return None
    full_grid = wb.grid
    routing_grid = wb.grid
    try:
        import seapath_native
        native_grid = seapath_native.NativeGrid(
            routing_grid.rows, routing_grid.cols,
            routing_grid.lat_min, routing_grid.lat_max,
            routing_grid.lon_min, routing_grid.lon_max,
            routing_grid.depths,
            routing_grid.fairway_weights
        )
        router = seapath_native.NativeRouter(native_grid)
        _HAS_NATIVE = True
    except Exception:
        router = wb.router
        _HAS_NATIVE = False
    return wb

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ActivateWaterbodyRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    waterbody_id: str = Field(..., description="Unique ID of water body to activate")


class CreateWaterbodyRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    name: str = Field(..., min_length=2, max_length=100, description="Name of bay, river, or lake")
    category: Optional[str] = Field(default="lake", description="Category: bay, river, lake, reservoir, custom")
    bbox: List[float] = Field(..., min_length=4, max_length=4, description="[min_lon, min_lat, max_lon, max_lat]")
    soundings: Optional[List[Dict[str, float]]] = Field(default=None, description="Optional sounding measurements")


class LiveTelemetryRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Vessel GPS latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Vessel GPS longitude")
    depth_m: Optional[float] = Field(default=None, ge=0.0, le=11000.0, description="Live sounder depth reading in meters")
    sog_knots: Optional[float] = Field(default=0.0, ge=0.0, le=100.0, description="Speed over ground in knots")
    cog_deg: Optional[float] = Field(default=0.0, ge=0.0, le=360.0, description="Course over ground in degrees")


class SonarUploadRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    raw_content: Optional[str] = Field(default=None, description="Raw text content of sounder file (CSV, NMEA, or GPX)")
    filename: Optional[str] = Field(default="sounder_log.csv", description="Original filename for parser format detection")
    is_dredged: Optional[bool] = Field(default=False, description="Flag if soundings represent a dredged channel across land")
    grid_size: Optional[int] = Field(default=70, ge=20, le=200, description="Interpolated mesh dimensions (NxN)")
    power: Optional[float] = Field(default=2.0, ge=1.0, le=4.0, description="IDW distance weighting exponent")
    search_radius_m: Optional[float] = Field(default=1200.0, ge=50.0, le=10000.0, description="Spatial neighborhood radius in meters")


class PassagePlanRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    route_data: Dict[str, Any] = Field(description="Complete calculated route payload from /api/route")
    vessel_profile: Optional[str] = Field(default="yacht", description="Vessel class profile (yacht, ferry, cargo, custom)")
    vessel_name: Optional[str] = Field(default="M/V SeaPath", description="Vessel ship name")
    draft: Optional[float] = Field(default=1.2, ge=0.1, le=25.0)
    speed_knots: Optional[float] = Field(default=10.0, ge=0.0, le=50.0)
    ukc: Optional[float] = Field(default=0.5, ge=0.0, le=10.0)
    wave_height_m: Optional[float] = Field(default=0.0, ge=0.0, le=15.0)
    tide_offset_m: Optional[float] = Field(default=0.0, ge=-5.0, le=5.0)
    xtd_m: Optional[float] = Field(default=185.2, ge=20.0, le=2000.0)
    fuel_burn_l_h: Optional[float] = Field(default=None, ge=0.0, le=50000.0)


class RouteRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    start_lon: Optional[float] = Field(default=None, description="Departure longitude in decimal degrees")
    start_lat: Optional[float] = Field(default=None, description="Departure latitude in decimal degrees")
    goal_lon: Optional[float] = Field(default=None, description="Destination longitude in decimal degrees")
    goal_lat: Optional[float] = Field(default=None, description="Destination latitude in decimal degrees")
    waypoints: Optional[List[List[float]]] = Field(default=None, description="List of [lon, lat] coordinates for multi-waypoint passage plan")
    draft: float = Field(..., gt=0.0, description="Static ship draft in meters")
    speed_knots: float = Field(..., ge=0.0, description="Planned vessel speed in knots")
    ukc: float = Field(..., ge=0.0, description="Required under-keel clearance margin in meters")
    turning_radius_m: float = Field(default=150.0, ge=10.0, description="Minimum vessel turning radius in meters")
    fairway_preference: float = Field(default=1.0, ge=0.0, le=1.0, description="Navigational fairway attraction factor (0.0=neutral, 1.0=prioritize fairways)")
    block_coefficient: float = Field(default=0.65, ge=0.3, le=0.95, description="Hull block coefficient Cb")
    port_xtd_m: float = Field(default=185.2, ge=20.0, le=1852.0, description="Port cross-track limit in meters (XTD)")
    stbd_xtd_m: float = Field(default=185.2, ge=20.0, le=1852.0, description="Starboard cross-track limit in meters (XTD)")
    tide_offset_m: float = Field(default=0.0, ge=-2.0, le=4.0, description="Dynamic water level or tide offset in meters")
    wave_height_m: float = Field(default=0.0, ge=0.0, le=5.0, description="Significant wave height Hs in meters")
    gm_m: float = Field(default=1.5, ge=0.5, le=4.0, description="Transverse metacentric height GM in meters")
    # -- Параметры единого бюджета поиска (см. dukc.compute_search_budget) --
    beam_m: Optional[float] = Field(default=None, description="Vessel beam in meters (None = estimate 2.5*draft)")
    exposed_waters: bool = Field(default=False, description="Exposed coastal waters flag for wave allowance (PIANC 0.50*Hs vs 0.35*Hs)")
    strict_dukc: bool = Field(default=True, description="Use full PIANC search budget (squat+heel+wave) instead of legacy squat-only")

    @field_validator("waypoints")
    @classmethod
    def validate_waypoints(cls, v: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        if v is not None:
            if len(v) < 2:
                raise ValueError("Waypoints list must contain at least 2 coordinate points.")
            for idx, pt in enumerate(v):
                if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                    raise ValueError(f"Waypoint {idx + 1} must be an exact [lon, lat] coordinate pair.")
                for coord in pt:
                    if not math.isfinite(float(coord)):
                        raise ValueError(f"Waypoint {idx + 1} coordinate must be finite.")
        return v


class ExportWaypoint(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    lat: Optional[float] = None
    latitude: Optional[float] = None
    lon: Optional[float] = None
    lng: Optional[float] = None
    longitude: Optional[float] = None
    turn_radius_m: Optional[float] = Field(default=None, ge=0.0, le=2000.0)
    turn_angle_deg: Optional[float] = Field(default=None, ge=0.0, le=180.0)
    maneuver_feasible: Optional[bool] = Field(default=True)
    rot_deg_min: Optional[float] = Field(default=0.0)
    wop_distance_m: Optional[float] = Field(default=0.0)
    leg_bearing_deg: Optional[float] = Field(default=0.0)
    leg_distance_nm: Optional[float] = Field(default=0.0)
    port_xtd_nm: Optional[float] = Field(default=0.10, ge=0.001)
    stbd_xtd_nm: Optional[float] = Field(default=0.10, ge=0.001)
    depth_m: Optional[float] = None
    clearance_m: Optional[float] = None
    depth_at_waypoint: Optional[float] = None
    depth: Optional[float] = None
    clearance: Optional[float] = None
    heel_sinkage_m: Optional[float] = Field(default=0.0)
    heel_angle_deg: Optional[float] = Field(default=0.0)
    snap_offset_m: Optional[float] = None
    snap_reason: Optional[str] = None
    original_coords: Optional[List[float]] = None

    def get_lat(self) -> float:
        v = self.lat if self.lat is not None else self.latitude
        if v is None:
            raise ValueError("Waypoint latitude is required.")
        fv = float(v)
        if not math.isfinite(fv):
            raise ValueError("Waypoint latitude must be a finite number.")
        return fv

    def get_lon(self) -> float:
        v = self.lon if self.lon is not None else (self.lng if self.lng is not None else self.longitude)
        if v is None:
            raise ValueError("Waypoint longitude is required.")
        fv = float(v)
        if not math.isfinite(fv):
            raise ValueError("Waypoint longitude must be a finite number.")
        return fv

    @field_validator("latitude", "lat")
    @classmethod
    def check_lat(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if not math.isfinite(v):
                raise ValueError("Latitude must be a finite number.")
            if not (-90.0 <= v <= 90.0):
                raise ValueError("Latitude out of valid geographic range [-90, 90].")
        return v

    @field_validator("longitude", "lng", "lon")
    @classmethod
    def check_lon(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if not math.isfinite(v):
                raise ValueError("Longitude must be a finite number.")
            if not (-180.0 <= v <= 180.0):
                raise ValueError("Longitude out of valid geographic range [-180, 180].")
        return v


class ExportRouteRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    plan_id: Optional[str] = None
    waypoints: Optional[List[ExportWaypoint]] = None
    route: List[List[float]] = Field(default_factory=list)
    draft: float = Field(default=1.2, gt=0.0)
    speed_knots: float = Field(default=10.0, ge=0.0)
    ukc: float = Field(default=0.5, ge=0.0)
    safety_contour: Optional[float] = None
    route_name: Optional[str] = "SeaPath_Passage_Plan"
    safety_status: Optional[str] = None
    is_safe: Optional[bool] = None
    allow_unverified: bool = Field(default=False, description="Explicit flag allowing export of unverified or hazardous routes with mandatory warning annotations")
    block_coefficient: float = Field(default=0.65, ge=0.3, le=0.95)
    tide_offset_m: float = Field(default=0.0, ge=-5.0, le=5.0)
    wave_height_m: float = Field(default=0.0, ge=0.0, le=10.0)
    turning_radius_m: float = Field(default=150.0, ge=10.0, le=2000.0)
    beam_m: Optional[float] = None
    gm_m: Optional[float] = None
    exposed_waters: bool = False
    strict_dukc: bool = True

    @field_validator("safety_contour", "draft", "speed_knots", "ukc", "block_coefficient", "tide_offset_m", "wave_height_m", "turning_radius_m", mode="before")
    @classmethod
    def validate_finite_numbers(cls, v: Any) -> Any:
        if v is not None:
            try:
                fv = float(v)
                if not math.isfinite(fv):
                    raise ValueError("Numeric values must be finite (NaN and Inf are forbidden).")
                return fv
            except (TypeError, ValueError) as err:
                raise ValueError(f"Invalid numeric value: {err}")
        return v

    @model_validator(mode="after")
    def validate_plan_or_waypoints(self):
        if not self.plan_id:
            if not self.waypoints or len(self.waypoints) < 2:
                raise ValueError("Passage plan must contain at least 2 waypoints or a valid plan_id.")
            for idx, wp in enumerate(self.waypoints):
                try:
                    wp.get_lat()
                    wp.get_lon()
                except ValueError as err:
                    raise ValueError(f"Waypoint {idx + 1}: {err}")
        return self


# ---------------------------------------------------------------------------
# Spatial Utilities
# ---------------------------------------------------------------------------
def _snap_to_navigable(
    r: int,
    c: int,
    grid: BathymetricGrid,
    min_depth: float,
    max_snap_meters: float = 1500.0,
    orig_lon: Optional[float] = None,
    orig_lat: Optional[float] = None,
) -> Tuple[Optional[int], Optional[int], float, Optional[str]]:
    """
    Breadth-first search to snap shoreline/berth coordinates to the nearest
    navigable node satisfying safety depth constraints (depth > min_depth)
    and bypassing restricted zones.
    Strictly prevents leaping across land barriers (islands/peninsulas) or prohibited zones.
    Measures exact distance from user's clicked (orig_lon, orig_lat).
    Returns (snapped_r, snapped_c, snap_distance_m, snap_reason).
    """
    if not (0 <= r < grid.rows and 0 <= c < grid.cols):
        return None, None, 0.0, "OUT_OF_BOUNDS"

    is_restr = grid.is_restricted(r, c)
    is_nodata = bool(grid.nodata_mask[r, c]) if getattr(grid, "nodata_mask", None) is not None else False
    cur_d = float(grid.depths[r, c])
    is_land = (cur_d <= 0.0 and not is_nodata) or (getattr(grid, "land_mask", None) is not None and bool(grid.land_mask[r, c]))

    if cur_d > min_depth and not is_restr and not is_land and not is_nodata:
        return r, c, 0.0, None

    if is_restr:
        reason = "RESTRICTED_ZONE"
    elif is_land:
        reason = "LAND_SHORELINE"
    elif is_nodata:
        reason = "NODATA_UNVERIFIED"
    else:
        reason = "INSUFFICIENT_DEPTH"

    lat_cell, lon_cell = grid.get_cell_coords(r, c)
    lat_user = orig_lat if orig_lat is not None else lat_cell
    lon_user = orig_lon if orig_lon is not None else lon_cell

    init_is_water = (cur_d > 0.0 and not is_land and not is_restr and not is_nodata)
    queue = deque([(r, c, 0, init_is_water)])
    visited = {(r, c)}
    max_steps = 15  # Up to 15 grid cells (~1500m)

    # Pre-check for interior (land, restricted, or nodata):
    # If starting on land, restricted zone, or nodata, check if any immediate 8-neighbor is genuine water.
    # If start has NO immediate water neighbor, point is deep in the interior.
    if is_land or is_restr or is_nodata:
        has_immediate_water = False
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = r + dr, c + dc
            if 0 <= nr < grid.rows and 0 <= nc < grid.cols:
                nd = float(grid.depths[nr, nc])
                nnd = bool(grid.nodata_mask[nr, nc]) if getattr(grid, "nodata_mask", None) is not None else False
                nl = (nd <= 0.0 and not nnd) or (getattr(grid, "land_mask", None) is not None and bool(grid.land_mask[nr, nc]))
                nrst = grid.is_restricted(nr, nc)
                if nd > 0.0 and not nl and not nrst and not nnd:
                    has_immediate_water = True
                    break
        if not has_immediate_water:
            if is_land:
                return None, None, 0.0, "LAND_INTERIOR"
            elif is_restr:
                return None, None, 0.0, "RESTRICTED_ZONE_INTERIOR"
            else:
                return None, None, 0.0, "NODATA_INTERIOR"

    while queue:
        cr, cc, steps, entered_water = queue.popleft()
        c_depth = float(grid.depths[cr, cc])
        c_nodata = bool(grid.nodata_mask[cr, cc]) if getattr(grid, "nodata_mask", None) is not None else False
        c_land = (c_depth <= 0.0 and not c_nodata) or (getattr(grid, "land_mask", None) is not None and bool(grid.land_mask[cr, cc]))
        c_restr = grid.is_restricted(cr, cc)

        # Candidate check: must satisfy required depth and not be restricted or land/nodata
        if c_depth > min_depth and not c_restr and not c_land and not c_nodata:
            lat_c, lon_c = grid.get_cell_coords(cr, cc)
            dist_m = calculate_distance_m(lat_user, lon_user, lat_c, lon_c)
            if dist_m <= max_snap_meters:
                # Obstacle barrier check: line-of-sight must not cut across dry land, restricted, or nodata once water entered
                line_cells = grid.sample_depth_along_segment(r, c, cr, cc)
                water_seen = False
                hopped_barrier = False
                for d_val in line_cells:
                    if d_val > 0.0:
                        water_seen = True
                    elif water_seen and d_val <= 0.0:
                        hopped_barrier = True
                        break
                if not hopped_barrier:
                    return cr, cc, round(dist_m, 1), reason

        if steps >= max_steps:
            continue

        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < grid.rows and 0 <= nc < grid.cols and (nr, nc) not in visited:
                n_depth = float(grid.depths[nr, nc])
                n_nodata = bool(grid.nodata_mask[nr, nc]) if getattr(grid, "nodata_mask", None) is not None else False
                n_land = (n_depth <= 0.0 and not n_nodata) or (getattr(grid, "land_mask", None) is not None and bool(grid.land_mask[nr, nc]))
                n_restr = grid.is_restricted(nr, nc)
                n_is_water = (n_depth > 0.0 and not n_land and not n_restr and not n_nodata)

                # Strict barrier invariants:
                # 1. If starting on land, restricted, or nodata, cannot step to another obstacle cell!
                # Only 1 step directly to genuine water is permitted:
                if (is_land or is_restr or is_nodata) and not entered_water and not n_is_water:
                    continue
                # 2. Once water is entered, strictly forbidden from crossing back onto land, restricted, or nodata!
                if entered_water and not n_is_water:
                    continue

                visited.add((nr, nc))
                queue.append((nr, nc, steps + 1, entered_water or n_is_water))

    return None, None, 0.0, reason



def _is_within_coverage(lon: float, lat: float, grid: BathymetricGrid) -> bool:
    return (grid.lon_min <= lon <= grid.lon_max) and (grid.lat_min <= lat <= grid.lat_max)


def _attach_coordinates_to_profile(profile: List[Dict[str, Any]], route_coords: List[List[float]]) -> List[Dict[str, Any]]:
    """Interpolates exact geographic (lon, lat) positions for all bathymetric profile soundings."""
    if not profile or not route_coords:
        return profile

    route_dists = [0.0]
    for i in range(1, len(route_coords)):
        d = calculate_distance_m(route_coords[i - 1][1], route_coords[i - 1][0], route_coords[i][1], route_coords[i][0])
        route_dists.append(route_dists[-1] + d)

    seg_idx = 0
    num_segs = len(route_coords) - 1

    for pt in profile:
        s = pt.get("distance_from_start_m", 0.0)
        while seg_idx < num_segs - 1 and route_dists[seg_idx + 1] < s:
            seg_idx += 1
        d0 = route_dists[seg_idx]
        d1 = route_dists[seg_idx + 1] if seg_idx + 1 < len(route_dists) else d0
        span = max(1e-3, d1 - d0)
        fraction = max(0.0, min(1.0, (s - d0) / span))

        p0 = route_coords[seg_idx]
        p1 = route_coords[min(seg_idx + 1, len(route_coords) - 1)]
        pt["lon"] = round(p0[0] + (p1[0] - p0[0]) * fraction, 6)
        pt["lat"] = round(p0[1] + (p1[1] - p0[1]) * fraction, 6)

    return profile


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health_check(response: Response):
    is_prod = _IS_PRODUCTION
    data_status = "operational" if not is_synthetic_mode else "synthetic_demo"
    if is_prod and is_synthetic_mode:
        response.status_code = 503
        return {
            "status": "unhealthy",
            "environment": "production",
            "data_status": data_status,
            "is_synthetic": True,
            "error": "Operational GeoTIFF dataset not loaded in production environment",
            "dataset_rows": routing_grid.rows,
            "dataset_cols": routing_grid.cols,
            "bounds": [routing_grid.lon_min, routing_grid.lat_min, routing_grid.lon_max, routing_grid.lat_max],
        }
    return {
        "status": "healthy",
        "environment": "production" if is_prod else "development",
        "data_status": data_status,
        "dataset_rows": routing_grid.rows,
        "dataset_cols": routing_grid.cols,
        "is_synthetic": is_synthetic_mode,
        "bounds": [routing_grid.lon_min, routing_grid.lat_min, routing_grid.lon_max, routing_grid.lat_max],
    }


@app.get("/api/grid")
def get_grid_coverage():
    """Returns GeoJSON boundary polygon representing available bathymetry coverage."""
    return routing_grid.to_geojson()


@app.get("/api/fairways")
def get_fairways():
    """Returns vector fairways, TSS corridors, and navigational restriction polygons in GeoJSON format."""
    return load_fairways_geojson()


@app.get("/api/cartography/contours")
def get_cartography_contours():
    """Returns vector isobaths in GeoJSON format for nautical charts."""
    contours_path = _FRONTEND_DIR / "contours.geojson"
    if contours_path.exists():
        return FileResponse(contours_path, media_type="application/geo+json")
    return {"type": "FeatureCollection", "features": []}


@app.get("/api/cartography/soundings")
def get_cartography_soundings():
    """Returns nautical spot soundings in GeoJSON format."""
    soundings_path = _FRONTEND_DIR / "soundings.geojson"
    if soundings_path.exists():
        return FileResponse(soundings_path, media_type="application/geo+json")
    return {"type": "FeatureCollection", "features": []}


@app.get("/api/ports")
def get_ports():
    """Returns database of maritime and inland ports, marinas, and safe approach coordinates."""
    active_wb = waterbody_mgr.get_active()
    if active_wb and active_wb.ports:
        return active_wb.ports
    if active_wb and active_wb.id == "black_sea":
        ports_path = _BASE_DIR / "data" / "ports.json"
        if ports_path.exists():
            with open(ports_path, "r", encoding="utf-8") as f:
                return json.load(f)
    return []


@app.get("/api/anchorages")
def get_anchorages():
    """Returns database of official maritime and inland anchorages, holding grounds, and sheltered roadsteads."""
    active_wb = waterbody_mgr.get_active()
    if active_wb and active_wb.anchorages:
        return active_wb.anchorages
    if active_wb and active_wb.id == "black_sea":
        anc_path = _BASE_DIR / "data" / "anchorages.json"
        if anc_path.exists():
            with open(anc_path, "r", encoding="utf-8") as f:
                return json.load(f)
    return []


@app.get("/api/depth")
def get_depth_at(lon: float, lat: float):
    """Returns exact seabed sounding depth in meters at the given geographic coordinate."""
    if not _is_within_coverage(lon, lat, full_grid):
        return {"depth": None, "in_bounds": False, "is_land": False, "is_nodata": False, "is_restricted": False}
    r, c = full_grid.lonlat_to_cell(lon, lat)
    d = float(full_grid.depths[r, c])
    is_nodata = bool(full_grid.nodata_mask[r, c]) if full_grid.nodata_mask is not None else False
    is_land = bool(full_grid.land_mask[r, c]) if full_grid.land_mask is not None else (d <= 0.05 and not is_nodata)
    is_restr = False
    if routing_grid:
        rr, rc = routing_grid.lonlat_to_cell(lon, lat)
        is_restr = routing_grid.is_restricted(rr, rc)
    return {
        "depth": round(d, 1) if not is_land and not is_nodata else 0.0,
        "in_bounds": True,
        "is_land": is_land,
        "is_nodata": is_nodata,
        "is_restricted": is_restr,
    }


# ---------------------------------------------------------------------------
# Server-Side Passage Plan Registry (Cache for export validation)
# ---------------------------------------------------------------------------
_CACHE_DIR_ENV = os.getenv("SEAPATH_CACHE_DIR")
if _CACHE_DIR_ENV:
    _candidate_cache = Path(_CACHE_DIR_ENV)
else:
    _candidate_cache = _BASE_DIR / ".cache" / "plans"

try:
    _candidate_cache.mkdir(parents=True, exist_ok=True)
    _test_file = _candidate_cache / ".write_test"
    _test_file.touch()
    _test_file.unlink(missing_ok=True)
    _PLANS_CACHE_DIR = _candidate_cache
except Exception:
    import tempfile
    _PLANS_CACHE_DIR = Path(tempfile.gettempdir()) / "seapath_cache" / "plans"
    _PLANS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_CACHE_TTL_HOURS = 24.0
_DEFAULT_MAX_DISK_PLANS = 500

try:
    SEAPATH_PLAN_CACHE_TTL_SECONDS = float(os.getenv("SEAPATH_PLAN_CACHE_TTL_HOURS", str(_DEFAULT_CACHE_TTL_HOURS))) * 3600.0
except Exception:
    SEAPATH_PLAN_CACHE_TTL_SECONDS = 86400.0

try:
    SEAPATH_MAX_DISK_PLANS = int(os.getenv("SEAPATH_MAX_DISK_PLANS", str(_DEFAULT_MAX_DISK_PLANS)))
except Exception:
    SEAPATH_MAX_DISK_PLANS = 500

_last_cache_prune_time: float = 0.0


def _prune_plan_cache(force: bool = False) -> None:
    """
    Safely prunes disk cache files in _PLANS_CACHE_DIR:
    1. Removes expired JSON plan files older than SEAPATH_PLAN_CACHE_TTL_SECONDS.
    2. Enforces SEAPATH_MAX_DISK_PLANS by deleting oldest files by mtime.
    3. Cleans orphan .tmp files older than 300 seconds.
    Throttled to run at most once every 30 seconds unless force=True.
    Thread-safe and fail-safe: never raises exceptions to callers.
    """
    global _last_cache_prune_time
    now = time.time()
    if not force and (now - _last_cache_prune_time < 30.0):
        return

    _last_cache_prune_time = now
    try:
        if not _PLANS_CACHE_DIR.exists():
            return

        json_files: List[Tuple[Path, float]] = []
        for p in _PLANS_CACHE_DIR.iterdir():
            try:
                if p.is_file():
                    st = p.stat()
                    if p.suffix == ".tmp":
                        if now - st.st_mtime > 300.0:
                            p.unlink(missing_ok=True)
                    elif p.suffix == ".json":
                        age = now - st.st_mtime
                        if age > SEAPATH_PLAN_CACHE_TTL_SECONDS:
                            p.unlink(missing_ok=True)
                            plan_id = p.stem
                            _SAVED_PLANS.pop(plan_id, None)
                        else:
                            json_files.append((p, st.st_mtime))
            except Exception:
                continue

        # If remaining files still exceed max capacity, delete oldest by mtime
        if len(json_files) > SEAPATH_MAX_DISK_PLANS:
            json_files.sort(key=lambda item: item[1])  # oldest first
            to_remove_count = len(json_files) - SEAPATH_MAX_DISK_PLANS
            for p, _ in json_files[:to_remove_count]:
                try:
                    p.unlink(missing_ok=True)
                    plan_id = p.stem
                    _SAVED_PLANS.pop(plan_id, None)
                except Exception:
                    continue
    except Exception:
        pass


_SAVED_PLANS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _store_passage_plan(plan_id: str, plan_data: Dict[str, Any]) -> None:
    """Stores verified passage plan in memory LRU and cross-process disk cache with automatic pruning."""
    # 1. Update in-memory LRU
    if plan_id in _SAVED_PLANS:
        _SAVED_PLANS.move_to_end(plan_id)
    _SAVED_PLANS[plan_id] = plan_data
    while len(_SAVED_PLANS) > 200:
        _SAVED_PLANS.popitem(last=False)

    # 2. Persist to disk for multi-worker/process access
    try:
        plan_file = _PLANS_CACHE_DIR / f"{plan_id}.json"
        tmp_file = _PLANS_CACHE_DIR / f"{plan_id}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(plan_data, f)
        tmp_file.replace(plan_file)
        _prune_plan_cache()
    except Exception:
        pass


def _get_passage_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves verified passage plan from memory LRU or cross-process disk cache enforcing TTL."""
    # 1. Check in-memory LRU
    if plan_id in _SAVED_PLANS:
        _SAVED_PLANS.move_to_end(plan_id)
        return _SAVED_PLANS[plan_id]

    # 2. Check cross-process disk cache
    plan_file = _PLANS_CACHE_DIR / f"{plan_id}.json"
    if plan_file.exists():
        try:
            st = plan_file.stat()
            if time.time() - st.st_mtime > SEAPATH_PLAN_CACHE_TTL_SECONDS:
                plan_file.unlink(missing_ok=True)
                _SAVED_PLANS.pop(plan_id, None)
                return None
            with open(plan_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            _SAVED_PLANS[plan_id] = data
            _SAVED_PLANS.move_to_end(plan_id)
            while len(_SAVED_PLANS) > 200:
                _SAVED_PLANS.popitem(last=False)
            return data
        except Exception:
            return None
    return None



@app.post("/api/route")
def calculate_route(req: RouteRequest):
    """
    Calculates safe passage plan complying with draft, squat, UKC, tide, and fairway constraints.
    Supports single-leg and multi-waypoint routes, circular turn arcs, bottleneck analysis,
    and full PIANC DUKC budget.
    """
    if req.waypoints and len(req.waypoints) >= 2:
        points = req.waypoints
    elif req.start_lon is not None and req.start_lat is not None and req.goal_lon is not None and req.goal_lat is not None:
        points = [[req.start_lon, req.start_lat], [req.goal_lon, req.goal_lat]]
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid request: either specify departure/destination coordinates or provide at least 2 waypoints."
        )

    points_within = all(_is_within_coverage(pt[0], pt[1], routing_grid) for pt in points)
    if not points_within:
        for wb_id, wb_cand in waterbody_mgr.waterbodies.items():
            if all(_is_within_coverage(pt[0], pt[1], wb_cand.grid) for pt in points):
                activate_waterbody_instance(wb_id)
                points_within = True
                break

    for idx, pt in enumerate(points):
        if not _is_within_coverage(pt[0], pt[1], routing_grid):
            raise HTTPException(
                status_code=400,
                detail=f"Waypoint {idx + 1} ({pt[0]:.4f}, {pt[1]:.4f}) is outside bathymetry coverage area."
            )

    for k in range(len(points) - 1):
        leg_dist = calculate_distance_m(points[k][1], points[k][0], points[k + 1][1], points[k + 1][0])
        if leg_dist < 10.0:
            raise HTTPException(
                status_code=400,
                detail=f"Waypoint {k + 1} and Waypoint {k + 2} are identical or too close ({leg_dist:.1f}m < 10m minimum separation)."
            )

    # -- Единый бюджет поиска: считаем ОДИН раз на весь запрос -----------------
    # Что делаем: вместо трёх разных формул (здесь + внутри Python-роутера +
    # внутри C++-роутера) вызываем канон dukc.compute_search_budget.
    # На выходе:
    #   min_required_chart_depth — порог карты: ячейка мельче → непроходима.
    #     Формула strict: draft + squat + heel + wave + max(0.05, ukc - tide).
    #     Именно этот порог потом ест A* (через min_chart_depth_m), луч
    #     видимости при спрямлении и snap-привязка старта/финиша к воде.
    #   search_dynamic_draft — динамическая осадка (draft+squat+heel+wave),
    #     уходит в роутеры как dynamic_draft_m, чтобы они НЕ пересчитывали
    #     squat по-своему и не дрейфовали друг от друга.
    #   effective_ukc_for_search — запас с поправкой на прилив, уходит в
    #     роутеры как ukc для совместимости со старыми вызовами.
    # Зачем: раньше поиск требовал меньше, чем пост-проверки (safety,
    # bottleneck, dukc), и маршрут падал в CRITICAL уже после прокладки.
    # Теперь поиск строже-or-равен проверкам: мель режется на поиске (400),
    # а не пост-фактум.
    search_budget = compute_search_budget(
        draft_m=req.draft,
        speed_knots=req.speed_knots,
        ukc_net_margin_m=req.ukc,
        block_coefficient=req.block_coefficient,
        tide_offset_m=req.tide_offset_m,
        wave_height_m=req.wave_height_m,
        turning_radius_m=req.turning_radius_m,
        gm_m=req.gm_m,
        beam_m=req.beam_m,
        exposed_waters=req.exposed_waters,
        strict_dukc=req.strict_dukc,
    )
    effective_ukc_for_search = search_budget["effective_ukc_m"]
    dynamic_squat = search_budget["dynamic_squat_m"]
    search_dynamic_draft = search_budget["dynamic_draft_m"]
    min_required_chart_depth = search_budget["min_chart_depth_m"]

    # Pre-snap all waypoints to navigable water before leg pathfinding
    snapped_points: List[Tuple[int, int]] = []
    snapped_wps_info: List[Dict[str, Any]] = []

    for idx, pt in enumerate(points):
        pt_r, pt_c = routing_grid.lonlat_to_cell(pt[0], pt[1])
        sr, sc, s_dist, s_reason = _snap_to_navigable(
            pt_r, pt_c, routing_grid, min_required_chart_depth,
            orig_lon=pt[0], orig_lat=pt[1]
        )
        if sr is None:
            raise HTTPException(
                status_code=400,
                detail=f"Waypoint {idx + 1} ({pt[0]:.4f}, {pt[1]:.4f}): no navigable channel found satisfying safety depth ({min_required_chart_depth:.1f}m) within 1500m (reason: {s_reason})."
            )
        snapped_points.append((sr, sc))
        snapped_wps_info.append({
            "index": idx + 1,
            "snapped": s_dist > 0.0,
            "snap_distance_m": s_dist,
            "snap_reason": s_reason,
            "original_coords": [pt[0], pt[1]],
            "snapped_cell": [sr, sc],
        })

    # Calculate routes along each leg
    combined_route: List[List[float]] = []
    combined_profile: List[Dict[str, Any]] = []
    leg_results: List[Dict[str, Any]] = []
    cum_dist_m = 0.0

    for k in range(len(points) - 1):
        start_r, start_c = snapped_points[k]
        goal_r, goal_c = snapped_points[k + 1]

        # -- Вызов роутера с готовым бюджетом ----------------------------------
        # draft/speed/ukc ниже — для совместимости (старый роутер без новых
        # kwargs их использует для собственного пересчёта). Новый роутер видит
        # min_chart_depth_m + dynamic_draft_m и свой пересчёт ПРОПУСКАЕТ —
        # так Python и C++ ядра считают строго одинаково.
        # ukc передаём уже с поправкой на прилив (effective), чтобы legacy-
        # ветка внутри роутера тоже была с приливом.
        leg_res = router.find_path(
            start_r=start_r,
            start_c=start_c,
            goal_r=goal_r,
            goal_c=goal_c,
            draft=req.draft,
            speed_knots=req.speed_knots,
            ukc=effective_ukc_for_search,
            turning_radius_m=req.turning_radius_m,
            fairway_preference=req.fairway_preference,
            block_coefficient=req.block_coefficient,
            min_chart_depth_m=min_required_chart_depth,
            dynamic_draft_m=search_dynamic_draft,
        )

        if leg_res is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No navigable passage found for Leg {k + 1} (between WP{k + 1} and WP{k + 2}) "
                    f"satisfying static draft ({req.draft:.1f}m) and UKC margin ({req.ukc:.1f}m). "
                    f"Consider increasing tide elevation or reducing vessel draft."
                )
            )

        leg_results.append(leg_res)

        # Merge route coordinates
        leg_coords = leg_res["route"]
        if not combined_route:
            combined_route.extend(leg_coords)
        else:
            combined_route.extend(leg_coords[1:])

        # -- Склейка профиля глубин по плечам ----------------------------------
        # Роутер считает clearance = depth - dynamic_draft - effective_ukc,
        # где effective_ukc = ukc - tide_offset.
        # То есть: clearance = (depth + tide_offset) - (dynamic_draft + ukc).
        # Прилив УЖЕ полностью учтён в clearance роутера. Повторно прибавлять нельзя.
        for pt in leg_res["profile"]:
            combined_profile.append({
                "distance_from_start_m": round(cum_dist_m + pt["distance_from_start_m"], 1),
                "depth": pt["depth"],
                "clearance": round(pt["clearance"], 2),
            })

        cum_dist_m += leg_res["diagnostics"]["distance_m"]

    # Attach coordinates to profile soundings
    _attach_coordinates_to_profile(combined_profile, combined_route)

    # Build unified passage plan waypoints
    raw_waypoints: List[Dict[str, Any]] = []
    for leg_idx, leg in enumerate(leg_results):
        wps = leg.get("waypoints", [])
        if leg_idx == 0:
            raw_waypoints.extend(wps)
        else:
            raw_waypoints.extend(wps[1:])

    # Recalculate kinematics across combined waypoints
    combined_waypoints: List[Dict[str, Any]] = []
    n_wps = len(raw_waypoints)
    for i in range(n_wps):
        wp = dict(raw_waypoints[i])
        wp["index"] = i + 1

        if i < n_wps - 1:
            wp_next = raw_waypoints[i + 1]
            wp["leg_bearing_deg"] = round(calculate_bearing_deg(wp["lat"], wp["lon"], wp_next["lat"], wp_next["lon"]), 1)
            wp["leg_distance_nm"] = round(calculate_distance_m(wp["lat"], wp["lon"], wp_next["lat"], wp_next["lon"]) / 1852.0, 2)
        else:
            wp["leg_bearing_deg"] = 0.0
            wp["leg_distance_nm"] = 0.0

        wp["port_xtd_nm"] = round(req.port_xtd_m / 1852.0, 3)
        wp["stbd_xtd_nm"] = round(req.stbd_xtd_m / 1852.0, 3)

        if 0 < i < n_wps - 1:
            wp_prev = raw_waypoints[i - 1]
            b_in = calculate_bearing_deg(wp_prev["lat"], wp_prev["lon"], wp["lat"], wp["lon"])
            b_out = wp["leg_bearing_deg"]
            diff = (b_out - b_in + 180.0) % 360.0 - 180.0
            turn_ang = abs(diff)
            wp["turn_angle_deg"] = round(turn_ang, 1)

            # Assign valid turning radius for intermediate course alterations
            raw_r = wp.get("turn_radius_m")
            act_r = float(raw_r) if (raw_r is not None and float(raw_r) > 10.0) else float(req.turning_radius_m or 150.0)
            wp["turn_radius_m"] = act_r

            geom = calculate_turn_arc_geometry(
                prev_lon=wp_prev["lon"],
                prev_lat=wp_prev["lat"],
                wp_lon=wp["lon"],
                wp_lat=wp["lat"],
                next_lon=wp_next["lon"],
                next_lat=wp_next["lat"],
                radius_m=act_r,
                speed_knots=req.speed_knots,
            )
            if geom:
                wp["rot_deg_min"] = geom["rot_deg_min"]
                wp["wop_distance_m"] = geom["wop_dist_m"]
                wp["effective_turn_radius_m"] = geom["effective_radius_m"]
                wp["maneuver_feasible"] = geom["is_feasible"]
            else:
                wp["rot_deg_min"] = 0.0
                wp["wop_distance_m"] = 0.0
                wp["maneuver_feasible"] = True
        else:
            wp["turn_angle_deg"] = 0.0
            wp["rot_deg_min"] = 0.0
            wp["wop_distance_m"] = 0.0
            wp["maneuver_feasible"] = True

        if i == 0 and snapped_wps_info[0]["snapped"]:
            wp["snap_offset_m"] = snapped_wps_info[0]["snap_distance_m"]
            wp["snap_reason"] = snapped_wps_info[0]["snap_reason"]
            wp["original_coords"] = snapped_wps_info[0]["original_coords"]
        elif i == n_wps - 1 and snapped_wps_info[-1]["snapped"]:
            wp["snap_offset_m"] = snapped_wps_info[-1]["snap_distance_m"]
            wp["snap_reason"] = snapped_wps_info[-1]["snap_reason"]
            wp["original_coords"] = snapped_wps_info[-1]["original_coords"]
        elif 0 < i < n_wps - 1:
            for s_info in snapped_wps_info[1:-1]:
                if s_info["snapped"]:
                    sc_lat, sc_lon = routing_grid.get_cell_coords(s_info["snapped_cell"][0], s_info["snapped_cell"][1])
                    if calculate_distance_m(wp["lat"], wp["lon"], sc_lat, sc_lon) < 150.0:
                        wp["snap_offset_m"] = s_info["snap_distance_m"]
                        wp["snap_reason"] = s_info["snap_reason"]
                        wp["original_coords"] = s_info["original_coords"]
                        break

        combined_waypoints.append(wp)

    # Splice filleted turn arcs into combined_route so real route geometry contains the physical turns
    turn_geoms_list = []
    for i in range(1, n_wps - 1):
        wp_turn = combined_waypoints[i]
        wp_prev = combined_waypoints[i - 1]
        wp_next = combined_waypoints[i + 1]
        r_val = float(wp_turn.get("turn_radius_m") or req.turning_radius_m or 150.0)
        if r_val > 10.0:
            g = calculate_turn_arc_geometry(
                prev_lon=wp_prev["lon"],
                prev_lat=wp_prev["lat"],
                wp_lon=wp_turn["lon"],
                wp_lat=wp_turn["lat"],
                next_lon=wp_next["lon"],
                next_lat=wp_next["lat"],
                radius_m=r_val,
                speed_knots=req.speed_knots,
            )
            if g:
                g["wp_index"] = i + 1
                turn_geoms_list.append(g)

    if turn_geoms_list:
        combined_route = splice_turn_arcs_into_route(combined_route, turn_geoms_list)

    # Rebuild synchronized continuous depth soundings profile and metrics along finalized combined_route
    updated_profile: List[Dict[str, Any]] = []
    final_cum_dist_m = 0.0

    for seg_idx in range(len(combined_route)):
        lon0, lat0 = combined_route[seg_idx]

        if seg_idx < len(combined_route) - 1:
            lon1, lat1 = combined_route[seg_idx + 1]
            seg_len_m = calculate_distance_m(lat0, lon0, lat1, lon1)

            r0, c0 = full_grid.lonlat_to_cell(lon0, lat0)
            r1, c1 = full_grid.lonlat_to_cell(lon1, lat1)
            depths_along = full_grid.sample_depth_along_segment(r0, c0, r1, c1)
            n_samples = len(depths_along)

            for k, d in enumerate(depths_along):
                fraction = k / max(1, n_samples - 1)
                sample_dist = final_cum_dist_m + seg_len_m * fraction
                sample_lat = lat0 + (lat1 - lat0) * fraction
                sample_lon = lon0 + (lon1 - lon0) * fraction
                clearance_val = (d + req.tide_offset_m) - (search_dynamic_draft + req.ukc)
                updated_profile.append({
                    "distance_from_start_m": round(sample_dist, 1),
                    "depth": round(float(d), 2),
                    "clearance": round(float(clearance_val), 2),
                    "lat": round(sample_lat, 6),
                    "lon": round(sample_lon, 6),
                })
            final_cum_dist_m += seg_len_m
        elif len(combined_route) == 1:
            r0, c0 = full_grid.lonlat_to_cell(lon0, lat0)
            d = full_grid.get_depth(r0, c0)
            clearance_val = (d + req.tide_offset_m) - (search_dynamic_draft + req.ukc)
            updated_profile.append({
                "distance_from_start_m": 0.0,
                "depth": round(float(d), 2),
                "clearance": round(float(clearance_val), 2),
                "lat": round(lat0, 6),
                "lon": round(lon0, 6),
            })

    if updated_profile:
        combined_profile = updated_profile
        cum_dist_m = final_cum_dist_m

    total_dist_nm = round(cum_dist_m / 1852.0, 2)
    eta_hours = round(total_dist_nm / req.speed_knots, 2) if req.speed_knots > 0 else None
    min_clearance = min((p["clearance"] for p in combined_profile), default=0.0)

    # 1. Safety Corridor (XTD)
    corridor_seg_xtds = compute_route_segment_xtds(
        combined_route,
        combined_waypoints,
        default_port_xtd_m=req.port_xtd_m,
        default_stbd_xtd_m=req.stbd_xtd_m,
    )
    corridor_feature = generate_safety_corridor_polygon(
        combined_route,
        port_xtd_m=req.port_xtd_m,
        stbd_xtd_m=req.stbd_xtd_m,
        seg_xtds=corridor_seg_xtds,
    )

    # 2. Circular Turn Arcs & Wheel Over Point (WOP) FeatureCollection
    turn_arcs_fc = generate_turn_arcs_feature_collection(
        combined_waypoints,
        radius_m=req.turning_radius_m,
        speed_knots=req.speed_knots,
    )

    # Calculate actual turning heel sinkage per waypoint from its executed radius
    for wp in combined_waypoints:
        turn_ang = wp.get("turn_angle_deg", 0.0)
        act_r = wp.get("turn_radius_m", req.turning_radius_m)
        if abs(turn_ang) >= 4.0 and act_r > 10.0 and req.speed_knots > 0:
            heel_wp = calculate_turning_heel(req.speed_knots, act_r, req.draft, req.beam_m, req.gm_m)
            wp["heel_sinkage_m"] = heel_wp["heel_sinkage_m"]
            wp["heel_angle_deg"] = heel_wp["heel_angle_deg"]
        else:
            wp["heel_sinkage_m"] = 0.0
            wp["heel_angle_deg"] = 0.0

    max_actual_heel = max((wp.get("heel_sinkage_m", 0.0) for wp in combined_waypoints), default=0.0)
    has_maneuver_violation = any(wp.get("maneuver_feasible") is False for wp in combined_waypoints)

    # 3. ECDIS Route Safety Verification (IEC 61174) with Full 2D XTD Scan on actual filleted route
    safety_audit = verify_ecdis_route_safety(
        waypoints=combined_waypoints,
        profile=combined_profile,
        fairways_coll=load_fairways_geojson(),
        draft=req.draft,
        dynamic_squat=dynamic_squat,
        ukc=req.ukc,
        speed_knots=req.speed_knots,
        min_chart_depth_m=min_required_chart_depth,
        dynamic_draft_m=search_dynamic_draft,
        corridor_feature=corridor_feature,
        grid=full_grid,
        requested_radius_m=req.turning_radius_m,
        port_xtd_m=req.port_xtd_m,
        stbd_xtd_m=req.stbd_xtd_m,
        route_coords=combined_route,
    )

    # 4. Bottleneck & Choke Point Analysis + Speed Adaptation Advisory
    bottleneck_analysis = analyze_route_bottlenecks(
        profile=combined_profile,
        draft_m=req.draft,
        speed_knots=req.speed_knots,
        ukc_net_margin_m=req.ukc,
        block_coefficient=req.block_coefficient,
        turning_radius_m=req.turning_radius_m,
        wave_height_m=req.wave_height_m,
        tide_offset_m=req.tide_offset_m,
        beam_m=req.beam_m,
        gm_m=req.gm_m,
        exposed_waters=req.exposed_waters,
    )

    # 5. Full PIANC DUKC Budget (отчётный, на самой мелкой точке профиля)
    min_chart_depth = min((p["depth"] for p in combined_profile), default=10.0)
    dukc_budget = compute_dukc_budget(
        draft_m=req.draft,
        speed_knots=req.speed_knots,
        block_coefficient=req.block_coefficient,
        ukc_net_margin_m=req.ukc,
        water_depth_m=min_chart_depth,
        turning_radius_m=req.turning_radius_m,
        wave_height_m=req.wave_height_m,
        tide_offset_m=req.tide_offset_m,
        beam_m=req.beam_m,
        gm_m=req.gm_m,
        exposed_waters=req.exposed_waters,
    )

    # -- Synthetic demo enforcement (P1.4) & Passage Plan Registry -----------
    plan_id = str(uuid.uuid4())
    if is_synthetic_mode:
        safety_audit["is_safe"] = False
        safety_audit["status"] = "DEMO_UNVERIFIED"
        data_integrity = "SYNTHETIC_DEMO_NON_NAVIGABLE"
        is_executable = False
        if not any(a.get("code") == "ALM-10-SYNTHETIC-DATA-NON-NAVIGABLE" for a in safety_audit.get("alarms", [])):
            safety_audit.setdefault("alarms", []).append({
                "severity": "CRITICAL",
                "code": "ALM-10-SYNTHETIC-DATA-NON-NAVIGABLE",
                "title": "Synthetic Bathymetry Mode Active",
                "detail": "Passage plan generated on synthetic demonstration bathymetry. Uncertified for real navigation.",
            })
    else:
        data_integrity = "OPERATIONAL_GEOTIFF"
        is_executable = bool(not has_maneuver_violation and safety_audit["is_safe"])

    # -- Диагностика ответа: всё из одного бюджета, без пересчётов -----------
    diagnostics = {
        "plan_id": plan_id,
        "distance_m": round(cum_dist_m, 0),
        "distance_nm": total_dist_nm,
        "eta_hours": eta_hours,
        "min_clearance_m": round(min_clearance, 2),
        "gross_ukc_m": round(min_chart_depth - req.draft, 2),
        "required_ukc_m": req.ukc,
        "clearance_margin_m": round(min_clearance, 2),
        "dynamic_draft_m": round(search_dynamic_draft, 2),
        "dynamic_squat_m": round(dynamic_squat, 2),
        "heel_sinkage_m": round(search_budget["heel_sinkage_m"], 3),
        "max_actual_heel_sinkage_m": round(max_actual_heel, 3),
        "wave_allowance_m": round(search_budget["wave_allowance_m"], 3),
        "min_chart_depth_m": round(min_required_chart_depth, 2),
        "strict_dukc": req.strict_dukc,
        "waypoints": len(combined_waypoints),
        "tide_offset_m": req.tide_offset_m,
        "wave_height_m": req.wave_height_m,
        "routing_engine": "seapath_native_cpp" if _HAS_NATIVE else "python_fallback",
        "has_maneuvering_violation": has_maneuver_violation,
        "is_navigable_candidate": True,
        "is_executable_passage_plan": is_executable,
        "data_integrity": data_integrity,
        "waypoint_snapping": {
            "start_snapped": snapped_wps_info[0]["snapped"],
            "start_snap_distance_m": snapped_wps_info[0]["snap_distance_m"],
            "start_snap_reason": snapped_wps_info[0]["snap_reason"],
            "goal_snapped": snapped_wps_info[-1]["snapped"],
            "goal_snap_distance_m": snapped_wps_info[-1]["snap_distance_m"],
            "goal_snap_reason": snapped_wps_info[-1]["snap_reason"],
            "all_waypoints_snapped": any(w["snapped"] for w in snapped_wps_info),
            "snapped_waypoints": [w for w in snapped_wps_info if w["snapped"]],
        },
    }

    _store_passage_plan(plan_id, {
        "plan_id": plan_id,
        "waypoints": combined_waypoints,
        "route": combined_route,
        "profile": combined_profile,
        "corridor": corridor_feature,
        "turn_arcs": turn_arcs_fc,
        "draft": req.draft,
        "speed_knots": req.speed_knots,
        "ukc": req.ukc,
        "block_coefficient": req.block_coefficient,
        "tide_offset_m": req.tide_offset_m,
        "wave_height_m": req.wave_height_m,
        "turning_radius_m": req.turning_radius_m,
        "beam_m": req.beam_m,
        "gm_m": req.gm_m,
        "exposed_waters": req.exposed_waters,
        "strict_dukc": req.strict_dukc,
        "port_xtd_m": req.port_xtd_m,
        "stbd_xtd_m": req.stbd_xtd_m,
        "min_chart_depth_m": min_required_chart_depth,
        "safety_contour": min_required_chart_depth,
        "properties": diagnostics,
        "is_safe": safety_audit["is_safe"],
        "safety_status": safety_audit["status"],
        "safety_check": safety_audit,
        "is_synthetic": is_synthetic_mode,
    })

    return {
        "type": "Feature",
        "plan_id": plan_id,
        "properties": {
            **diagnostics,
            "safety_check": safety_audit,
            "bottleneck": bottleneck_analysis,
            "dukc": dukc_budget,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": combined_route,
        },
        "corridor": corridor_feature,
        "turn_arcs": turn_arcs_fc,
        "profile": combined_profile,
        "waypoints": combined_waypoints,
        "safety_check": safety_audit,
        "bottleneck": bottleneck_analysis,
        "dukc": dukc_budget,
    }


def _sanitize_filename(name: Optional[str], default: str = "passage_plan") -> str:
    if not name:
        return default
    import re
    clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', name.strip())
    return clean[:64] or default


def _validate_route_waypoints_coupling(
    waypoints: Optional[List[ExportWaypoint]],
    route: List[List[float]],
    max_lateral_dev_m: float = 50.0,
) -> None:
    """
    Validates tight coupling between waypoints sequence and detailed route coordinates:
    1. Start and End points match within tolerance.
    2. Every waypoint projects within max_lateral_dev_m of the route polyline.
    3. Waypoints appear in monotonic order along the route path (no looping backwards or skipping).
    """
    if not route or len(route) < 2:
        return
    if not waypoints or len(waypoints) < 2:
        raise HTTPException(status_code=400, detail="At least 2 waypoints are required.")

    # 1. Departure match
    wp_start_lat = waypoints[0].get_lat()
    wp_start_lon = waypoints[0].get_lon()
    r_start_lon, r_start_lat = route[0][0], route[0][1]
    d_start = calculate_distance_m(wp_start_lat, wp_start_lon, r_start_lat, r_start_lon)
    if d_start > max_lateral_dev_m:
        raise HTTPException(
            status_code=400,
            detail=f"Passage plan geometry mismatch: Departure waypoint WP1 ({wp_start_lon:.4f}, {wp_start_lat:.4f}) does not match route departure ({r_start_lon:.4f}, {r_start_lat:.4f}) (deviation {d_start:.1f}m > {max_lateral_dev_m:.0f}m)."
        )

    # 2. Destination match
    wp_end_lat = waypoints[-1].get_lat()
    wp_end_lon = waypoints[-1].get_lon()
    r_end_lon, r_end_lat = route[-1][0], route[-1][1]
    d_end = calculate_distance_m(wp_end_lat, wp_end_lon, r_end_lat, r_end_lon)
    if d_end > max_lateral_dev_m:
        raise HTTPException(
            status_code=400,
            detail=f"Passage plan geometry mismatch: Destination waypoint WP{len(waypoints)} ({wp_end_lon:.4f}, {wp_end_lat:.4f}) does not match route destination ({r_end_lon:.4f}, {r_end_lat:.4f}) (deviation {d_end:.1f}m > {max_lateral_dev_m:.0f}m)."
        )

    # Cumulative distances along route polyline
    route_cum_dist = [0.0]
    for i in range(1, len(route)):
        seg_d = calculate_distance_m(route[i - 1][1], route[i - 1][0], route[i][1], route[i][0])
        route_cum_dist.append(route_cum_dist[-1] + seg_d)

    deg_to_rad = math.pi / 180.0
    meters_per_deg_lat = 111139.0

    def project_onto_route(pt_lon: float, pt_lat: float) -> Tuple[float, float]:
        min_dev = math.inf
        proj_dist_along = 0.0
        for seg in range(len(route) - 1):
            p0_lon, p0_lat = route[seg][0], route[seg][1]
            p1_lon, p1_lat = route[seg + 1][0], route[seg + 1][1]
            lat_mid = (p0_lat + p1_lat) / 2.0
            m_lat = meters_per_deg_lat
            m_lon = meters_per_deg_lat * math.cos(lat_mid * deg_to_rad)
            if abs(m_lon) < 1.0:
                m_lon = 1.0

            dx = (p1_lon - p0_lon) * m_lon
            dy = (p1_lat - p0_lat) * m_lat
            seg_len = math.hypot(dx, dy)
            if seg_len < 1e-3:
                dev = calculate_distance_m(pt_lat, pt_lon, p0_lat, p0_lon)
                if dev < min_dev:
                    min_dev = dev
                    proj_dist_along = route_cum_dist[seg]
                continue

            wx = (pt_lon - p0_lon) * m_lon
            wy = (pt_lat - p0_lat) * m_lat
            t = max(0.0, min(1.0, (wx * dx + wy * dy) / (seg_len * seg_len)))
            closest_x = p0_lon * m_lon + t * dx
            closest_y = p0_lat * m_lat + t * dy
            dev = math.hypot((pt_lon * m_lon) - closest_x, (pt_lat * m_lat) - closest_y)
            if dev < min_dev:
                min_dev = dev
                proj_dist_along = route_cum_dist[seg] + t * seg_len
        return min_dev, proj_dist_along

    last_proj_dist = -1.0
    num_wp = len(waypoints)
    for idx, wp in enumerate(waypoints):
        wp_lat = wp.get_lat()
        wp_lon = wp.get_lon()
        min_dev, proj_dist = project_onto_route(wp_lon, wp_lat)

        # Dynamic kinematic tolerance for course alteration / turn waypoints (fillet arc kinematics)
        allowed_dev = max_lateral_dev_m
        if 0 < idx < num_wp - 1:
            prev_wp = waypoints[idx - 1]
            next_wp = waypoints[idx + 1]
            b_in = calculate_bearing_deg(prev_wp.get_lat(), prev_wp.get_lon(), wp_lat, wp_lon)
            b_out = calculate_bearing_deg(wp_lat, wp_lon, next_wp.get_lat(), next_wp.get_lon())
            # Angle is strictly calculated on the server from the actual waypoint vectors
            t_angle = abs((b_out - b_in + 180.0) % 360.0 - 180.0)

            # Turning radius is bounded within physical navigation limits [50m, 2000m]
            raw_r = wp.turn_radius_m
            turn_r = min(max(float(raw_r if raw_r is not None and raw_r > 0 else 150.0), 50.0), 2000.0)

            # Cap angle for circular fillet offset calculation at 120°
            if 1.0 <= t_angle <= 120.0:
                half_ang_rad = math.radians(t_angle / 2.0)
                cos_val = math.cos(half_ang_rad)
                if cos_val > 0.1:
                    kinematic_offset = turn_r * (1.0 / cos_val - 1.0)
                    # Kinematic tolerance matches circular fillet offset + buffer (bounded at 2500m)
                    allowed_dev = min(2500.0, max(allowed_dev, kinematic_offset + 50.0))


        if min_dev > allowed_dev:
            raise HTTPException(
                status_code=400,
                detail=f"Passage plan geometry mismatch: Waypoint WP{idx + 1} ({wp_lon:.4f}, {wp_lat:.4f}) deviates by {min_dev:.1f}m from route track (> {allowed_dev:.0f}m tolerance)."
            )
        # Monotonic progression with tolerance for fillet arc tangent points
        if proj_dist < last_proj_dist - 35.0:
            raise HTTPException(
                status_code=400,
                detail=f"Passage plan geometry mismatch: Waypoint WP{idx + 1} appears out of order along route track (non-monotonic progression)."
            )
        last_proj_dist = proj_dist


def _verify_export_safety(
    waypoints: List[Dict[str, Any]],
    route_coords: List[List[float]],
    draft: float,
    speed_knots: float,
    ukc: float,
    safety_contour: Optional[float] = None,
    block_coefficient: float = 0.65,
    tide_offset_m: float = 0.0,
    wave_height_m: float = 0.0,
    turning_radius_m: float = 150.0,
    beam_m: Optional[float] = None,
    gm_m: Optional[float] = None,
    exposed_waters: bool = False,
    strict_dukc: bool = True,
    is_safe_claim: Optional[bool] = None,
    safety_status_claim: Optional[str] = None,
) -> Tuple[bool, str, List[str], float]:
    """
    Performs server-side ECDIS safety verification on passage plan prior to export:
    - Calculates canonical search budget (squat, heel, wave allowance, tide offset).
    - Prevents client-supplied safety_contour from weakening canonical minimum depth threshold.
    - Rejects synthetic demonstration bathymetry as uncertified for navigation.
    - Samples depths along route track and evaluates full 2D corridor (XTD) area swath.
    Returns (is_safe, safety_status, alarm_codes, effective_safety_depth).
    """
    if full_grid.is_synthetic or is_synthetic_mode or safety_status_claim == "DEMO_UNVERIFIED":
        fallback_depth = safety_contour if (safety_contour is not None and math.isfinite(safety_contour) and safety_contour > 0.0) else (draft + ukc)
        return False, "DEMO_UNVERIFIED", ["ALM-10-SYNTHETIC-DATA-NON-NAVIGABLE"], float(fallback_depth)

    canonical_budget = compute_search_budget(
        draft_m=draft,
        speed_knots=speed_knots,
        ukc_net_margin_m=ukc,
        block_coefficient=block_coefficient,
        tide_offset_m=tide_offset_m,
        wave_height_m=wave_height_m,
        turning_radius_m=turning_radius_m,
        gm_m=gm_m,
        beam_m=beam_m,
        exposed_waters=exposed_waters,
        strict_dukc=strict_dukc,
    )
    canonical_min_depth = canonical_budget["min_chart_depth_m"]
    # Guard against NaN / Inf / non-positive safety_contour
    if safety_contour is not None and math.isfinite(safety_contour) and safety_contour > 0.0:
        effective_safety_depth = max(float(safety_contour), canonical_min_depth)
    else:
        effective_safety_depth = canonical_min_depth

    dynamic_squat = canonical_budget["dynamic_squat_m"]
    search_dynamic_draft = canonical_budget["dynamic_draft_m"]
    effective_ukc = canonical_budget["effective_ukc_m"]

    # Sample bathymetric depths along route
    profile_samples: List[Dict[str, Any]] = []
    cum_d = 0.0
    for i in range(len(route_coords) - 1):
        p0 = route_coords[i]
        p1 = route_coords[i + 1]
        seg_d = calculate_distance_m(p0[1], p0[0], p1[1], p1[0])
        if full_grid.is_within_coverage(p0[0], p0[1]) and full_grid.is_within_coverage(p1[0], p1[1]):
            r0, c0 = full_grid.lonlat_to_cell(p0[0], p0[1])
            r1, c1 = full_grid.lonlat_to_cell(p1[0], p1[1])
            seg_depths = full_grid.sample_depth_along_segment(r0, c0, r1, c1)
        else:
            seg_depths = [0.0]
        num_pts = max(1, len(seg_depths))
        for s_idx, d_val in enumerate(seg_depths):
            dist_pt = cum_d + seg_d * (s_idx / num_pts)
            clr = (d_val + tide_offset_m) - (search_dynamic_draft + ukc)
            profile_samples.append({
                "distance_from_start_m": dist_pt,
                "depth": d_val,
                "clearance": clr,
            })
        cum_d += seg_d

    port_xtd_m = max((float(wp.get("port_xtd_nm", 0.10) or 0.10) for wp in waypoints), default=0.10) * 1852.0
    stbd_xtd_m = max((float(wp.get("stbd_xtd_nm", 0.10) or 0.10) for wp in waypoints), default=0.10) * 1852.0

    safety_audit = verify_ecdis_route_safety(
        waypoints=waypoints,
        profile=profile_samples,
        fairways_coll=load_fairways_geojson(),
        draft=draft,
        dynamic_squat=dynamic_squat,
        ukc=effective_ukc,
        speed_knots=speed_knots,
        min_chart_depth_m=effective_safety_depth,
        dynamic_draft_m=search_dynamic_draft,
        grid=full_grid,
        port_xtd_m=port_xtd_m,
        stbd_xtd_m=stbd_xtd_m,
        route_coords=route_coords,
        requested_radius_m=turning_radius_m,
    )

    any_maneuver_infeasible = any(wp.get("maneuver_feasible") is False for wp in waypoints)
    client_flagged_unsafe = (is_safe_claim is False) or (safety_status_claim == "CRITICAL_HAZARD") or any_maneuver_infeasible
    is_safe = bool(safety_audit["is_safe"] and not client_flagged_unsafe)

    # Preserve WARNING_ADVISORY status instead of overwriting with SAFE_PASSED
    if not is_safe:
        status = "CRITICAL_HAZARD"
    elif (
        safety_audit.get("status") == "WARNING_ADVISORY"
        or safety_status_claim == "WARNING_ADVISORY"
        or any(a.get("severity") == "WARNING" for a in safety_audit.get("alarms", []))
    ):
        status = "WARNING_ADVISORY"
    else:
        status = "SAFE_PASSED"

    alarm_codes = [a["code"] for a in safety_audit.get("alarms", [])]
    return is_safe, status, alarm_codes, effective_safety_depth


def _prepare_export_data(req: ExportRouteRequest) -> Tuple[List[Dict[str, Any]], List[List[float]], Dict[str, Any], bool, str, List[str]]:
    if req.plan_id:
        plan = _get_passage_plan(req.plan_id)
        if not plan:
            raise HTTPException(
                status_code=404,
                detail=f"Passage plan '{req.plan_id}' not found or expired. Please re-calculate route."
            )
        wps_dicts = plan["waypoints"]
        route_coords = plan["route"]
        draft = plan["draft"]
        speed_knots = plan["speed_knots"]
        ukc = plan["ukc"]
        block_coefficient = plan.get("block_coefficient", 0.65)
        tide_offset_m = plan.get("tide_offset_m", 0.0)
        wave_height_m = plan.get("wave_height_m", 0.0)
        turning_radius_m = plan.get("turning_radius_m", 150.0)
        beam_m = plan.get("beam_m")
        gm_m = plan.get("gm_m")
        exposed_waters = plan.get("exposed_waters", False)
        strict_dukc = plan.get("strict_dukc", True)
        is_safe_claim = plan.get("is_safe")
        safety_status_claim = plan.get("safety_status")
        # Plan-based export enforces server-computed safety contour
        safety_contour_eval = (
            plan.get("safety_contour")
            or plan.get("min_chart_depth_m")
            or plan.get("properties", {}).get("min_chart_depth_m")
        )
    else:
        # Validate waypoints/route coupling
        _validate_route_waypoints_coupling(req.waypoints, req.route)
        wps_dicts = []
        for wp in req.waypoints:
            d = wp.model_dump(exclude_none=True)
            d["lat"] = wp.get_lat()
            d["lon"] = wp.get_lon()
            wps_dicts.append(d)
        is_safe_claim = req.is_safe
        safety_status_claim = req.safety_status
        # Construct real physical navigation track with circular fillet turn arcs as Single Source of Truth
        canonical_route, turn_geoms = construct_filleted_path(
            wps_dicts,
            default_radius_m=req.turning_radius_m or 150.0,
            speed_knots=req.speed_knots,
            step_m=50.0,
        )
        for tg in turn_geoms:
            wp_idx = tg.get("wp_index", 0) - 1
            if 0 <= wp_idx < len(wps_dicts):
                wps_dicts[wp_idx]["turn_radius_m"] = tg.get("effective_radius_m", req.turning_radius_m)
                if not tg.get("is_feasible", True):
                    wps_dicts[wp_idx]["maneuver_feasible"] = False
                    is_safe_claim = False
                    safety_status_claim = "CRITICAL_HAZARD"

        if req.route and len(req.route) >= 2:
            # Verify turn arc kinematics coupling:
            # Check the ENTIRE circular turn arc (both arc coverage and turn zone conformity)
            # to prevent bypass attempts via jagged corner shortcuts or detours.
            for i in range(1, len(wps_dicts) - 1):
                wp_cur = wps_dicts[i]
                wp_prev = wps_dicts[i - 1]
                wp_next = wps_dicts[i + 1]
                raw_r = wp_cur.get("turn_radius_m")
                act_r = float(raw_r) if (raw_r is not None and float(raw_r) > 10.0) else float(req.turning_radius_m or 150.0)
                wp_cur["turn_radius_m"] = act_r

                geom = calculate_turn_arc_geometry(
                    prev_lon=wp_prev["lon"],
                    prev_lat=wp_prev["lat"],
                    wp_lon=wp_cur["lon"],
                    wp_lat=wp_cur["lat"],
                    next_lon=wp_next["lon"],
                    next_lat=wp_next["lat"],
                    radius_m=act_r,
                    speed_knots=req.speed_knots,
                )
                if geom and geom.get("arc_coordinates") and len(geom["arc_coordinates"]) >= 2:
                    arc_pts = geom["arc_coordinates"]
                    apex = arc_pts[len(arc_pts) // 2]
                    delta_apex = calculate_distance_m(wp_cur["lat"], wp_cur["lon"], apex[1], apex[0])
                    if delta_apex >= 25.0:
                        max_allowed_dev = 50.0

                        # Check 1 (Arc Coverage): Every point on the canonical arc must be covered by client route
                        for arc_pt in arc_pts:
                            d_arc_to_route = point_to_polyline_distance_m(arc_pt[0], arc_pt[1], req.route)
                            if d_arc_to_route > max_allowed_dev:
                                raise HTTPException(
                                    status_code=400,
                                    detail=(
                                        f"Client route geometry does not conform to waypoint {i + 1} turning kinematics "
                                        f"(radius {act_r:.0f}m, angle {geom['turn_angle_deg']}°): required turn arc departs by "
                                        f"{d_arc_to_route:.1f}m from route track (allowed: <= {max_allowed_dev:.1f}m). "
                                        "Omit 'route' to auto-generate certified filleted trajectory, or supply a route conforming to turn arcs."
                                    )
                                )

                        # Check 2 (Turn Zone Containment): Vertices of client route within turn zone must stay within canonical filleted corridor
                        wop_dist = float(geom.get("wop_distance_m", 0.0) or act_r)
                        turn_zone_radius = max(act_r + 50.0, wop_dist + 50.0)
                        for p in req.route:
                            d_to_wp = calculate_distance_m(wp_cur["lat"], wp_cur["lon"], p[1], p[0])
                            if d_to_wp <= turn_zone_radius:
                                d_pt_to_ref = point_to_polyline_distance_m(p[0], p[1], canonical_route)
                                if d_pt_to_ref > max_allowed_dev:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=(
                                            f"Client route geometry contains vertex departing by {d_pt_to_ref:.1f}m from waypoint {i + 1} "
                                            f"filleted track (radius {act_r:.0f}m, allowed: <= {max_allowed_dev:.1f}m). "
                                            "Omit 'route' to auto-generate certified filleted trajectory, or supply a route conforming to turn arcs."
                                        )
                                    )

                    wp_cur["effective_turn_radius_m"] = geom["effective_radius_m"]
                    if not geom.get("is_feasible", True):
                        wp_cur["maneuver_feasible"] = False
                        is_safe_claim = False
                        safety_status_claim = "CRITICAL_HAZARD"

        # Canonical filleted track is the Single Source of Truth verified by ECDIS safety engine
        route_coords = canonical_route
        draft = req.draft
        speed_knots = req.speed_knots
        ukc = req.ukc
        block_coefficient = req.block_coefficient
        tide_offset_m = req.tide_offset_m
        wave_height_m = req.wave_height_m
        turning_radius_m = req.turning_radius_m
        beam_m = req.beam_m
        gm_m = req.gm_m
        exposed_waters = req.exposed_waters
        strict_dukc = req.strict_dukc
        is_safe_claim = req.is_safe
        safety_status_claim = req.safety_status
        safety_contour_eval = req.safety_contour

    is_safe, safety_status, alarm_codes, verified_safety_depth = _verify_export_safety(
        waypoints=wps_dicts,
        route_coords=route_coords,
        draft=draft,
        speed_knots=speed_knots,
        ukc=ukc,
        safety_contour=safety_contour_eval,
        block_coefficient=block_coefficient,
        tide_offset_m=tide_offset_m,
        wave_height_m=wave_height_m,
        turning_radius_m=turning_radius_m,
        beam_m=beam_m,
        gm_m=gm_m,
        exposed_waters=exposed_waters,
        strict_dukc=strict_dukc,
        is_safe_claim=is_safe_claim,
        safety_status_claim=safety_status_claim,
    )

    params = {
        "draft": draft,
        "speed_knots": speed_knots,
        "ukc": ukc,
        "route_name": req.route_name or "SeaPath_Passage_Plan",
        "safety_contour": verified_safety_depth,
    }
    return wps_dicts, route_coords, params, is_safe, safety_status, alarm_codes


@app.post("/api/export/gpx")
def export_gpx(req: ExportRouteRequest, response: Response):
    """Generates and downloads standard GPX 1.1 file for GPS and marine chart plotters."""
    wps_dicts, route_coords, params, is_safe, safety_status, alarm_codes = _prepare_export_data(req)
    if not is_safe and not req.allow_unverified:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot export hazardous passage plan: ECDIS safety verification failed (status: {safety_status}). "
                f"Alarms detected: {alarm_codes or ['UNVERIFIED_HAZARD']}. "
                "Set allow_unverified=true to export with mandatory warning annotations."
            )
        )

    xml_content = export_to_gpx(
        waypoints=wps_dicts,
        route_coords=route_coords,
        draft=params["draft"],
        speed_knots=params["speed_knots"],
        ukc=params["ukc"],
        route_name=params["route_name"],
        safety_contour=params["safety_contour"],
        safety_status=safety_status,
        is_safe=is_safe,
    )
    safe_name = _sanitize_filename(params["route_name"])
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.gpx"'}
    if not is_safe:
        headers["X-SeaPath-Route-Status"] = "DEMO_UNVERIFIED" if safety_status == "DEMO_UNVERIFIED" else "UNVERIFIED_HAZARD"
    else:
        headers["X-SeaPath-Route-Status"] = safety_status
    return Response(
        content=xml_content,
        media_type="application/gpx+xml",
        headers=headers,
    )


@app.post("/api/export/rtz")
def export_rtz(req: ExportRouteRequest, response: Response):
    """Generates and downloads standard RTZ 1.1 (IEC 61174) XML for commercial ECDIS systems."""
    wps_dicts, route_coords, params, is_safe, safety_status, alarm_codes = _prepare_export_data(req)
    if not is_safe and not req.allow_unverified:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot export hazardous passage plan: ECDIS safety verification failed (status: {safety_status}). "
                f"Alarms detected: {alarm_codes or ['UNVERIFIED_HAZARD']}. "
                "Set allow_unverified=true to export with mandatory warning annotations."
            )
        )

    xml_content = export_to_rtz(
        waypoints=wps_dicts,
        draft=params["draft"],
        speed_knots=params["speed_knots"],
        ukc=params["ukc"],
        route_name=params["route_name"],
        safety_contour=params["safety_contour"],
        safety_status=safety_status,
        is_safe=is_safe,
    )
    safe_name = _sanitize_filename(params["route_name"])
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.rtz"'}
    if not is_safe:
        headers["X-SeaPath-Route-Status"] = "DEMO_UNVERIFIED" if safety_status == "DEMO_UNVERIFIED" else "UNVERIFIED_HAZARD"
    else:
        headers["X-SeaPath-Route-Status"] = safety_status
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers=headers,
    )


@app.post("/api/bathymetry/upload-sonar")
def upload_sonar_data(req: SonarUploadRequest):
    """
    Ingest, parse, and interpolate custom echo sounder data (CSV, NMEA, GPX).
    Extracts high-resolution isobaths and fuses soundings into the active navigation grid.
    """
    if not req.raw_content or not req.raw_content.strip():
        raise HTTPException(status_code=400, detail="Отсутствует содержимое файла промеров эхолота.")

    soundings = parse_sonar_payload(req.raw_content, req.filename or "sounder_log.csv")
    if not soundings:
        raise HTTPException(
            status_code=422,
            detail="Не удалось извлечь валидные координаты и глубины. Проверьте формат CSV, NMEA или GPX."
        )

    # 1. 2D IDW surface interpolation & vector isobath contouring
    interp_result = interpolate_sonar_bathymetry(
        soundings=soundings,
        grid_size=req.grid_size or 70,
        power=req.power or 2.0,
        search_radius_m=req.search_radius_m or 1200.0,
    )

    # 2. Fuse into active navigation grids (IHO S-44 Shoal-bias rule)
    updated_full = fuse_soundings_into_grid(full_grid, soundings, is_dredged=bool(req.is_dredged))
    updated_routing = 0
    if routing_grid:
        updated_routing = fuse_soundings_into_grid(routing_grid, soundings, is_dredged=bool(req.is_dredged))

    interp_result["statistics"]["cells_fused_into_grid"] = updated_full
    active_wb = waterbody_mgr.get_active()
    for s in soundings:
        active_wb.live_track_points.append({"timestamp": time.time(), "lat": s["lat"], "lon": s["lon"], "depth_m": s["depth_m"], "sog_knots": 0.0, "cog_deg": 0.0})
    waterbody_mgr.save_survey(active_wb.id)
    interp_result["statistics"]["total_persistent_points"] = len(active_wb.live_track_points)
    interp_result["statistics"]["routing_cells_updated"] = updated_routing

    return interp_result


@app.post("/api/passage-plan")
def create_passage_plan(req: PassagePlanRequest):
    """
    Constructs an official IMO Resolution A.893(21) Passage Plan document
    with complete waypoints log, DUKC hydrodynamics, and fuel budget.
    """
    ports_path = _BASE_DIR / "data" / "ports.json"
    ports_db = []
    if ports_path.exists():
        with open(ports_path, "r", encoding="utf-8") as f:
            ports_db = json.load(f)

    anc_path = _BASE_DIR / "data" / "anchorages.json"
    anc_db = []
    if anc_path.exists():
        with open(anc_path, "r", encoding="utf-8") as f:
            anc_db = json.load(f)

    custom_params = {
        "draft": req.draft,
        "speed_knots": req.speed_knots,
        "ukc": req.ukc,
        "wave_height_m": req.wave_height_m,
        "tide_offset_m": req.tide_offset_m,
        "xtd_m": req.xtd_m,
    }
    if req.fuel_burn_l_h is not None:
        custom_params["fuel_burn_l_h"] = req.fuel_burn_l_h

    plan = generate_passage_plan(
        route_data=req.route_data,
        vessel_profile=req.vessel_profile or "yacht",
        vessel_name=req.vessel_name or "M/V SeaPath",
        custom_params=custom_params,
        ports_db=ports_db,
        anchorages_db=anc_db,
    )
    return plan


# ---------------------------------------------------------------------------
# Multi-Waterbody & Real Inland / Coastal Navigation Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/waterbodies")
def list_waterbodies():
    """List all registered and custom navigation water bodies (bays, rivers, lakes, seas)."""
    return waterbody_mgr.list_all()


@app.post("/api/waterbody/activate")
def activate_waterbody(req: ActivateWaterbodyRequest):
    """Switch active navigation water body and re-center chartplotter grid."""
    wb = activate_waterbody_instance(req.waterbody_id)
    if not wb:
        raise HTTPException(status_code=404, detail=f"Водоем с идентификатором '{req.waterbody_id}' не найден.")
    return {
        "success": True,
        "active_waterbody": wb.to_dict(),
        "bounds": wb.bounds,
        "center": wb.center,
        "zoom": wb.zoom,
        "ports": wb.ports,
        "anchorages": wb.anchorages,
    }


@app.post("/api/waterbody/create")
def create_waterbody(req: CreateWaterbodyRequest):
    """
    Create a new high-resolution localized water body project for ANY lake, river, or bay in the world.
    Extracts water boundaries and IDW interpolates depths from survey soundings.
    """
    wb = waterbody_mgr.create_custom_waterbody(
        name=req.name,
        bbox=req.bbox,
        soundings=req.soundings,
        category=req.category or "lake"
    )
    activate_waterbody_instance(wb.id)
    return {
        "success": True,
        "created_waterbody": wb.to_dict(),
        "bounds": wb.bounds,
        "center": wb.center,
        "zoom": wb.zoom,
        "ports": wb.ports,
        "anchorages": wb.anchorages,
    }


# ---------------------------------------------------------------------------
# Crimea Autonomous Hydrographic & Navigational Dataset Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/crimea/summary")
def get_crimea_dataset_summary():
    """Summary of complete authoritative Crimea hydrographic dataset."""
    return waterbody_mgr.get_crimea_summary()


@app.get("/api/crimea/lighthouses")
def get_crimea_lighthouses():
    """Crimean lighthouses and major navigation lights with light character and sectors."""
    return waterbody_mgr.get_crimea_lighthouses()


@app.get("/api/crimea/anchorages")
def get_crimea_anchorages():
    """Official Crimean anchorages, roadsteads, and wind shelters."""
    return waterbody_mgr.get_crimea_anchorages()


@app.get("/api/crimea/piers")
def get_crimea_piers(
    min_lon: Optional[float] = None,
    min_lat: Optional[float] = None,
    max_lon: Optional[float] = None,
    max_lat: Optional[float] = None,
    limit: int = 500
):
    """Piers, marinas, breakwaters, and docks in Crimea."""
    return waterbody_mgr.get_crimea_piers(
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        limit=limit
    )


@app.get("/api/geo/search")
def search_geographic_location(q: str, viewbox: Optional[str] = None):
    """
    Maritime and inland geocoding search proxy.
    Searches both pre-indexed local bathymetric models and OpenStreetMap Nominatim.
    Prioritizes local regional waterbodies and applies smart category tagging.
    """
    if not q or not q.strip():
        return []

    results = []

    # 1. Search local pre-built catalog and presets first
    local_matches = waterbody_mgr.search_local_catalog(q)
    results.extend(local_matches)

    # 2. Query OpenStreetMap Nominatim for global coverage
    query = q.strip()
    q_clean = query.lower().replace("ё", "е")
    for pfx in ["река ", "р. ", "озеро ", "оз. ", "бухта ", "б. ", "залив ", "пролив ", "вдхр. ", "водохранилище ", "river ", "lake ", "bay "]:
        if q_clean.startswith(pfx):
            q_clean = q_clean[len(pfx):].strip()
            break

    vb_param = f"&viewbox={viewbox}&bounded=0" if viewbox else ""
    encoded = urllib.parse.quote(q_clean)
    url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&addressdetails=1&limit=12{vb_param}"
    req = urllib.request.Request(url, headers={"User-Agent": "SeaPath-Marine-Navigator/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                bbox_str = item.get("boundingbox", [])
                if len(bbox_str) == 4:
                    s, n, w, e = [float(x) for x in bbox_str]
                    bbox = [w, s, e, n]
                else:
                    lat_f, lon_f = float(item["lat"]), float(item["lon"])
                    bbox = [lon_f - 0.05, lat_f - 0.05, lon_f + 0.05, lat_f + 0.05]

                osm_type = str(item.get("type", "")).lower()
                osm_class = str(item.get("class", "")).lower()
                if osm_type in ["river", "stream", "canal", "waterway", "drain"] or osm_class == "waterway":
                    cat = "river"
                elif osm_type in ["bay", "harbour", "harbor", "gulf", "fjord", "cove", "sea", "strait"] or osm_class in ["bay", "harbour"]:
                    cat = "bay"
                elif osm_type in ["lake", "reservoir", "pond", "water", "basin"] or osm_class in ["water", "natural"]:
                    cat = "lake"
                else:
                    cat = "river" if "рек" in query.lower() else "lake"

                raw_name = item.get("name") or item.get("display_name", "").split(",")[0]
                parts = [p.strip() for p in item.get("display_name", "").split(",") if p.strip()]
                region_str = ""
                if len(parts) > 1:
                    region_str = f" ({parts[1]}, {parts[-1]})"
                title = f"{raw_name}{region_str}"

                # Avoid duplicate if same area already in results
                c_lat, c_lon = float(item["lat"]), float(item["lon"])
                if any(abs(r.get("lat", 0) - c_lat) < 0.01 and abs(r.get("lon", 0) - c_lon) < 0.01 for r in results):
                    continue

                results.append({
                    "name": title,
                    "display_name": item.get("display_name"),
                    "lat": c_lat,
                    "lon": c_lon,
                    "bbox": bbox,
                    "category": cat,
                    "is_preset": False
                })
    except Exception:
        pass

    return results


@app.post("/api/telemetry/live-point")
def record_live_telemetry_point(req: LiveTelemetryRequest):
    """
    Ingest live GPS and echo sounder telemetry from vessel in real-time (Chartplotter / Quickdraw mode).
    Fuses measured depth into active grid and returns anti-grounding clearance and warnings.
    """
    res = waterbody_mgr.log_live_telemetry(
        lat=req.lat,
        lon=req.lon,
        depth_m=req.depth_m,
        sog_knots=req.sog_knots or 0.0,
        cog_deg=req.cog_deg or 0.0
    )
    return res


@app.get("/api/telemetry/live-track")
def get_live_track():
    """
    Return GeoJSON FeatureCollection of recorded vessel sounding points and track line.
    """
    wb = waterbody_mgr.get_active()
    points = wb.live_track_points
    features = []

    if len(points) >= 2:
        line_coords = [[p["lon"], p["lat"]] for p in points]
        features.append({
            "type": "Feature",
            "properties": {"role": "vessel_track_line"},
            "geometry": {"type": "LineString", "coordinates": line_coords}
        })

    for p in points:
        features.append({
            "type": "Feature",
            "properties": {
                "role": "live_sounding_point",
                "depth_m": p.get("depth_m"),
                "sog_knots": p.get("sog_knots"),
                "cog_deg": p.get("cog_deg")
            },
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}
        })

    return {"type": "FeatureCollection", "features": features}


@app.post("/api/surveys/save")
def save_active_survey(waterbody_id: Optional[str] = None):
    """
    Persist current hydrographic survey soundings and boat track to disk in data/surveys/.
    """
    return waterbody_mgr.save_survey(waterbody_id)


@app.get("/api/surveys/status")
def get_surveys_status():
    """
    Get inventory of all persisted surveys and current active survey sounding count.
    """
    return waterbody_mgr.get_surveys_status()


@app.post("/api/surveys/clear")
def clear_active_survey(waterbody_id: Optional[str] = None):
    """
    Clear recorded survey track and delete persisted survey file for specified waterbody.
    """
    return waterbody_mgr.clear_survey(waterbody_id)


class NmeaFeedRequest(BaseModel):
    sentence: str = Field(description="Raw NMEA 0183 sentence string, e.g. $SDDPT,12.4,0.0*5E")
    source: Optional[str] = Field(default="HTTP_FEED", description="Source identifier")


@app.get("/api/nmea/status")
def get_nmea_status():
    """
    Return diagnostic status of NMEA 0183 listener, physical packet counts, and vessel fix.
    """
    return nmea_mgr.get_status()


@app.post("/api/nmea/feed")
def feed_nmea_sentence(req: NmeaFeedRequest):
    """
    Inject raw NMEA 0183 sentence into the engine (for USB/serial relays and testing).
    """
    success = nmea_mgr.receiver.feed_sentence(req.sentence, source_tag=req.source or "HTTP_FEED")
    return {
        "accepted": success,
        "vessel_state": nmea_mgr.receiver.state.to_dict()
    }


@app.websocket("/api/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """
    Real-time WebSocket feed streaming instantaneous NMEA vessel telemetry
    (GPS fix, SOG, COG, depth, water temp, sensor health) to chartplotter UI.
    """
    await websocket.accept()
    connected_websockets.add(websocket)
    try:
        initial_st = nmea_mgr.receiver.state.to_dict()
        await websocket.send_text(json.dumps({"type": "vessel_telemetry", "data": initial_st}))
        while True:
            msg = await websocket.receive_text()
    except WebSocketDisconnect:
        connected_websockets.discard(websocket)
    except Exception:
        connected_websockets.discard(websocket)



# ---------------------------------------------------------------------------
# Static Web Frontend
# ---------------------------------------------------------------------------
_FRONTEND_DIR = _BASE_DIR / "frontend"

if _FRONTEND_DIR.exists():
    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def serve_index():
        return FileResponse(_FRONTEND_DIR / "index.html")

    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")
