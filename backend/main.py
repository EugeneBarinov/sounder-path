"""
SeaPath Navigation Engine - API Gateway
=======================================
FastAPI service providing bathymetric passage planning, fairway optimization,
and real-time under-keel clearance (UKC) analysis.
"""

from collections import deque
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.core.grid import BathymetricGrid
from backend.core.router import Router
from backend.core.fairways import rasterize_fairway_weights, load_fairways_geojson
from backend.core.export import export_to_gpx, export_to_rtz
from backend.core.safety import generate_safety_corridor_polygon, verify_ecdis_route_safety
from backend.core.dukc import compute_dukc_budget, calculate_tidal_window
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
@app.middleware("http")
async def no_cache_header_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/static", "/app.js", "/style.css", "/index.html", "/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

# ---------------------------------------------------------------------------
# Bathymetric Data Initialization
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent
_RAW_DATA_DIR = _BASE_DIR / "data" / "raw"
_GEOTIFF_PATH = _RAW_DATA_DIR / "E8_2024.tif"

if _GEOTIFF_PATH.exists():
    print(f"Loading primary bathymetric grid: {_GEOTIFF_PATH.name}...")
    full_grid = BathymetricGrid.load_from_geotiff(_GEOTIFF_PATH)
    routing_grid = full_grid.downsample(factor=10)
else:
    print(
        f"[DEMO MODE] {_GEOTIFF_PATH.name} not found in data/raw/. "
        "Initialized synthetic regional bathymetry grid for demonstration. "
        "Place operational EMODnet GeoTIFF in data/raw/ for real-world navigation."
    )
    full_grid = BathymetricGrid.create_synthetic()
    routing_grid = full_grid

# Optional local high-resolution soundings fusion
_LOCAL_SURVEY_CSV = _RAW_DATA_DIR / "yevpatoriya_channel.csv"
if _LOCAL_SURVEY_CSV.exists():
    full_grid.fuse_csv_data(_LOCAL_SURVEY_CSV)

# Rasterize navigational fairways & restricted areas (RESARE)
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
    print("[ROUTER] Activated high-performance C++ native navigation core (seapath_native)")
except Exception as _err:
    router = Router(routing_grid)
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


class ExportRouteRequest(BaseModel):
    waypoints: List[Dict[str, Any]]
    route: List[List[float]] = Field(default_factory=list)
    draft: float = 1.2
    speed_knots: float = 10.0
    ukc: float = 0.5
    route_name: Optional[str] = "SeaPath_Passage_Plan"


# ---------------------------------------------------------------------------
# Spatial Utilities
# ---------------------------------------------------------------------------
def _snap_to_navigable(
    r: int,
    c: int,
    grid: BathymetricGrid,
    min_depth: float,
    radius: int = 60,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Breadth-first search to snap shoreline/berth coordinates to the nearest
    navigable fairway node satisfying safety depth constraints and bypassing restricted zones.
    """
    queue = deque([(r, c)])
    visited = {(r, c)}

    while queue:
        cr, cc = queue.popleft()
        if float(grid.depths[cr, cc]) >= min_depth and not grid.is_restricted(cr, cc):
            return cr, cc

        if max(abs(cr - r), abs(cc - c)) >= radius:
            continue

        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < grid.rows and 0 <= nc < grid.cols and (nr, nc) not in visited:
                visited.add((nr, nc))
                queue.append((nr, nc))

    return None, None


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
def health_check():
    return {
        "status": "healthy",
        "dataset_rows": routing_grid.rows,
        "dataset_cols": routing_grid.cols,
    }


@app.get("/api/grid")
def get_grid_coverage():
    """Returns GeoJSON boundary polygon representing available bathymetry coverage."""
    return routing_grid.to_geojson()


@app.get("/api/fairways")
def get_fairways():
    """Returns vector fairways, TSS corridors, and navigational restriction polygons in GeoJSON format."""
    return load_fairways_geojson()


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

    # Dynamic Water Level & PIANC Squat
    effective_ukc_for_search = max(0.05, req.ukc - req.tide_offset_m)
    h_over_t = (req.draft + req.ukc) / req.draft if req.draft > 0 else 0.0
    dynamic_squat = Router.calculate_squat(req.speed_knots, req.block_coefficient, h_over_t)
    min_required_chart_depth = req.draft + dynamic_squat + effective_ukc_for_search

    # Calculate routes along each leg
    combined_route: List[List[float]] = []
    combined_profile: List[Dict[str, Any]] = []
    leg_results: List[Dict[str, Any]] = []
    cum_dist_m = 0.0

    for k in range(len(points) - 1):
        p_start = points[k]
        p_goal = points[k + 1]

        start_r, start_c = routing_grid.lonlat_to_cell(p_start[0], p_start[1])
        goal_r, goal_c = routing_grid.lonlat_to_cell(p_goal[0], p_goal[1])

        start_r, start_c = _snap_to_navigable(start_r, start_c, routing_grid, min_required_chart_depth)
        goal_r, goal_c = _snap_to_navigable(goal_r, goal_c, routing_grid, min_required_chart_depth)

        if start_r is None:
            raise HTTPException(
                status_code=400,
                detail=f"Waypoint {k + 1}: no navigable channel found satisfying safety depth ({min_required_chart_depth:.1f}m)."
            )
        if goal_r is None:
            raise HTTPException(
                status_code=400,
                detail=f"Waypoint {k + 2}: no navigable channel found satisfying safety depth ({min_required_chart_depth:.1f}m)."
            )

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

        # Merge profile samples with cumulative distance and tide-adjusted clearance
        for pt in leg_res["profile"]:
            combined_profile.append({
                "distance_from_start_m": round(cum_dist_m + pt["distance_from_start_m"], 1),
                "depth": pt["depth"],
                "clearance": round(pt["clearance"] + req.tide_offset_m, 2),
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

        if 0 < i < n_wps - 1:
            wp_prev = raw_waypoints[i - 1]
            b_in = calculate_bearing_deg(wp_prev["lat"], wp_prev["lon"], wp["lat"], wp["lon"])
            b_out = wp["leg_bearing_deg"]
            diff = (b_out - b_in + 180.0) % 360.0 - 180.0
            turn_ang = abs(diff)
            wp["turn_angle_deg"] = round(turn_ang, 1)

            if turn_ang >= 0.5 and req.turning_radius_m > 10.0 and req.speed_knots > 0.0:
                rot = (5556.0 / math.pi) * (req.speed_knots / req.turning_radius_m)
                wp["rot_deg_min"] = round(rot if diff >= 0.0 else -rot, 1)
                rad_half = math.radians(turn_ang / 2.0)
                v_ms = req.speed_knots * 0.514444
                wp["wop_distance_m"] = round(req.turning_radius_m * math.tan(rad_half) + v_ms * 12.0, 1)
            else:
                wp["rot_deg_min"] = 0.0
                wp["wop_distance_m"] = 0.0
        else:
            wp["turn_angle_deg"] = 0.0
            wp["rot_deg_min"] = 0.0
            wp["wop_distance_m"] = 0.0

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

    # 3. ECDIS Route Safety Verification (IEC 61174)
    safety_audit = verify_ecdis_route_safety(
        waypoints=combined_waypoints,
        profile=combined_profile,
        fairways_coll=load_fairways_geojson(),
        draft=req.draft,
        dynamic_squat=dynamic_squat,
        ukc=req.ukc,
        speed_knots=req.speed_knots,
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
    )

    # 5. Full PIANC DUKC Budget
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
        gm_m=req.gm_m,
    )

    diagnostics = {
        "distance_m": round(cum_dist_m, 0),
        "distance_nm": total_dist_nm,
        "eta_hours": eta_hours,
        "min_clearance_m": round(min_clearance, 2),
        "dynamic_draft_m": round(req.draft + dynamic_squat, 2),
        "dynamic_squat_m": round(dynamic_squat, 2),
        "waypoints": len(combined_waypoints),
        "tide_offset_m": req.tide_offset_m,
        "wave_height_m": req.wave_height_m,
    }

    return {
        "type": "Feature",
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


@app.post("/api/export/gpx")
def export_gpx(req: ExportRouteRequest):
    """Generates and downloads standard GPX 1.1 file for GPS and marine chart plotters."""
    xml_content = export_to_gpx(
        waypoints=req.waypoints,
        route_coords=req.route,
        draft=req.draft,
        speed_knots=req.speed_knots,
        ukc=req.ukc,
        route_name=req.route_name or "SeaPath_Passage_Plan",
    )
    return Response(
        content=xml_content,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f"attachment; filename={req.route_name or 'passage_plan'}.gpx"},
    )


@app.post("/api/export/rtz")
def export_rtz(req: ExportRouteRequest):
    """Generates and downloads standard RTZ 1.1 (IEC 61174) XML for commercial ECDIS systems."""
    xml_content = export_to_rtz(
        waypoints=req.waypoints,
        draft=req.draft,
        speed_knots=req.speed_knots,
        ukc=req.ukc,
        route_name=req.route_name or "SeaPath_Passage_Plan",
    )
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename={req.route_name or 'passage_plan'}.rtz"},
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
