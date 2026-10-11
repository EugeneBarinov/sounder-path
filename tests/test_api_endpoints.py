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
