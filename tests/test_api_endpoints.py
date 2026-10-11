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
        "safety_contour": 2.5,
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
    assert 'safetyContour="2.5"' in res_rtz.text
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


