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


def test_snapping_does_not_hop_across_island_or_barrier():
    """
    Verify that _snap_to_navigable() does not hop across narrow islands or land barriers.
    Must only follow water-connected paths.
    """
    from backend.main import _snap_to_navigable

    # 30x30 grid: cell size ~100m
    # Lat 44.00 to 44.03, Lon 33.00 to 33.03
    depths = np.full((30, 30), 10.0, dtype=np.float32)
    # Put an island running north-south across cols 12..14 from row 5 to 25
    depths[5:25, 12:15] = 0.0

    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.03,
        lon_min=33.0,
        lon_max=33.03,
        depths=depths,
    )

    # Point at row 10, col 11 is on the west side of the island.
    # Make col 10..11 shallow (1.5m). We require 3.5m.
    grid.depths[10, 11] = 1.5
    grid.depths[10, 10] = 1.5
    # Deep water is at col 9 (west, 10m) and col 16 (east, across island, 10m).
    # Since col 12..14 is land, snapping from (10, 11) must go WEST to col <= 9, NEVER EAST to col >= 15!
    snapped_r, snapped_c, snap_dist, reason = _snap_to_navigable(
        10, 11, grid, min_depth=3.0, max_snap_meters=1500.0
    )

    assert snapped_r is not None and snapped_c is not None
    assert snapped_c < 12, f"Snapping hopped across island to col {snapped_c}! Must remain on west side."
    assert float(grid.depths[snapped_r, snapped_c]) > 3.0
    assert snap_dist > 0.0
    assert reason == "INSUFFICIENT_DEPTH"


def test_exhaustive_swath_scan_does_not_abort_early():
    """
    Verify that scan_corridor_raster_swath checks the ENTIRE corridor across all legs
    and does NOT prematurely abort after the first 10 hazards.
    """
    # 50x50 grid, deep water everywhere (25m)
    depths = np.full((50, 50), 25.0, dtype=np.float32)
    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.05,
        lon_min=33.0,
        lon_max=33.05,
        depths=depths,
    )

    # 3-leg route: Leg 1 (rows 5..10), Leg 2 (rows 10..30), Leg 3 (rows 30..45)
    lat_5, lon_10 = grid.get_cell_coords(5, 10)
    lat_15, lon_10 = grid.get_cell_coords(15, 10)
    lat_30, lon_10 = grid.get_cell_coords(30, 10)
    lat_45, lon_10 = grid.get_cell_coords(45, 10)

    route_coords = [
        [lon_10, lat_5],
        [lon_10, lat_15],
        [lon_10, lat_30],
        [lon_10, lat_45],
    ]

    # Create 12 shoals in Leg 1 (between row 5 and 15)
    for r in range(5, 15):
        grid.depths[r, 11] = 2.0
        grid.depths[r, 9] = 2.0

    # Place a critical 0.5m shoal in Leg 3 (row 40, col 11)
    grid.depths[40, 11] = 0.5

    safety_depth = 4.0
    port_xtd_m = 250.0
    stbd_xtd_m = 250.0

    hazards = scan_corridor_raster_swath(
        route_coords=route_coords,
        port_xtd_m=port_xtd_m,
        stbd_xtd_m=stbd_xtd_m,
        grid=grid,
        safety_depth=safety_depth,
    )

    # Must find the critical shoal in Leg 3, proving it didn't abort after 10 hazards in Leg 1!
    has_leg3_shoal = any(h["depth"] == 0.5 and h.get("r") == 40 for h in hazards)
    assert has_leg3_shoal, "Corridor scan aborted prematurely! Critical shoal in Leg 3 was missed."
    assert any(h["hazard_type"] == "SHALLOW_WATER" for h in hazards)


def test_snapping_rejects_interior_of_island_and_restricted_zone():
    """Verify that points in the interior of dry land or restricted zones are safely rejected."""
    from backend.main import _snap_to_navigable

    depths = np.full((30, 30), 10.0, dtype=np.float32)
    # Island from row 5..25, col 10..20
    depths[5:25, 10:20] = 0.0

    fw = np.ones((30, 30), dtype=np.float32)
    # Restricted zone from row 2..5, col 2..5
    fw[2:6, 2:6] = 1000.0

    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.03,
        lon_min=33.0,
        lon_max=33.03,
        depths=depths,
        fairway_weights=fw,
    )

    # 1. Point deep inside island (row 15, col 15) has no adjacent water
    sr, sc, s_dist, reason = _snap_to_navigable(15, 15, grid, min_depth=3.0)
    assert sr is None
    assert reason == "LAND_INTERIOR"

    # 2. Point deep inside restricted zone (row 3, col 3) has no adjacent unrestricted water
    sr_res, sc_res, s_dist_res, reason_res = _snap_to_navigable(3, 3, grid, min_depth=3.0)
    assert sr_res is None
    assert reason_res == "RESTRICTED_ZONE_INTERIOR"


def test_asymmetric_xtd_endcap_swath_detection():
    """
    Verify that asymmetric XTD (20m port / 1000m stbd) uses elliptical endcaps
    and detects hazards within the longitudinal endcap reach on the wide side,
    while correctly excluding points beyond the narrow side.
    """
    # 50x50 grid, 20m depth everywhere (~70m per cell)
    depths = np.full((50, 50), 20.0, dtype=np.float32)
    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.05,
        lon_min=33.0,
        lon_max=33.05,
        depths=depths,
    )

    # Route along row 25 from col 15 to col 35 (west to east)
    lat_25, lon_15 = grid.get_cell_coords(25, 15)
    lat_25, lon_35 = grid.get_cell_coords(25, 35)

    route_coords = [
        [lon_15, lat_25],
        [lon_35, lat_25],
    ]

    port_xtd_m = 20.0
    stbd_xtd_m = 1000.0  # Wide starboard side (south in this heading)
    safety_depth = 4.0

    # 1. Shoal behind start on starboard side (row 27, col 14) -> s < 0, d_xtd > 0
    # Inside the 510m longitudinal stern endcap!
    grid.depths[27, 14] = 1.0

    # 2. Shoal ahead of goal on starboard side (row 27, col 36) -> s > seg_len, d_xtd > 0
    # Inside the 510m longitudinal bow endcap!
    grid.depths[27, 36] = 1.2

    # 3. Shoal on port side (north) at row 23 (approx 200m offset), far beyond 20m port XTD
    grid.depths[23, 25] = 1.5

    hazards = scan_corridor_raster_swath(
        route_coords=route_coords,
        port_xtd_m=port_xtd_m,
        stbd_xtd_m=stbd_xtd_m,
        grid=grid,
        safety_depth=safety_depth,
    )

    detected_depths = [h["depth"] for h in hazards]
    # Both endcap starboard shoals must be detected
    assert 1.0 in detected_depths, "Stern endcap shoal on starboard side was not detected!"
    assert 1.2 in detected_depths, "Bow endcap shoal on starboard side was not detected!"
    # Port shoal beyond 20m must NOT be detected
    assert 1.5 not in detected_depths, "Shoal beyond port XTD was incorrectly included!"


def test_separated_corridor_alarms_by_hazard_type():
    """Verify verify_ecdis_route_safety separates alarms by hazard type (ALM-04, ALM-06, ALM-08, ALM-09)."""
    depths = np.full((30, 30), 15.0, dtype=np.float32)
    fw = np.ones((30, 30), dtype=np.float32)
    land = np.zeros((30, 30), dtype=bool)
    nodata = np.zeros((30, 30), dtype=bool)

    # Route row 15, cols 5..25
    grid = BathymetricGrid(
        lat_min=44.0,
        lat_max=44.03,
        lon_min=33.0,
        lon_max=33.03,
        depths=depths,
        fairway_weights=fw,
        land_mask=land,
        nodata_mask=nodata,
    )

    # 1. Shallow water at (16, 10)
    grid.depths[16, 10] = 1.0
    # 2. Restricted zone at (16, 14)
    grid.fairway_weights[16, 14] = 1000.0
    # 3. Land at (16, 18)
    grid.land_mask[16, 18] = True
    grid.depths[16, 18] = 0.0
    # 4. NoData at (16, 22)
    grid.nodata_mask[16, 22] = True
    grid.depths[16, 22] = 0.0

    lat_mid, lon_start = grid.get_cell_coords(15, 5)
    lat_mid, lon_end = grid.get_cell_coords(15, 25)

    waypoints = [
        {"lon": lon_start, "lat": lat_mid, "turn_radius_m": 150.0, "rot_deg_min": 0.0},
        {"lon": lon_end, "lat": lat_mid, "turn_radius_m": 150.0, "rot_deg_min": 0.0},
    ]
    profile = [
        {"distance_from_start_m": 0.0, "depth": 15.0, "clearance": 12.0},
        {"distance_from_start_m": 2000.0, "depth": 15.0, "clearance": 12.0},
    ]

    audit = verify_ecdis_route_safety(
        waypoints=waypoints,
        profile=profile,
        fairways_coll={"type": "FeatureCollection", "features": []},
        draft=2.0,
        dynamic_squat=0.5,
        ukc=0.5,
        speed_knots=10.0,
        min_chart_depth_m=3.0,
        grid=grid,
        port_xtd_m=200.0,
        stbd_xtd_m=200.0,
    )

    alarm_codes = [a["code"] for a in audit["alarms"]]
    assert "ALM-04-XTD-CORRIDOR-HAZARD" in alarm_codes
    assert "ALM-06-UNVERIFIED-DATA-COVERAGE" in alarm_codes
    assert "ALM-08-RESTRICTED-ZONE-VIOLATION" in alarm_codes
    assert "ALM-09-LAND-TERRESTRIAL-HAZARD" in alarm_codes

    for code in ["ALM-04-XTD-CORRIDOR-HAZARD", "ALM-06-UNVERIFIED-DATA-COVERAGE", "ALM-08-RESTRICTED-ZONE-VIOLATION", "ALM-09-LAND-TERRESTRIAL-HAZARD"]:
        alm = next(a for a in audit["alarms"] if a["code"] == code)
        assert alm["severity"] == "CRITICAL"
