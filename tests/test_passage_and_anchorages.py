"""
Integration tests for Anchorages, Passage Plan, and Sonar Upload API endpoints
=============================================================================
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_api_anchorages(client):
    res = client.get("/api/anchorages")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 5
    for anc in data:
        assert "id" in anc
        assert "name" in anc
        assert "coords" in anc
        assert len(anc["coords"]) == 2
        assert "depth_min_m" in anc
        assert "holding_ground" in anc
        assert "shelter" in anc


def test_api_sonar_upload_csv(client):
    csv_payload = {
        "raw_content": "lat,lon,depth\n44.595,33.428,14.5\n44.596,33.429,12.0\n44.597,33.430,8.5\n",
        "filename": "bay_survey.csv",
        "grid_size": 40
    }
    res = client.post("/api/bathymetry/upload-sonar", json=csv_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "statistics" in data
    assert data["statistics"]["soundings_count"] == 3
    assert data["statistics"]["min_depth_m"] == 8.5
    assert "geojson" in data
    assert data["geojson"]["type"] == "FeatureCollection"


def test_api_sonar_upload_empty_fails(client):
    res = client.post("/api/bathymetry/upload-sonar", json={"raw_content": ""})
    assert res.status_code == 400


def test_api_sonar_upload_invalid_format_fails(client):
    res = client.post("/api/bathymetry/upload-sonar", json={"raw_content": "foo,bar\n1,2\n"})
    assert res.status_code == 422


def test_api_passage_plan(client):
    mock_route = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[33.420, 44.620], [33.450, 44.600], [34.185, 44.485]]
        },
        "properties": {
            "distance_nm": 42.5,
            "min_chart_depth_m": 8.5,
            "is_safe": True
        },
        "waypoints": [
            {"role": "start", "coords": [33.420, 44.620], "leg_distance_nm": 15.0, "leg_bearing_deg": 120.0},
            {"role": "via", "coords": [33.450, 44.600], "leg_distance_nm": 27.5, "leg_bearing_deg": 105.0},
            {"role": "goal", "coords": [34.185, 44.485], "leg_distance_nm": 0.0, "leg_bearing_deg": 0.0}
        ],
        "profile": [
            {"distance_from_start_m": 0, "depth": 14.5, "clearance": 12.0},
            {"distance_from_start_m": 25000, "depth": 8.5, "clearance": 6.0},
            {"distance_from_start_m": 78000, "depth": 28.5, "clearance": 26.0}
        ],
        "bottleneck": {
            "coords": [33.450, 44.600],
            "depth": 8.5,
            "clearance": 6.0,
            "distance_from_start_m": 25000,
            "speed_adaptation": {"recommended_speed_knots": 8.0}
        }
    }

    payload = {
        "route_data": mock_route,
        "vessel_profile": "yacht",
        "vessel_name": "S/Y Black Pearl",
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "wave_height_m": 0.5,
        "tide_offset_m": 0.0,
        "fuel_burn_l_h": 15.0
    }

    res = client.post("/api/passage-plan", json=payload)
    assert res.status_code == 200
    plan = res.json()
    assert "passage_plan_id" in plan
    assert plan["vessel"]["name"] == "S/Y Black Pearl"
    assert "voyage_summary" in plan
    assert plan["voyage_summary"]["total_distance_nm"] == 42.5
    assert "bunkering_fuel_budget" in plan
    assert plan["bunkering_fuel_budget"]["nominal_consumption_liters"] > 0
    assert plan["bunkering_fuel_budget"]["total_fuel_required_liters"] > plan["bunkering_fuel_budget"]["nominal_consumption_liters"]
    assert len(plan["waypoints"]) == 3
    assert "dukc_safety_budget" in plan
    assert plan["dukc_safety_budget"]["static_draft_m"] == 1.5
    assert plan["dukc_safety_budget"]["barrass_squat_m"] > 0
    assert len(plan["ports_of_refuge"]) >= 1
