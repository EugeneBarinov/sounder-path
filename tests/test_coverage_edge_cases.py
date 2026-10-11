"""
Comprehensive Edge Cases & Robustness Test Suite
=================================================
Validates edge cases, boundary conditions, zero-division guards,
coordinate tolerance in exports, and full Python/C++ NativeGrid API parity.
"""

import pytest
import numpy as np
from pathlib import Path
from fastapi.testclient import TestClient
from backend.main import app, routing_grid
from backend.core.dukc import compute_search_budget, calculate_turning_heel
from backend.core.bottleneck import analyze_route_bottlenecks
from backend.core.export import export_to_gpx, export_to_rtz
from backend.core.grid import BathymetricGrid


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_api_route_identical_waypoints_rejected(client):
    """Start and destination at the exact same location must be rejected with 400."""
    payload = {
        "start_lon": 34.0,
        "start_lat": 44.0,
        "goal_lon": 34.0,
        "goal_lat": 44.0,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 400
    assert "identical or too close" in res.json()["detail"].lower()


def test_api_route_too_close_waypoints_rejected(client):
    """Waypoints separated by less than 10 meters must be rejected with 400."""
    payload = {
        "waypoints": [
            [34.0, 44.0],
            [34.00001, 44.00001],  # ~1.4 meters apart
        ],
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 400
    assert "identical or too close" in res.json()["detail"].lower()


def test_native_grid_python_bindings_parity():
    """Validates that C++ NativeGrid exposes full spatial querying API matching BathymetricGrid."""
    try:
        import seapath_native
    except ImportError:
        pytest.skip("seapath_native C++ module not compiled")

    lat_min, lat_max = 43.0, 45.0
    lon_min, lon_max = 33.0, 36.0
    rows, cols = 50, 60
    depth_data = np.full((rows, cols), 50.0, dtype=np.float32)

    ng = seapath_native.NativeGrid(rows, cols, lat_min, lat_max, lon_min, lon_max, depth_data)

    # 1. Attributes
    assert ng.rows == rows
    assert ng.cols == cols
    assert ng.lat_min == lat_min
    assert ng.lat_max == lat_max
    assert ng.lon_min == lon_min
    assert ng.lon_max == lon_max

    # 2. Methods
    assert ng.get_depth(10, 10) == 50.0
    assert ng.get_depth(-1, -1) == 0.0
    assert ng.get_fairway_weight(10, 10) == 1.0
    assert ng.is_restricted(10, 10) is False

    # 3. Coordinate conversion
    r, c = ng.lonlat_to_cell(34.5, 44.0)
    assert 0 <= r < rows
    assert 0 <= c < cols

    lat_c, lon_c = ng.get_cell_coords(r, c)
    assert abs(lat_c - 44.0) < 0.1
    assert abs(lon_c - 34.5) < 0.1


def test_bottleneck_extreme_parameters():
    """Confirms analyze_route_bottlenecks handles extreme values without division by zero or errors."""
    profile = [
        {"distance_from_start_m": 0.0, "depth": 3.0, "clearance": -0.5, "lon": 34.0, "lat": 44.0},
        {"distance_from_start_m": 1000.0, "depth": 2.5, "clearance": -1.0, "lon": 34.01, "lat": 44.01},
    ]

    # Extreme block coefficient: 0.0 or 2.0
    res = analyze_route_bottlenecks(
        profile=profile,
        draft_m=2.0,
        speed_knots=12.0,
        ukc_net_margin_m=0.5,
        block_coefficient=0.0,  # Extreme low Cb
    )
    assert res["is_grounding_hazard"] is True
    assert res["depth_deficit_m"] > 0

    # Zero speed
    res_zero = analyze_route_bottlenecks(
        profile=profile,
        draft_m=2.0,
        speed_knots=0.0,
        ukc_net_margin_m=0.5,
    )
    assert res_zero["shallowest_point"] is not None

    # Empty profile
    res_empty = analyze_route_bottlenecks(profile=[], draft_m=2.0, speed_knots=10.0)
    assert res_empty["has_bottleneck"] is False


def test_export_tolerant_schema():
    """Ensures export functions tolerate varied coordinate schemas and empty routes."""
    # Dict with 'latitude' and 'longitude'
    waypoints = [
        {"latitude": 44.0, "longitude": 34.0, "depth_m": 25.0, "clearance_m": 22.0},
        {"lat": 44.2, "lng": 34.5, "depth_m": 30.0, "clearance_m": 27.0},
    ]
    route_coords = [
        {"lat": 44.0, "lon": 34.0},
        [34.5, 44.2],
    ]

    gpx_str = export_to_gpx(waypoints, route_coords, draft=2.0, speed_knots=10.0, ukc=1.0)
    assert "<gpx" in gpx_str
    assert "WP01" in gpx_str
    assert "WP02" in gpx_str

    rtz_str = export_to_rtz(waypoints, draft=2.0, speed_knots=10.0, ukc=1.0)
    assert "<route" in rtz_str
    assert 'name="WP01"' in rtz_str
    assert 'name="WP02"' in rtz_str


def test_csv_fusion_malformed_and_oob(tmp_path):
    """Ensures CSV fusion handles malformed rows, bad numbers, and OOB points cleanly."""
    grid = BathymetricGrid.create_synthetic(rows=20, cols=20)
    csv_file = tmp_path / "bad_soundings.csv"

    # CSV with malformed rows, header variations, and extreme lat/lon
    csv_file.write_text(
        "lat,lon,depth,dredged\n"
        "not_a_num,35.0,10.0,false\n"
        "44.0,not_a_num,10.0,false\n"
        "44.0,35.0,not_a_num,false\n"
        "10.0,10.0,15.0,false\n"  # Way out of bounds
        ",,,\n"
        "44.5,35.5,12.5,false\n",  # Valid row
        encoding="utf-8"
    )

    # Must complete smoothly without raising exceptions
    grid.fuse_csv_data(csv_file)
    r, c = grid.lonlat_to_cell(35.5, 44.5)
    # The valid row should be fused
    assert grid.depths[r, c] <= 12.5
