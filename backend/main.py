"""
SeaPath Navigation Engine - API Gateway
=======================================
FastAPI service providing bathymetric passage planning, fairway optimization,
and real-time under-keel clearance (UKC) analysis.
"""

from collections import deque
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.core.grid import BathymetricGrid
from backend.core.router import Router
from backend.core.fairways import rasterize_fairway_weights, load_fairways_geojson
from backend.core.export import export_to_gpx, export_to_rtz
from backend.core.safety import generate_safety_corridor_polygon, verify_ecdis_route_safety
from backend.core.dukc import compute_dukc_budget, calculate_tidal_window, compute_search_budget, calculate_turning_heel
from backend.core.bottleneck import analyze_route_bottlenecks
from backend.core.kinematics import (
    generate_turn_arcs_feature_collection,
    calculate_bearing_deg,
    calculate_distance_m,
)
import math

app = FastAPI(
    title="SeaPath ECDIS Engine",
    description="Maritime routing and bathymetric passage planning engine",
    version="1.0.0",
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
_RAW_DATA_DIR = _BASE_DIR / "data" / "raw"
_GEOTIFF_PATH_ENV = os.environ.get("SEAPATH_GEOTIFF_PATH")
_GEOTIFF_PATH = Path(_GEOTIFF_PATH_ENV) if _GEOTIFF_PATH_ENV else (_RAW_DATA_DIR / "E8_2024.tif")
_IS_PRODUCTION = os.environ.get("SEAPATH_ENV", "").strip().lower() == "production"

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

# 1. Optional local high-resolution soundings fusion (before downsampling)
_LOCAL_SURVEY_CSV = _RAW_DATA_DIR / "yevpatoriya_channel.csv"
if _LOCAL_SURVEY_CSV.exists():
    full_grid.fuse_csv_data(_LOCAL_SURVEY_CSV)

# 2. Downsample for routing AFTER soundings fusion
if not is_synthetic_mode and _GEOTIFF_PATH.exists():
    routing_grid = full_grid.downsample(factor=10)
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

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class RouteRequest(BaseModel):
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
    # beam_m=None → оценка max(3.0, 2.5*draft) внутри calculate_turning_heel.
    # Явно заданный бим точнее оценки и всегда в приоритете.
    beam_m: Optional[float] = Field(default=None, description="Vessel beam in meters (None = estimate 2.5*draft)")
    # exposed_waters=False → укрытая вода, волна 0.35*Hs; True → открытое
    # море, 0.50*Hs по PIANC. Дефолт sheltered, чтобы не ужесточать резко.
    exposed_waters: bool = Field(default=False, description="Exposed coastal waters flag for wave allowance (PIANC 0.50*Hs vs 0.35*Hs)")
    # strict_dukc=True → порог поиска включает squat+крен+волну (безопасно).
    # False → legacy только squat, для A/B-сравнения и отката.
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
        return v


class ExportWaypoint(BaseModel):
    lat: Optional[float] = None
    latitude: Optional[float] = None
    lon: Optional[float] = None
    lng: Optional[float] = None
    longitude: Optional[float] = None
    turn_radius_m: Optional[float] = Field(default=150.0, ge=0.0)
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

    def get_lat(self) -> float:
        v = self.lat if self.lat is not None else self.latitude
        if v is None:
            raise ValueError("Waypoint latitude is required.")
        return float(v)

    def get_lon(self) -> float:
        v = self.lon if self.lon is not None else (self.lng if self.lng is not None else self.longitude)
        if v is None:
            raise ValueError("Waypoint longitude is required.")
        return float(v)

    @field_validator("latitude", "lat")
    @classmethod
    def check_lat(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (-90.0 <= v <= 90.0):
            raise ValueError("Latitude out of valid geographic range [-90, 90].")
        return v

    @field_validator("longitude", "lng", "lon")
    @classmethod
    def check_lon(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (-180.0 <= v <= 180.0):
            raise ValueError("Longitude out of valid geographic range [-180, 180].")
        return v


class ExportRouteRequest(BaseModel):
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
_SAVED_PLANS: Dict[str, Dict[str, Any]] = {}

def _store_passage_plan(plan_id: str, plan_data: Dict[str, Any]) -> None:
    """Stores verified passage plan in memory (LRU cap of 200 items)."""
    if len(_SAVED_PLANS) > 200:
        oldest_k = next(iter(_SAVED_PLANS))
        _SAVED_PLANS.pop(oldest_k, None)
    _SAVED_PLANS[plan_id] = plan_data


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

            act_r = wp.get("turn_radius_m", req.turning_radius_m)
            if turn_ang >= 0.5 and act_r > 10.0 and req.speed_knots > 0.0:
                rot = (5556.0 / math.pi) * (req.speed_knots / act_r)
                wp["rot_deg_min"] = round(rot if diff >= 0.0 else -rot, 1)
                rad_half = math.radians(turn_ang / 2.0)
                v_ms = req.speed_knots * 0.514444
                t_dist = act_r * math.tan(rad_half)
                wp["wop_distance_m"] = round(t_dist + v_ms * 12.0, 1)
            else:
                wp["rot_deg_min"] = 0.0
                wp["wop_distance_m"] = 0.0
        else:
            wp["turn_angle_deg"] = 0.0
            wp["rot_deg_min"] = 0.0
            wp["wop_distance_m"] = 0.0

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

    total_dist_nm = round(cum_dist_m / 1852.0, 2)
    eta_hours = round(total_dist_nm / req.speed_knots, 2) if req.speed_knots > 0 else None
    min_clearance = min((p["clearance"] for p in combined_profile), default=0.0)

    # 1. Safety Corridor (XTD)
    corridor_feature = generate_safety_corridor_polygon(
        combined_route,
        port_xtd_m=req.port_xtd_m,
        stbd_xtd_m=req.stbd_xtd_m,
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
    for idx, wp in enumerate(waypoints):
        wp_lat = wp.get_lat()
        wp_lon = wp.get_lon()
        min_dev, proj_dist = project_onto_route(wp_lon, wp_lat)
        if min_dev > max_lateral_dev_m:
            raise HTTPException(
                status_code=400,
                detail=f"Passage plan geometry mismatch: Waypoint WP{idx + 1} ({wp_lon:.4f}, {wp_lat:.4f}) deviates by {min_dev:.1f}m from route track (> {max_lateral_dev_m:.0f}m tolerance)."
            )
        # Monotonic progression with 25m tolerance for fillet arc tangent points
        if proj_dist < last_proj_dist - 25.0:
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
) -> Tuple[bool, str, List[str]]:
    """
    Performs server-side ECDIS safety verification on passage plan prior to export:
    - Calculates canonical search budget (squat, heel, wave allowance, tide offset).
    - Prevents client-supplied safety_contour from weakening canonical minimum depth threshold.
    - Rejects synthetic demonstration bathymetry as uncertified for navigation.
    - Samples depths along route track and evaluates full 2D corridor (XTD) area swath.
    Returns (is_safe, safety_status, alarm_codes).
    """
    if full_grid.is_synthetic or is_synthetic_mode:
        return False, "DEMO_UNVERIFIED", ["ALM-10-SYNTHETIC-DATA-NON-NAVIGABLE"]

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
    effective_safety_depth = max(safety_contour or 0.0, canonical_min_depth)
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

    port_xtd_m = float(waypoints[0].get("port_xtd_nm", 0.10) or 0.10) * 1852.0 if waypoints else 185.2
    stbd_xtd_m = float(waypoints[0].get("stbd_xtd_nm", 0.10) or 0.10) * 1852.0 if waypoints else 185.2

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

    client_flagged_unsafe = (is_safe_claim is False) or (safety_status_claim == "CRITICAL_HAZARD")
    is_safe = bool(safety_audit["is_safe"] and not client_flagged_unsafe)
    status = "SAFE_PASSED" if is_safe else "CRITICAL_HAZARD"
    alarm_codes = [a["code"] for a in safety_audit.get("alarms", [])]
    return is_safe, status, alarm_codes


def _prepare_export_data(req: ExportRouteRequest) -> Tuple[List[Dict[str, Any]], List[List[float]], Dict[str, Any], bool, str, List[str]]:
    if req.plan_id:
        plan = _SAVED_PLANS.get(req.plan_id)
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
    else:
        # Validate waypoints/route coupling
        _validate_route_waypoints_coupling(req.waypoints, req.route)
        wps_dicts = []
        for wp in req.waypoints:
            d = wp.model_dump(exclude_none=True)
            d["lat"] = wp.get_lat()
            d["lon"] = wp.get_lon()
            wps_dicts.append(d)
        if req.route and len(req.route) >= 2:
            route_coords = req.route
        else:
            route_coords = [[w["lon"], w["lat"]] for w in wps_dicts]
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

    is_safe, safety_status, alarm_codes = _verify_export_safety(
        waypoints=wps_dicts,
        route_coords=route_coords,
        draft=draft,
        speed_knots=speed_knots,
        ukc=ukc,
        safety_contour=req.safety_contour,
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
        "safety_contour": req.safety_contour,
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
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers=headers,
    )


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
