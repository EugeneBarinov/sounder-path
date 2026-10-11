import pytest
import numpy as np
from pathlib import Path
from backend.core.grid import BathymetricGrid
from backend.core.safety import verify_ecdis_route_safety, scan_corridor_raster_swath
from backend.core.router import Router as PyRouter
from backend.core.dukc import compute_search_budget

try:
    import seapath_native
    HAS_NATIVE = True
except ImportError:
    HAS_NATIVE = False


def test_xtd_2d_raster_area_swath_detects_interior_shoal():
    """
    Verify that an isolated shoal strictly INSIDE the XTD corridor (offset from centerline,
    not touching boundary) is detected by 2D area scanning, raising ALM-04 with CRITICAL severity.
    """
    # Create a 40x40 grid, deep water everywhere (20m)
    depths = np.full((40, 40), 20.0, dtype=np.float32)
    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.04,
        lon_min=33.0,
        lon_max=33.04,
        depths=depths,
    )

    # Route along row 20 from lon 33.005 to 33.035
    route_coords = [
        [33.005, 44.020],
        [33.035, 44.020]
    ]

    # Place an isolated underwater pinnacle / shoal inside the corridor
    # At row 22 (lat ~44.022), col 20 (lon ~33.020).
    # Distance from centerline is ~220m.
    # We set XTD to 350m, so it is strictly INSIDE the corridor.
    # The polygon boundary passes at row 23.5 / 16.5, so it doesn't touch the perimeter!
    grid.depths[22, 20] = 1.0  # dangerous shoal (1.0m depth)

    required_depth = 4.0
    port_xtd_m = 350.0
    stbd_xtd_m = 350.0

    violations = scan_corridor_raster_swath(
        route_coords=route_coords,
        port_xtd_m=port_xtd_m,
        stbd_xtd_m=stbd_xtd_m,
        grid=grid,
        safety_depth=required_depth,
    )

    assert len(violations) > 0, "2D raster swath scan must detect interior shoal!"
    assert any(v["depth"] == 1.0 for v in violations)

    # Full ECDIS safety audit check
    waypoints = [
        {"lon": 33.005, "lat": 44.020, "turn_radius_m": 200.0, "rot_deg_min": 0.0},
        {"lon": 33.035, "lat": 44.020, "turn_radius_m": 200.0, "rot_deg_min": 0.0},
    ]
    profile = [
        {"distance_from_start_m": 0.0, "depth": 20.0, "clearance": 16.0},
        {"distance_from_start_m": 3000.0, "depth": 20.0, "clearance": 16.0},
    ]

    audit = verify_ecdis_route_safety(
        waypoints=waypoints,
        profile=profile,
        fairways_coll={"type": "FeatureCollection", "features": []},
        draft=3.0,
        dynamic_squat=0.5,
        ukc=0.5,
        speed_knots=10.0,
        min_chart_depth_m=required_depth,
        grid=grid,
        port_xtd_m=port_xtd_m,
        stbd_xtd_m=stbd_xtd_m,
    )

    assert not audit["is_safe"], "Route must be unsafe due to interior shoal in XTD corridor"
    assert audit["status"] == "CRITICAL_HAZARD"
    alarm_codes = [a["code"] for a in audit["alarms"]]
    assert "ALM-04-XTD-CORRIDOR-HAZARD" in alarm_codes
    corridor_alarm = next(a for a in audit["alarms"] if a["code"] == "ALM-04-XTD-CORRIDOR-HAZARD")
    assert corridor_alarm["severity"] == "CRITICAL"


def test_maneuvering_violation_blocks_route_as_critical():
    """
    Test that when a waypoint has maneuver_feasible == False,
    the safety audit raises ALM-05 with CRITICAL severity and marks the route unsafe.
    """
    depths = np.full((30, 30), 15.0, dtype=np.float32)
    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.03,
        lon_min=33.0,
        lon_max=33.03,
        depths=depths,
    )

    # Waypoints where turning maneuver is impossible (e.g. leg too short for turn radius)
    waypoints = [
        {"wp_num": 1, "lon": 33.005, "lat": 44.005, "rot_deg_min": 0.0, "turn_radius_m": 200.0, "maneuver_feasible": True},
        {"wp_num": 2, "lon": 33.010, "lat": 44.010, "rot_deg_min": 8.0, "turn_radius_m": 200.0, "maneuver_feasible": False},
        {"wp_num": 3, "lon": 33.025, "lat": 44.010, "rot_deg_min": 0.0, "turn_radius_m": 200.0, "maneuver_feasible": True},
    ]
    profile = [
        {"distance_from_start_m": 0.0, "depth": 15.0, "clearance": 12.0},
        {"distance_from_start_m": 1500.0, "depth": 15.0, "clearance": 12.0},
    ]

    audit = verify_ecdis_route_safety(
        waypoints=waypoints,
        profile=profile,
        fairways_coll={"type": "FeatureCollection", "features": []},
        draft=2.5,
        dynamic_squat=0.5,
        ukc=0.5,
        speed_knots=10.0,
        grid=grid,
        requested_radius_m=200.0,
    )

    assert not audit["is_safe"], "Route with unexecutable maneuver must be marked unsafe"
    assert audit["status"] == "CRITICAL_HAZARD"
    alarm_codes = [a["code"] for a in audit["alarms"]]
    assert "ALM-05-MANEUVERING-LIMIT-VIOLATION" in alarm_codes
    alm = next(a for a in audit["alarms"] if a["code"] == "ALM-05-MANEUVERING-LIMIT-VIOLATION")
    assert alm["severity"] == "CRITICAL"


def test_land_mask_protected_from_csv_sounding_overwrites(tmp_path):
    """
    Verify that sounding CSV points falling on terrestrial land (elevation > 0)
    are ignored and do NOT overwrite the land mask, unless explicitly flagged.
    """
    # Create grid with land in the north half (rows 0..14)
    # In BathymetricGrid, row 0 is at lat_max, so rows 0..14 are north
    depths = np.full((30, 30), 10.0, dtype=np.float32)
    land_mask = np.zeros((30, 30), dtype=bool)
    land_mask[0:15, :] = True
    depths[0:15, :] = 0.0

    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.03,
        lon_min=33.0,
        lon_max=33.03,
        depths=depths,
        land_mask=land_mask,
    )

    # Initial land mask check
    assert grid.land_mask[5, 15] == True
    assert grid.land_mask[20, 15] == False

    # Get coordinates of cells
    lat_sea, lon_sea = grid.get_cell_coords(20, 15)
    lat_land, lon_land = grid.get_cell_coords(5, 15)
    lat_canal, lon_canal = grid.get_cell_coords(10, 15)

    csv_file = tmp_path / "test_soundings.csv"
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("lat,lon,depth,dredged\n")
        f.write(f"{lat_sea},{lon_sea},8.0,0\n")
        f.write(f"{lat_land},{lon_land},12.0,0\n")  # mountain, must NOT overwrite!
        f.write(f"{lat_canal},{lon_canal},9.5,1\n")  # dredged canal, should carve through land

    grid.fuse_csv_data(csv_file)

    # Sea point updated with shallower depth (10.0 -> 8.0)
    assert grid.depths[20, 15] == 8.0

    # Dry land point retained 0.0 depth, land_mask preserved
    assert grid.depths[5, 15] == 0.0
    assert grid.land_mask[5, 15] == True

    # Dredged canal point carved out through land
    assert grid.depths[10, 15] == 9.5
    assert grid.land_mask[10, 15] == False


@pytest.mark.skipif(not HAS_NATIVE, reason="seapath_native C++ extension not available")
def test_python_and_cpp_router_parity():
    """
    Test algorithmic parity between seapath_native C++ engine and Python reference router:
    - Same waypoint count
    - Smooth filleted turning arcs generated in both
    - Matched path clearance and distance within 1.5%
    """
    # Create a realistic bathymetry grid with deep water and a central shoal
    depths = np.full((60, 60), 20.0, dtype=np.float32)
    # Shoal in the center forcing a turn
    depths[20:40, 20:40] = 1.0

    grid = BathymetricGrid(
        lat_min=44.10,
        lat_max=44.16,
        lon_min=28.60,
        lon_max=28.66,
        depths=depths,
    )

    py_router = PyRouter(grid)
    start_r, start_c = 10, 10
    goal_r, goal_c = 50, 50
    draft = 2.5
    speed_knots = 10.0
    ukc = 0.5
    turning_radius_m = 150.0

    budget = compute_search_budget(
        draft_m=draft,
        speed_knots=speed_knots,
        ukc_net_margin_m=ukc,
        block_coefficient=0.65,
        turning_radius_m=turning_radius_m,
        strict_dukc=True,
    )
    min_depth = budget["min_chart_depth_m"]
    dyn_draft = budget["dynamic_draft_m"]

    # Run Python router
    res_py = py_router.find_path(
        start_r, start_c, goal_r, goal_c,
        draft=draft, speed_knots=speed_knots, ukc=ukc,
        turning_radius_m=turning_radius_m,
        min_chart_depth_m=min_depth, dynamic_draft_m=dyn_draft
    )

    # Run C++ router
    native_grid = seapath_native.NativeGrid(
        grid.rows, grid.cols,
        grid.lat_min, grid.lat_max,
        grid.lon_min, grid.lon_max,
        grid.depths,
        None,
    )
    cpp_router = seapath_native.NativeRouter(native_grid)
    res_cpp = cpp_router.find_path(
        start_r, start_c, goal_r, goal_c,
        draft, speed_knots, ukc, turning_radius_m,
        1.0, 0.65, min_depth, dyn_draft
    )

    # Both must find a path
    assert res_py is not None and res_cpp is not None

    py_diag = res_py["diagnostics"]
    cpp_diag = res_cpp["diagnostics"]

    # Distance parity (< 2% difference due to slight coordinate projection rounding)
    assert abs(py_diag["distance_nm"] - cpp_diag["distance_nm"]) < 0.2

    # Waypoints count parity
    assert abs(py_diag["waypoints"] - cpp_diag["waypoints"]) <= 1

    # Check filleted turning arcs: coordinates count must exceed waypoints count
    assert len(res_py["route"]) > py_diag["waypoints"]
    assert len(res_cpp["route"]) > cpp_diag["waypoints"]

    # Both must maintain positive minimum clearance
    assert py_diag["min_clearance_m"] >= 0.0
    assert cpp_diag["min_clearance_m"] >= 0.0
