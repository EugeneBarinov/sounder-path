"""
SeaPath Navigation Engine - API Gateway
=======================================
FastAPI service providing bathymetric passage planning, fairway optimization,
and real-time under-keel clearance (UKC) analysis.
"""

from collections import deque
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.core.grid import BathymetricGrid
from backend.core.router import Router

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

if not _GEOTIFF_PATH.exists():
    raise RuntimeError(
        f"Primary bathymetric dataset missing at {_GEOTIFF_PATH}. "
        "Place the regional GeoTIFF (EMODnet DTM) in data/raw/ before starting."
    )

print(f"Loading primary bathymetric grid: {_GEOTIFF_PATH.name}...")
full_grid = BathymetricGrid.load_from_geotiff(_GEOTIFF_PATH)

# Optional local high-resolution soundings fusion
_LOCAL_SURVEY_CSV = _RAW_DATA_DIR / "yevpatoriya_channel.csv"
if _LOCAL_SURVEY_CSV.exists():
    full_grid.fuse_csv_data(_LOCAL_SURVEY_CSV)

# Downsampled grid for fast heuristic graph search (10x reduction)
routing_grid = full_grid.downsample(factor=10)
print(f"Grid loaded: Full resolution {full_grid.rows}x{full_grid.cols} | Routing {routing_grid.rows}x{routing_grid.cols}")

router = Router(routing_grid)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class RouteRequest(BaseModel):
    start_lon: float = Field(..., description="Departure longitude in decimal degrees")
    start_lat: float = Field(..., description="Departure latitude in decimal degrees")
    goal_lon: float = Field(..., description="Destination longitude in decimal degrees")
    goal_lat: float = Field(..., description="Destination latitude in decimal degrees")
    draft: float = Field(..., gt=0.0, description="Static ship draft in meters")
    speed_knots: float = Field(..., ge=0.0, description="Planned vessel speed in knots")
    ukc: float = Field(..., ge=0.0, description="Required under-keel clearance margin in meters")


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
    navigable fairway node satisfying safety depth constraints.
    """
    queue = deque([(r, c)])
    visited = {(r, c)}

    while queue:
        cr, cc = queue.popleft()
        if float(grid.depths[cr, cc]) >= min_depth:
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


@app.post("/api/route")
def calculate_route(req: RouteRequest):
    """
    Calculates safe passage plan complying with draft, squat, and UKC constraints.
    Returns GeoJSON LineString route with continuous depth soundings profile.
    """
    if not _is_within_coverage(req.start_lon, req.start_lat, routing_grid):
        raise HTTPException(status_code=400, detail="Departure point is outside bathymetry coverage area.")
    if not _is_within_coverage(req.goal_lon, req.goal_lat, routing_grid):
        raise HTTPException(status_code=400, detail="Destination point is outside bathymetry coverage area.")

    dynamic_squat = Router.calculate_squat(req.speed_knots)
    min_required_depth = req.draft + dynamic_squat + req.ukc

    start_r, start_c = routing_grid.lonlat_to_cell(req.start_lon, req.start_lat)
    goal_r, goal_c = routing_grid.lonlat_to_cell(req.goal_lon, req.goal_lat)

    # Berth-to-fairway snapping
    start_r, start_c = _snap_to_navigable(start_r, start_c, routing_grid, min_required_depth)
    goal_r, goal_c = _snap_to_navigable(goal_r, goal_c, routing_grid, min_required_depth)

    if start_r is None:
        raise HTTPException(status_code=400, detail="Departure point: no navigable channel found within safety search radius.")
    if goal_r is None:
        raise HTTPException(status_code=400, detail="Destination point: no navigable channel found within safety search radius.")

    result = router.find_path(
        start_r=start_r,
        start_c=start_c,
        goal_r=goal_r,
        goal_c=goal_c,
        draft=req.draft,
        speed_knots=req.speed_knots,
        ukc=req.ukc,
    )

    if result is None:
        raise HTTPException(status_code=400, detail="No navigable passage found satisfying vessel draft and UKC constraints.")

    distance_nm = result["diagnostics"]["distance_nm"]
    eta_hours = round(distance_nm / max(req.speed_knots, 0.5), 2)
    result["diagnostics"]["eta_hours"] = eta_hours

    return {
        "type": "Feature",
        "properties": result["diagnostics"],
        "geometry": {
            "type": "LineString",
            "coordinates": result["route"],
        },
        "profile": result["profile"],
    }


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
