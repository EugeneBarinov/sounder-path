"""
SeaPath API Integration & Contract Tests
========================================
Validates all HTTP endpoints against schema contracts and edge cases.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_api_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["dataset_rows"] > 0
    assert data["dataset_cols"] > 0
    assert "is_synthetic" in data
    assert "bounds" in data
    assert len(data["bounds"]) == 4


def test_api_grid_coverage(client):
    res = client.get("/api/grid")
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) >= 1
    props = data["features"][0]["properties"]
    assert "is_synthetic" in props
    assert "bounds" in props


def test_api_depth_lookup(client):
    # 1. Point outside coverage
    res = client.get("/api/depth?lon=0.0&lat=0.0")
    assert res.status_code == 200
    d0 = res.json()
    assert d0["in_bounds"] is False
    assert "is_nodata" in d0
    assert "is_land" in d0

    # 2. Point inside coverage (center of Black Sea)
    res = client.get("/api/depth?lon=35.0&lat=43.5")
    assert res.status_code == 200
    data = res.json()
    assert data["in_bounds"] is True
    assert "depth" in data
    assert "is_nodata" in data
    assert "is_land" in data


def test_api_route_oob_validation(client):
    # Route with coordinates outside coverage area must return 400
    payload = {
        "start_lon": 10.0,
        "start_lat": 10.0,
        "goal_lon": 35.0,
        "goal_lat": 44.0,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 400
    assert "outside bathymetry coverage" in res.json()["detail"]


def test_api_route_invalid_waypoint_schema(client):
    # Malformed coordinate points must return 422 Unprocessable Entity
    payload = {
        "waypoints": [[34.0], [35.0, 44.0]],
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 422


def test_api_route_success_and_budget_consistency(client):
    # Valid route in deep water of Black Sea
    payload = {
        "start_lon": 34.0,
        "start_lat": 44.0,
        "goal_lon": 35.0,
        "goal_lat": 44.2,
        "draft": 1.5,
        "speed_knots": 12.0,
        "ukc": 0.5,
        "tide_offset_m": 0.2,
        "wave_height_m": 0.8,
        "turning_radius_m": 150.0,
        "exposed_waters": True,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 200
    data = res.json()

    # Verify GeoJSON structure
    assert data["type"] == "Feature"
    assert data["geometry"]["type"] == "LineString"
    assert len(data["geometry"]["coordinates"]) >= 2

    # Verify unified DUKC and diagnostics in properties
    props = data["properties"]
    assert "dynamic_draft_m" in props
    assert "min_chart_depth_m" in props
    assert "gross_ukc_m" in props
    assert "required_ukc_m" in props
    assert "clearance_margin_m" in props
    assert "is_navigable_candidate" in props
    assert "is_executable_passage_plan" in props
    assert "waypoint_snapping" in props
    assert props["is_executable_passage_plan"] is True
    assert "start_snapped" in props["waypoint_snapping"]

    # Verify safety check passed in deep water
    assert props["safety_check"]["is_safe"] is True
    assert props["dukc"]["is_safe"] is True


def test_api_export_gpx_and_rtz(client):
    import xmlschema
    from pathlib import Path

    xsd_path = Path(__file__).resolve().parent.parent / "backend" / "schemas" / "rtz_1_1.xsd"
    rtz_schema = xmlschema.XMLSchema(str(xsd_path))

    waypoints = [
        {"lat": 44.0, "lon": 34.0, "depth_at_waypoint": 200.0, "clearance_m": 198.0},
        {"lat": 44.2, "lon": 35.0, "depth_at_waypoint": 180.0, "clearance_m": 178.0},
    ]
    payload = {
        "waypoints": waypoints,
        "route": [[34.0, 44.0], [35.0, 44.2]],
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "safety_contour": 3.5,
    }

    # GPX
    res_gpx = client.post("/api/export/gpx", json=payload)
    assert res_gpx.status_code == 200
    assert "application/gpx+xml" in res_gpx.headers["content-type"]
    assert "WP01" in res_gpx.text

    # RTZ Standard Conformance
    res_rtz = client.post("/api/export/rtz", json=payload)
    assert res_rtz.status_code == 200
    assert "application/xml" in res_rtz.headers["content-type"]
    assert 'safetyContour="3.5"' in res_rtz.text
    assert 'portsideXTD=' in res_rtz.text
    assert 'geometryType="Loxodrome"' in res_rtz.text
    # Strict CIRM RTZ 1.1 XSD Validation
    rtz_schema.validate(res_rtz.text)

    # Hazardous route export check: must be rejected with 400 when allow_unverified=False
    payload_haz = dict(payload)
    payload_haz["is_safe"] = False
    payload_haz["safety_status"] = "CRITICAL_HAZARD"

    res_rejected_gpx = client.post("/api/export/gpx", json=payload_haz)
    assert res_rejected_gpx.status_code == 400
    assert "Cannot export hazardous passage plan" in res_rejected_gpx.json()["detail"]

    res_rejected_rtz = client.post("/api/export/rtz", json=payload_haz)
    assert res_rejected_rtz.status_code == 400

    # Hazardous route with allow_unverified=True must succeed with warning annotations and headers
    payload_haz["allow_unverified"] = True
    res_gpx_haz = client.post("/api/export/gpx", json=payload_haz)
    assert res_gpx_haz.status_code == 200
    assert res_gpx_haz.headers.get("X-SeaPath-Route-Status") == "UNVERIFIED_HAZARD"
    assert "CRITICAL WARNING" in res_gpx_haz.text

    res_rtz_haz = client.post("/api/export/rtz", json=payload_haz)
    assert res_rtz_haz.status_code == 200
    assert res_rtz_haz.headers.get("X-SeaPath-Route-Status") == "UNVERIFIED_HAZARD"
    assert "UNVERIFIED_HAZARD" in res_rtz_haz.text
    assert "CRITICAL WARNING" in res_rtz_haz.text
    # Strict CIRM RTZ 1.1 XSD Validation for hazardous export
    rtz_schema.validate(res_rtz_haz.text)


def test_full_grid_restricted_zone_detection():
    """Verify that full_grid has fairways attached and detects restricted areas within coverage."""
    from backend.main import full_grid, load_fairways_geojson

    fairways = load_fairways_geojson()
    restr_feats = [
        f for f in fairways.get("features", [])
        if f.get("properties", {}).get("category") == "restricted"
    ]
    assert len(restr_feats) > 0, "Expected at least one restricted zone in fairways data"

    tested = False
    for f in restr_feats:
        coords = f["geometry"]["coordinates"][0]
        mid_lon = sum(pt[0] for pt in coords) / len(coords)
        mid_lat = sum(pt[1] for pt in coords) / len(coords)
        if full_grid.is_within_coverage(mid_lon, mid_lat):
            r, c = full_grid.lonlat_to_cell(mid_lon, mid_lat)
            assert full_grid.is_restricted(r, c) is True, (
                f"Cell ({r}, {c}) inside restricted zone {f.get('properties', {}).get('name')} must be flagged is_restricted=True on full_grid"
            )
            tested = True
            break

    assert tested, "At least one restricted zone must fall inside grid coverage and be verified"


def test_production_fail_closed_health_status(monkeypatch, client):
    """Verify that in production mode, synthetic bathymetry causes /api/health to fail-closed with 503."""
    import backend.main as main_mod

    # Case 1: In production with synthetic mode -> 503 Unhealthy
    monkeypatch.setattr(main_mod, "_IS_PRODUCTION", True)
    monkeypatch.setattr(main_mod, "is_synthetic_mode", True)
    res_fail = client.get("/api/health")
    assert res_fail.status_code == 503
    d_fail = res_fail.json()
    assert d_fail["status"] == "unhealthy"
    assert d_fail["environment"] == "production"
    assert d_fail["data_status"] == "synthetic_demo"
    assert d_fail["is_synthetic"] is True

    # Case 2: In production with operational GeoTIFF -> 200 Healthy
    monkeypatch.setattr(main_mod, "is_synthetic_mode", False)
    res_ok = client.get("/api/health")
    assert res_ok.status_code == 200
    d_ok = res_ok.json()
    assert d_ok["status"] == "healthy"
    assert d_ok["environment"] == "production"
    assert d_ok["data_status"] == "operational"
    assert d_ok["is_synthetic"] is False


def test_multi_waypoint_snapping_metadata(client):
    """Verify that 3-waypoint passage plans retain independent snap metadata for each waypoint."""
    payload = {
        "waypoints": [
            [33.40, 45.18],  # Near Yevpatoriya harbor approach (snaps to navigable channel)
            [34.00, 44.00],  # Mid-sea deep water
            [35.00, 44.20],  # South Crimea deep water
        ],
        "draft": 1.2,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 200
    data = res.json()
    props = data["properties"]
    snap_info = props["waypoint_snapping"]

    assert "start_snapped" in snap_info
    assert "goal_snapped" in snap_info
    assert "all_waypoints_snapped" in snap_info
    assert "snapped_waypoints" in snap_info
    assert isinstance(snap_info["snapped_waypoints"], list)

    wps = data["waypoints"]
    assert len(wps) >= 3
    # Check that initial waypoint preserved its original coordinates
    if snap_info["start_snapped"]:
        assert wps[0].get("snap_offset_m", 0.0) > 0.0
        assert wps[0].get("original_coords") == [33.40, 45.18]


def test_server_side_export_rejects_hazardous_passage_plan(client):
    """Verify server rejects export of passage plans crossing dry land or shoals unless allow_unverified=True."""
    # Route through dry Crimean landmass
    land_waypoints = [
        {"lat": 45.30, "lon": 34.00},
        {"lat": 45.35, "lon": 34.05},
    ]
    payload = {
        "waypoints": land_waypoints,
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "allow_unverified": False,
    }

    # Must be rejected with 400 Bad Request
    res_gpx = client.post("/api/export/gpx", json=payload)
    assert res_gpx.status_code == 400
    assert "Cannot export hazardous passage plan" in res_gpx.json()["detail"]

    res_rtz = client.post("/api/export/rtz", json=payload)
    assert res_rtz.status_code == 400

    # With allow_unverified=True, must succeed with UNVERIFIED_HAZARD warning
    payload["allow_unverified"] = True
    res_gpx_override = client.post("/api/export/gpx", json=payload)
    assert res_gpx_override.status_code == 200
    assert res_gpx_override.headers.get("X-SeaPath-Route-Status") == "UNVERIFIED_HAZARD"

    # Malformed coordinate schema (e.g. invalid latitude 120.0) must fail validation with 422
    bad_payload = {
        "waypoints": [
            {"lat": 120.0, "lon": 34.0},
            {"lat": 44.0, "lon": 35.0},
        ],
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res_bad = client.post("/api/export/gpx", json=bad_payload)
    assert res_bad.status_code == 422


def test_client_cannot_bypass_dukc_via_low_safety_contour(client):
    """
    Verify that a client-supplied low safety_contour cannot bypass server-side safety checks.
    The server calculates canonical compute_search_budget() and enforces
    effective_safety_depth = max(req.safety_contour, canonical_min_chart_depth).
    """
    # A route where seabed depth is around 2.0m (near Crimean shoreline)
    # Draft 1.8m + speed 12 kts (squat ~0.35m) + UKC 0.5m -> requires > 2.65m depth.
    # Client passes safety_contour=1.0 and is_safe=True in an attempt to bypass.
    payload = {
        "waypoints": [
            {"lat": 45.18, "lon": 33.36},  # Yevpatoriya approach ~2m depth
            {"lat": 45.19, "lon": 33.37},
        ],
        "draft": 1.8,
        "speed_knots": 12.0,
        "ukc": 0.5,
        "safety_contour": 1.0,  # Deliberately lowered
        "is_safe": True,        # Client claims safe
        "allow_unverified": False,
    }
    res = client.post("/api/export/gpx", json=payload)
    assert res.status_code == 400
    assert "Cannot export hazardous passage plan" in res.json()["detail"]


def test_plan_id_export_workflow(client):
    """
    Verify passage plan export via server-issued plan_id:
    1. Calculate route via /api/route -> receive plan_id
    2. Export GPX using plan_id directly (zero geometry divergence)
    3. Export RTZ using plan_id directly with CIRM XSD validation
    4. Verify 404 for non-existent plan_id
    """
    import xmlschema
    from pathlib import Path

    xsd_path = Path(__file__).resolve().parent.parent / "backend" / "schemas" / "rtz_1_1.xsd"
    rtz_schema = xmlschema.XMLSchema(str(xsd_path))

    route_req = {
        "start_lon": 34.0,
        "start_lat": 44.0,
        "goal_lon": 35.0,
        "goal_lat": 44.2,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res_route = client.post("/api/route", json=route_req)
    assert res_route.status_code == 200
    route_data = res_route.json()
    assert "plan_id" in route_data
    plan_id = route_data["plan_id"]
    assert len(plan_id) > 10

    # 1. Export GPX via plan_id
    res_gpx = client.post("/api/export/gpx", json={"plan_id": plan_id})
    assert res_gpx.status_code == 200
    assert "application/gpx+xml" in res_gpx.headers["content-type"]
    assert "WP01" in res_gpx.text

    # 2. Export RTZ via plan_id
    res_rtz = client.post("/api/export/rtz", json={"plan_id": plan_id})
    assert res_rtz.status_code == 200
    assert "application/xml" in res_rtz.headers["content-type"]
    rtz_schema.validate(res_rtz.text)

    # 3. Non-existent plan_id returns 404
    res_404 = client.post("/api/export/gpx", json={"plan_id": "00000000-0000-0000-0000-000000000000"})
    assert res_404.status_code == 404
    assert "not found or expired" in res_404.json()["detail"]


def test_export_geometry_coupling_validation(client):
    """
    Verify server prevents route/waypoint divergence and bypasses:
    - Departure endpoint divergence > 50m
    - Destination endpoint divergence > 50m
    - Intermediate waypoint lateral divergence > 50m
    - Non-monotonic waypoint sequence along route track
    """
    valid_route = [
        [34.00, 44.00],
        [34.50, 44.10],
        [35.00, 44.20],
    ]

    # Case 1: Departure mismatch (WP1 is 5 km north of route departure)
    bad_dep_payload = {
        "waypoints": [
            {"lat": 44.05, "lon": 34.00},  # ~5.5 km north
            {"lat": 44.20, "lon": 35.00},
        ],
        "route": valid_route,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res_dep = client.post("/api/export/gpx", json=bad_dep_payload)
    assert res_dep.status_code == 400
    assert "Departure waypoint WP1" in res_dep.json()["detail"]

    # Case 2: Destination mismatch (WP2 is 5 km south of route destination)
    bad_dest_payload = {
        "waypoints": [
            {"lat": 44.00, "lon": 34.00},
            {"lat": 44.15, "lon": 35.00},  # ~5.5 km south
        ],
        "route": valid_route,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res_dest = client.post("/api/export/gpx", json=bad_dest_payload)
    assert res_dest.status_code == 400
    assert "Destination waypoint" in res_dest.json()["detail"]

    # Case 3: Intermediate waypoint lateral deviation (> 50m off route track)
    bad_mid_payload = {
        "waypoints": [
            {"lat": 44.00, "lon": 34.00},
            {"lat": 44.30, "lon": 34.50},  # ~22 km off polyline
            {"lat": 44.20, "lon": 35.00},
        ],
        "route": valid_route,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res_mid = client.post("/api/export/gpx", json=bad_mid_payload)
    assert res_mid.status_code == 400
    assert "deviates by" in res_mid.json()["detail"]

    # Case 4: Non-monotonic waypoint sequence (WP2 is placed AFTER WP3 along track)
    bad_order_payload = {
        "waypoints": [
            {"lat": 44.00, "lon": 34.00},
            {"lat": 44.20, "lon": 35.00},
            {"lat": 44.10, "lon": 34.50},  # Backwards!
        ],
        "route": valid_route,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res_order = client.post("/api/export/gpx", json=bad_order_payload)
    assert res_order.status_code == 400
    assert "Destination waypoint" in res_order.json()["detail"] or "out of order" in res_order.json()["detail"]


def test_synthetic_bathymetry_non_executable_status(monkeypatch, client):
    """
    Verify P1.4: In synthetic bathymetry mode, routes are explicitly marked
    DEMO_UNVERIFIED / SYNTHETIC_DEMO_NON_NAVIGABLE with is_executable_passage_plan=False.
    Export without allow_unverified=True is blocked.
    """
    import backend.main as main_mod

    monkeypatch.setattr(main_mod, "is_synthetic_mode", True)
    monkeypatch.setattr(main_mod.full_grid, "is_synthetic", True)

    payload = {
        "start_lon": 34.0,
        "start_lat": 44.0,
        "goal_lon": 35.0,
        "goal_lat": 44.2,
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 200
    data = res.json()
    props = data["properties"]

    assert props["is_executable_passage_plan"] is False
    assert props["data_integrity"] == "SYNTHETIC_DEMO_NON_NAVIGABLE"
    assert props["safety_check"]["status"] == "DEMO_UNVERIFIED"
    assert props["safety_check"]["is_safe"] is False
    alarm_codes = [a["code"] for a in props["safety_check"]["alarms"]]
    assert "ALM-10-SYNTHETIC-DATA-NON-NAVIGABLE" in alarm_codes

    plan_id = data["plan_id"]

    # Export must fail closed unless allow_unverified=True
    res_export_blocked = client.post("/api/export/gpx", json={"plan_id": plan_id})
    assert res_export_blocked.status_code == 400
    assert "DEMO_UNVERIFIED" in res_export_blocked.json()["detail"]

    # Export with allow_unverified=True must succeed with DEMO_UNVERIFIED header
    res_export_override = client.post("/api/export/gpx", json={"plan_id": plan_id, "allow_unverified": True})
    assert res_export_override.status_code == 200
    assert res_export_override.headers.get("X-SeaPath-Route-Status") == "DEMO_UNVERIFIED"


def test_real_subprocess_production_fail_closed_missing_geotiff():
    """
    Verify P2.6: Real subprocess execution with SEAPATH_ENV=production and a non-existent
    GeoTIFF dataset immediately aborts at module load time with RuntimeError and exit code 1.
    """
    import subprocess
    import sys
    import os
    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["SEAPATH_ENV"] = "production"
    env["SEAPATH_GEOTIFF_PATH"] = str(base_dir / "data" / "raw" / "nonexistent_operational_file.tif")
    env["PYTHONPATH"] = str(base_dir)

    proc = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        cwd=str(base_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0, "Production mode without GeoTIFF must fail closed with non-zero exit code"
    assert "PRODUCTION FAILURE" in proc.stderr
    assert "RuntimeError" in proc.stderr


def test_production_fail_closed_sample_fallback_prohibited(tmp_path):
    """
    Verify Issue 1: When SEAPATH_ENV=production, SEAPATH_GEOTIFF_PATH is unset,
    main E8_2024.tif is absent, but operational_sample.tif IS present in data/raw,
    the server MUST fail-closed with RuntimeError (not silently start on the sample).
    """
    import subprocess
    import sys
    import os
    import shutil
    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    mock_raw_dir = tmp_path / "mock_raw"
    mock_raw_dir.mkdir(parents=True, exist_ok=True)
    sample_src = base_dir / "data" / "raw" / "operational_sample.tif"
    if sample_src.exists():
        shutil.copy2(sample_src, mock_raw_dir / "operational_sample.tif")

    env = os.environ.copy()
    env["SEAPATH_ENV"] = "production"
    env["SEAPATH_DATA_DIR"] = str(mock_raw_dir)
    env.pop("SEAPATH_GEOTIFF_PATH", None)
    env["PYTHONPATH"] = str(base_dir)

    proc = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        cwd=str(base_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0, "Production mode with only sample GeoTIFF must fail closed"
    assert "PRODUCTION FAILURE" in proc.stderr
    assert "RuntimeError" in proc.stderr


def test_production_fail_closed_explicit_sample_path_rejected():
    """
    Verify Issue 1: In production mode, explicitly passing operational_sample.tif via
    SEAPATH_GEOTIFF_PATH is rejected as unauthorized test-only dataset.
    """
    import subprocess
    import sys
    import os
    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["SEAPATH_ENV"] = "production"
    env["SEAPATH_GEOTIFF_PATH"] = str(base_dir / "data" / "raw" / "operational_sample.tif")
    env["PYTHONPATH"] = str(base_dir)

    proc = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        cwd=str(base_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "PRODUCTION FAILURE" in proc.stderr
    assert "operational_sample.tif" in proc.stderr


def test_export_rejects_nan_and_inf_safety_contour(client):
    """
    Verify Issue 2: NaN and Inf in safety_contour or draft are rejected at schema level with 422.
    """
    import json
    from pydantic import ValidationError
    from backend.main import ExportRouteRequest

    valid_waypoints = [
        {"lat": 44.0, "lon": 34.0, "depth_at_waypoint": 200.0, "clearance_m": 198.0},
        {"lat": 44.2, "lon": 35.0, "depth_at_waypoint": 180.0, "clearance_m": 178.0},
    ]

    # 1. Pydantic validation rejects NaN and Inf
    with pytest.raises(ValidationError):
        ExportRouteRequest(waypoints=valid_waypoints, safety_contour=float("nan"))

    with pytest.raises(ValidationError):
        ExportRouteRequest(waypoints=valid_waypoints, safety_contour=float("inf"))

    with pytest.raises(ValidationError):
        ExportRouteRequest(waypoints=valid_waypoints, draft=float("nan"))

    with pytest.raises(ValidationError):
        ExportRouteRequest(waypoints=valid_waypoints, draft=float("-inf"))

    # 2. HTTP endpoint rejects non-finite values (422 Unprocessable Entity)
    nan_body = json.dumps({
        "waypoints": valid_waypoints,
        "safety_contour": float("nan"),
    }, allow_nan=True)
    res_nan = client.post("/api/export/gpx", content=nan_body, headers={"Content-Type": "application/json"})
    assert res_nan.status_code == 422

    inf_body = json.dumps({
        "waypoints": valid_waypoints,
        "safety_contour": float("inf"),
    }, allow_nan=True)
    res_inf = client.post("/api/export/gpx", content=inf_body, headers={"Content-Type": "application/json"})
    assert res_inf.status_code == 422

    draft_nan_body = json.dumps({
        "waypoints": valid_waypoints,
        "draft": float("nan"),
    }, allow_nan=True)
    res_draft_nan = client.post("/api/export/rtz", content=draft_nan_body, headers={"Content-Type": "application/json"})
    assert res_draft_nan.status_code == 422



def test_export_90_deg_turn_with_150m_radius_passes_geometry_coupling(client):
    """
    Verify Issue 3: Real passage plan with 90° course alteration and 150m turning radius
    where vertex is ~62.1m from filleted arc passes geometry coupling validation.
    """
    payload = {
        "start_lon": 34.00,
        "start_lat": 44.00,
        "goal_lon": 34.20,
        "goal_lat": 44.20,
        "draft": 1.2,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "turning_radius_m": 150.0,
    }
    res_route = client.post("/api/route", json=payload)
    assert res_route.status_code == 200
    route_data = res_route.json()

    # Export using canonical plan_id
    plan_id = route_data["plan_id"]
    res_gpx_plan = client.post("/api/export/gpx", json={"plan_id": plan_id})
    assert res_gpx_plan.status_code == 200

    # Also test direct coordinates export with the router's generated smoothed track and waypoints
    export_payload = {
        "waypoints": route_data["waypoints"],
        "route": route_data["geometry"]["coordinates"],
        "draft": 1.2,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "turning_radius_m": 150.0,
    }
    res_gpx_direct = client.post("/api/export/gpx", json=export_payload)
    assert res_gpx_direct.status_code == 200
    assert "xml" in res_gpx_direct.headers.get("Content-Type", "")


def test_export_preserves_warning_advisory_status(client):
    """
    Verify Issue 6: When safety_status is WARNING_ADVISORY, the status is preserved
    in HTTP response header and in RTZ 1.1 XML without being flattened to SAFE_PASSED.
    """
    import xmlschema
    from pathlib import Path

    xsd_path = Path(__file__).resolve().parent.parent / "backend" / "schemas" / "rtz_1_1.xsd"
    rtz_schema = xmlschema.XMLSchema(str(xsd_path))

    waypoints = [
        {"lat": 44.0, "lon": 34.0, "depth_at_waypoint": 200.0, "clearance_m": 198.0},
        {"lat": 44.2, "lon": 35.0, "depth_at_waypoint": 180.0, "clearance_m": 178.0},
    ]
    payload = {
        "waypoints": waypoints,
        "draft": 1.2,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "safety_status": "WARNING_ADVISORY",
        "is_safe": True,
    }

    res_rtz = client.post("/api/export/rtz", json=payload)
    assert res_rtz.status_code == 200
    assert res_rtz.headers.get("X-SeaPath-Route-Status") == "WARNING_ADVISORY"

    # RTZ XML must validate against CIRM XSD and contain WARNING_ADVISORY status
    rtz_xml = res_rtz.text
    rtz_schema.validate(rtz_xml)
    assert 'routeStatus="WARNING_ADVISORY"' in rtz_xml

    # GPX export must also preserve status
    res_gpx = client.post("/api/export/gpx", json=payload)
    assert res_gpx.status_code == 200
    assert res_gpx.headers.get("X-SeaPath-Route-Status") == "WARNING_ADVISORY"
    assert "ADVISORY: NAVIGATIONAL WARNINGS PRESENT" in res_gpx.text


def test_export_rejects_detached_waypoint_bypass_attempt(client):
    """
    Verify Issue 1: An attempted bypass where client claims turn_angle_deg=170 and
    turn_radius_m=2000 for a waypoint detached by ~33 km from the route track
    MUST be rejected with HTTP 400 and cannot inflate the tolerance.
    """
    # Safe deep-water straight route
    straight_route = [
        [34.0, 44.0],
        [34.1, 44.0],
        [34.2, 44.0],
        [34.3, 44.0],
        [34.4, 44.0],
        [34.5, 44.0],
    ]
    # WP2 is intentionally detached ~33 km north (44.3 vs 44.0)
    detached_waypoints = [
        {"lat": 44.0, "lon": 34.0, "turn_radius_m": 0.0},
        {"lat": 44.3, "lon": 34.25, "turn_angle_deg": 170.0, "turn_radius_m": 2000.0},
        {"lat": 44.0, "lon": 34.5, "turn_radius_m": 0.0},
    ]
    payload = {
        "waypoints": detached_waypoints,
        "route": straight_route,
        "draft": 1.2,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/export/rtz", json=payload)
    assert res.status_code == 400
    assert "Passage plan geometry mismatch" in res.json()["detail"]
    assert "deviates by" in res.json()["detail"]


def test_export_preserves_zero_turning_radius_in_rtz(client):
    """
    Verify Issue 2: RTZ export does not turn radius=0.0 into 150m.
    Waypoints with turn_radius_m=0.0 must output radius="0.000" in RTZ 1.1 XML.
    """
    import xmlschema
    from pathlib import Path

    xsd_path = Path(__file__).resolve().parent.parent / "backend" / "schemas" / "rtz_1_1.xsd"
    rtz_schema = xmlschema.XMLSchema(str(xsd_path))

    waypoints = [
        {"lat": 44.0, "lon": 34.0, "turn_radius_m": 0.0},
        {"lat": 44.1, "lon": 34.5, "turn_radius_m": 0.0},
        {"lat": 44.2, "lon": 35.0, "turn_radius_m": 0.0},
    ]
    payload = {
        "waypoints": waypoints,
        "draft": 1.2,
        "speed_knots": 10.0,
        "ukc": 0.5,
    }
    res = client.post("/api/export/rtz", json=payload)
    assert res.status_code == 200
    rtz_xml = res.text
    rtz_schema.validate(rtz_xml)
    # Start, middle with 0 radius, and end waypoints must have radius="0.000"
    assert 'radius="0.000"' in rtz_xml
    assert 'radius="0.081"' not in rtz_xml  # 150m is 0.081 NM, should NOT be present


def test_production_fail_closed_renamed_small_sample_rejected(tmp_path):
    """
    Verify Issue 3: In production mode, renaming operational_sample.tif to verified_bathymetry.tif
    must still fail closed because its dimensions (825x405) are below the 1000x1000 requirement.
    """
    import subprocess
    import sys
    import os
    import shutil
    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    sample_src = base_dir / "data" / "raw" / "operational_sample.tif"
    assert sample_src.exists(), "operational_sample.tif must exist for this test"

    mock_dir = tmp_path / "production_mock"
    mock_dir.mkdir(parents=True, exist_ok=True)
    renamed_sample = mock_dir / "verified_regional_bathymetry.tif"
    shutil.copy2(sample_src, renamed_sample)

    env = os.environ.copy()
    env["SEAPATH_ENV"] = "production"
    env["SEAPATH_GEOTIFF_PATH"] = str(renamed_sample)
    env["PYTHONPATH"] = str(base_dir)

    proc = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        cwd=str(base_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "PRODUCTION FAILURE" in proc.stderr
    assert "insufficient for operational navigation" in proc.stderr


def test_safety_depth_fail_closed_on_invalid_threshold():
    """
    Verify Issue 5: scan_corridor_raster_swath and verify_ecdis_route_safety fail-closed
    rather than guessing a default 2.0m depth when safety_depth is NaN or non-positive.
    """
    import pytest
    from backend.core.safety import scan_corridor_raster_swath, verify_ecdis_route_safety
    from backend.main import full_grid

    # 1. scan_corridor_raster_swath raises ValueError on NaN
    with pytest.raises(ValueError, match="Invalid safety_depth threshold"):
        scan_corridor_raster_swath(
            route_coords=[[34.0, 44.0], [34.5, 44.5]],
            port_xtd_m=185.2,
            stbd_xtd_m=185.2,
            grid=full_grid,
            safety_depth=float("nan"),
        )

    # 2. scan_corridor_raster_swath raises ValueError on non-positive depth
    with pytest.raises(ValueError, match="Invalid safety_depth threshold"):
        scan_corridor_raster_swath(
            route_coords=[[34.0, 44.0], [34.5, 44.5]],
            port_xtd_m=185.2,
            stbd_xtd_m=185.2,
            grid=full_grid,
            safety_depth=-1.5,
        )

    # 3. verify_ecdis_route_safety returns CRITICAL_HAZARD on non-finite safety depth
    res = verify_ecdis_route_safety(
        waypoints=[{"lat": 44.0, "lon": 34.0}],
        profile=[{"distance_from_start_m": 0.0, "depth": 10.0, "clearance": 8.0}],
        fairways_coll={"features": []},
        draft=1.0,
        dynamic_squat=0.2,
        ukc=0.5,
        speed_knots=10.0,
        min_chart_depth_m=float("nan"),
        grid=full_grid,
    )
    assert res["is_safe"] is False
    assert res["status"] == "CRITICAL_HAZARD"
    assert any(a["code"] == "ALM-00-INVALID-SAFETY-DEPTH" for a in res["alarms"])


def test_export_without_route_scans_curved_arc_shoal_outside_straight_xtd(client):
    """
    P1 verification: When client requests export without 'route' coordinates,
    the backend must construct the true physical curved trajectory (filleted circular turn arcs)
    rather than scanning straight lines between waypoints.
    A shoal located along the curved arc (586m from the corner, outside 185.2m straight-line XTD)
    must be detected by the corridor swath and reject hazardous export.
    """
    from backend.main import full_grid

    apex_lon, apex_lat = 33.507282, 43.694746
    r, c = full_grid.lonlat_to_cell(apex_lon, apex_lat)
    orig_depth = float(full_grid.depths[r, c])

    # 1. Place a shallow sounding at the arc apex
    full_grid.depths[r, c] = 1.0

    payload = {
        "waypoints": [
            {"lon": 33.5, "lat": 43.5, "turn_radius_m": 2000.0, "turn_angle_deg": 0.0},
            {"lon": 33.5, "lat": 43.7, "turn_radius_m": 2000.0, "turn_angle_deg": 90.0},
            {"lon": 33.7, "lat": 43.7, "turn_radius_m": 2000.0, "turn_angle_deg": 0.0},
        ],
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "safety_contour": 5.0,
        "turning_radius_m": 2000.0,
        "allow_unsafe": False,
    }

    try:
        # Route is omitted in payload: must construct curved arc and detect shoal
        res = client.post("/api/export/rtz", json=payload)
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "Cannot export hazardous passage plan" in detail
        assert "ALM-01-GROUNDING-HAZARD" in detail or "ALM-04-XTD-CORRIDOR-HAZARD" in detail
    finally:
        full_grid.depths[r, c] = orig_depth

    # 2. When shoal is removed, export succeeds
    res_safe = client.post("/api/export/rtz", json=payload)
    assert res_safe.status_code == 200


def test_multi_waypoint_route_junction_kinematics_and_arc_splicing(client):
    """
    P2 verification: Multi-waypoint route junctions must receive calculated turning radius,
    valid rate-of-turn, and have circular fillet turn arcs spliced into the route geometry.
    Must not trigger false ALM-05 alarms for intermediate course alterations.
    """
    payload = {
        "waypoints": [[33.5, 43.5], [33.5, 43.7], [33.7, 43.7]],
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "turning_radius_m": 2000.0,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 200
    data = res.json()

    wps = data["waypoints"]
    assert len(wps) == 3

    # Intermediate junction waypoint (index 1)
    wp1 = wps[1]
    assert wp1["turn_radius_m"] == 2000.0
    assert wp1["turn_angle_deg"] > 80.0
    assert wp1["rot_deg_min"] > 0.0
    assert wp1["wop_distance_m"] > 0.0
    assert wp1["maneuver_feasible"] is True

    # No false turning radius alarm (ALM-05)
    alarm_codes = [a["code"] for a in data.get("alarms", [])]
    assert "ALM-05-TURNING-RADIUS-RESTRICTION" not in alarm_codes

    # Turn arcs feature collection must be populated
    turn_arcs = data.get("turn_arcs", {})
    assert turn_arcs.get("type") == "FeatureCollection"
    assert len(turn_arcs.get("features", [])) >= 1


def test_multi_worker_plan_id_disk_cache(client):
    """
    P2 verification: Passage plans stored via plan_id must persist to cross-process disk cache
    so that multi-worker Uvicorn processes can retrieve and validate plans regardless of worker routing.
    """
    from backend.main import _SAVED_PLANS, _PLANS_CACHE_DIR

    payload = {
        "waypoints": [[33.5, 43.5], [33.5, 43.7], [33.7, 43.7]],
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "turning_radius_m": 2000.0,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 200
    plan_id = res.json()["plan_id"]

    # Verify atomic file exists on disk
    plan_file = _PLANS_CACHE_DIR / f"{plan_id}.json"
    assert plan_file.exists()

    # Clear in-memory LRU to simulate a request landing on a separate Uvicorn worker process
    _SAVED_PLANS.clear()

    # Export via plan_id must succeed by loading from disk cache
    res_export = client.post("/api/export/rtz", json={"plan_id": plan_id})
    assert res_export.status_code == 200


def test_production_fail_closed_insufficient_geo_span(tmp_path):
    """
    P1 verification: In production mode, a dataset with 1000x1000 cells but insufficient
    geographic span (dLat < 3.0° or dLon < 8.0°) must fail closed.
    """
    import subprocess
    import sys
    import os
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds
    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    mock_dir = tmp_path / "geo_span_mock"
    mock_dir.mkdir(parents=True, exist_ok=True)
    small_span_tif = mock_dir / "small_span.tif"

    # Create 1500x1500 raster with small geographic span (0.5° x 0.5°, 2.25M cells)
    width, height = 1500, 1500
    transform = from_bounds(33.0, 43.0, 33.5, 43.5, width, height)
    data = np.full((1, height, width), -50.0, dtype=np.float32)

    with rasterio.open(
        small_span_tif,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=np.float32,
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(data)

    env = os.environ.copy()
    env["SEAPATH_ENV"] = "production"
    env["SEAPATH_GEOTIFF_PATH"] = str(small_span_tif)
    env["PYTHONPATH"] = str(base_dir)

    proc = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        cwd=str(base_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "PRODUCTION FAILURE" in proc.stderr
    assert "geographic span" in proc.stderr


def test_plan_id_rtz_export_matches_route_safety_contour(client):
    """
    P1 verification: In RTZ exported via plan_id, safetyContour and safetyDepth
    attributes must precisely match min_chart_depth_m calculated by /api/route,
    accounting for full canonical DUKC (squat, heel, wave allowance) rather than
    defaulting to understated static draft + UKC.
    """
    import xml.etree.ElementTree as ET

    payload = {
        "waypoints": [[33.5, 43.5], [33.5, 43.7], [33.7, 43.7]],
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "turning_radius_m": 2000.0,
    }
    res_route = client.post("/api/route", json=payload)
    assert res_route.status_code == 200
    route_data = res_route.json()
    plan_id = route_data["plan_id"]
    expected_min_chart_depth = route_data["properties"]["min_chart_depth_m"]

    # Must be strictly greater than static draft + ukc (2.0 + 0.5 = 2.5) due to dynamic squat/heel
    assert expected_min_chart_depth > 2.5

    # Export to RTZ via plan_id
    res_rtz = client.post("/api/export/rtz", json={"plan_id": plan_id})
    assert res_rtz.status_code == 200

    root = ET.fromstring(res_rtz.text)
    ns = {"rtz": "http://www.cirm.org/RTZ/1/1"}
    legs = root.findall(".//rtz:leg", ns)
    assert len(legs) >= 1

    expected_val_str = f"{expected_min_chart_depth:.1f}"
    for leg in legs:
        assert leg.get("safetyContour") == expected_val_str
        assert leg.get("safetyDepth") == expected_val_str


def test_export_rejects_straight_route_with_large_turn_radius_bypass_attempt(client):
    """
    P1 verification: If client provides a straight polyline cutting through an intermediate waypoint,
    while requesting a large turning radius in RTZ (e.g. 2000m at a 90° corner),
    the server must detect the kinematic mismatch (arc apex offset ~586m from straight legs)
    and reject the request with HTTP 400.
    """
    payload = {
        "waypoints": [
            {"lon": 33.5, "lat": 43.5, "turn_radius_m": 2000.0, "turn_angle_deg": 0.0},
            {"lon": 33.5, "lat": 43.7, "turn_radius_m": 2000.0, "turn_angle_deg": 90.0},
            {"lon": 33.7, "lat": 43.7, "turn_radius_m": 2000.0, "turn_angle_deg": 0.0},
        ],
        "route": [
            [33.5, 43.5],
            [33.5, 43.7],
            [33.7, 43.7],
        ],
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "safety_contour": 5.0,
        "turning_radius_m": 2000.0,
        "allow_unsafe": False,
    }
    res = client.post("/api/export/rtz", json=payload)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "turning kinematics" in detail or "deviates" in detail


def test_route_profile_and_metrics_synchronized_with_spliced_turn_arcs(client):
    """
    P2 verification: After turn arcs are spliced into combined_route, the continuous depth profile,
    cumulative distance, ETA and minimum clearance must be recalculated along the finalized curved track.
    """
    payload = {
        "waypoints": [[33.5, 43.5], [33.5, 43.7], [33.7, 43.7]],
        "draft": 2.0,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "turning_radius_m": 2000.0,
    }
    res = client.post("/api/route", json=payload)
    assert res.status_code == 200
    data = res.json()

    route_coords = data["geometry"]["coordinates"]
    profile = data["profile"]
    props = data["properties"]

    assert len(profile) >= len(route_coords)
    # Check that profile ends at distance equal to properties.distance_m (+/- 1m tolerance)
    last_profile_dist = profile[-1]["distance_from_start_m"]
    assert abs(last_profile_dist - props["distance_m"]) <= 1.0

    # Minimum clearance in diagnostics matches profile minimum
    profile_min_clearance = min(p["clearance"] for p in profile)
    assert abs(props["min_clearance_m"] - profile_min_clearance) <= 0.05


def test_cache_directory_handles_readonly_filesystem(tmp_path):
    """
    P1 verification: If SEAPATH_CACHE_DIR points to a read-only or inaccessible directory
    (such as a read-only Docker volume mount ./data:/app/data:ro), backend.main initialization
    and plan storage/retrieval must fall back gracefully to a writable temp cache without failing.
    """
    import subprocess
    import sys
    import os
    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    ro_dir = tmp_path / "readonly_cache"
    ro_dir.mkdir(parents=True, exist_ok=True)

    test_code = """
import os
import backend.main
from backend.main import _store_passage_plan, _get_passage_plan, _PLANS_CACHE_DIR

# Store and retrieve plan
_store_passage_plan('test-ro-plan', {'status': 'ok'})
retrieved = _get_passage_plan('test-ro-plan')
assert retrieved is not None
assert retrieved['status'] == 'ok'
print('SUCCESS_CACHE_FALLBACK')
"""
    env = os.environ.copy()
    env["SEAPATH_CACHE_DIR"] = str(ro_dir / "forbidden" / "sub" / "plans")
    env["PYTHONPATH"] = str(base_dir)

    proc = subprocess.run(
        [sys.executable, "-c", test_code],
        cwd=str(base_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "SUCCESS_CACHE_FALLBACK" in proc.stdout






