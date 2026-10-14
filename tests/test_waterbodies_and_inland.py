"""
Tests for Multi-Waterbody, Inland Waterways, Rivers, Lakes, and Live Telemetry
=============================================================================
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app, waterbody_mgr, activate_waterbody_instance

client = TestClient(app)


def test_list_waterbodies():
    """Verify listing all registered coastal and inland waterbodies."""
    res = client.get("/api/waterbodies")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 4

    ids = [w["id"] for w in data]
    assert "black_sea" in ids
    assert "sevastopol_bays" in ids
    assert "volga_river" in ids
    assert "ladoga_lake" in ids

    categories = [w["category"] for w in data]
    assert "bay" in categories
    assert "river" in categories
    assert "lake" in categories


def test_activate_waterbody_volga():
    """Verify switching to Volga River activates river grid, ports, and depth lookups."""
    res = client.post("/api/waterbody/activate", json={"waterbody_id": "volga_river"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["active_waterbody"]["id"] == "volga_river"
    assert "Volga" in data["active_waterbody"]["name"] or "Волга" in data["active_waterbody"]["name"]

    # Check ports now return river docks
    ports_res = client.get("/api/ports")
    assert ports_res.status_code == 200
    ports = ports_res.json()
    assert any("Самара" in p["name"] for p in ports)

    # Check river depth lookup in channel
    d_res = client.get("/api/depth?lon=50.14&lat=53.22")
    assert d_res.status_code == 200
    d_data = d_res.json()
    assert d_data["in_bounds"] is True
    assert d_data["depth"] > 5.0  # Deep river channel


def test_river_routing_volga():
    """Verify calculating a safe passage plan along river bends avoiding banks."""
    activate_waterbody_instance("volga_river")

    # Start: Samara River station approach, Goal: Downstream approach or anchorage
    start_pt = [50.147, 53.186]
    goal_pt = [50.103, 53.240]

    payload = {
        "waypoints": [start_pt, goal_pt],
        "draft": 1.5,
        "speed_knots": 10.0,
        "ukc": 0.5,
        "turning_radius_m": 100.0,
        "fairway_preference": 1.0,
        "block_coefficient": 0.65,
        "port_xtd_m": 150.0,
        "stbd_xtd_m": 150.0,
        "tide_offset_m": 0.0,
        "wave_height_m": 0.0,
    }

    res = client.post("/api/route", json=payload)
    assert res.status_code == 200, res.text
    route_data = res.json()
    assert route_data["type"] == "Feature"
    assert len(route_data["geometry"]["coordinates"]) >= 2
    assert route_data["properties"]["distance_nm"] > 1.0

    # Minimum depth on route must satisfy draft + ukc
    assert route_data["properties"]["min_chart_depth_m"] >= 2.0


def test_create_custom_waterbody_lake(monkeypatch):
    """Verify creating a custom lake waterbody on demand anywhere on Earth with mocked OSM query."""
    import backend.core.waterbodies as wb_mod

    # Deterministic offline mock for OSM Overpass polygon
    mock_poly = {
        "type": "Polygon",
        "coordinates": [[
            [6.10, 46.20],
            [6.60, 46.20],
            [6.60, 46.50],
            [6.10, 46.50],
            [6.10, 46.20]
        ]]
    }
    monkeypatch.setattr(wb_mod, "_fetch_osm_water_polygons", lambda bbox, timeout_sec=5: [mock_poly])

    # Lake Geneva / Lac Leman bbox
    bbox = [6.10, 46.20, 6.60, 46.50]
    payload = {
        "name": "Женевское озеро (Lac Leman)",
        "category": "lake",
        "bbox": bbox,
        "soundings": [
            {"lat": 46.35, "lon": 6.35, "depth_m": 45.0},
            {"lat": 46.30, "lon": 6.25, "depth_m": 25.0},
            {"lat": 46.40, "lon": 6.45, "depth_m": 60.0},
        ]
    }

    res = client.post("/api/waterbody/create", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["created_waterbody"]["is_custom"] is True
    assert "Женевское" in data["created_waterbody"]["name"]

    # Verify depth at sounding point
    d_res = client.get("/api/depth?lon=6.35&lat=46.35")
    assert d_res.status_code == 200
    assert d_res.json()["in_bounds"] is True
    assert d_res.json()["depth"] > 20.0


def test_geo_search_proxy(monkeypatch):
    """Verify OSM Nominatim geocoding search proxy with mocked network response."""
    import urllib.request
    from unittest.mock import MagicMock
    import json

    mock_osm_items = [
        {
            "display_name": "Женевское озеро, Швейцария",
            "boundingbox": ["46.20", "46.50", "6.10", "6.60"],
            "lat": "46.35",
            "lon": "6.35",
            "type": "water"
        }
    ]

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_osm_items).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=5: mock_resp)

    res = client.get("/api/geo/search?q=geneva")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert "Женевское" in data[0]["name"]
    assert data[0]["bbox"] == [6.10, 46.20, 6.60, 46.50]


def test_live_telemetry_and_track():
    """Verify recording live boat GPS position & depth sounder (Chartplotter / Quickdraw mode)."""
    activate_waterbody_instance("sevastopol_bays")

    point_payload = {
        "lat": 44.618,
        "lon": 33.525,
        "depth_m": 14.5,
        "sog_knots": 8.2,
        "cog_deg": 85.0
    }

    res = client.post("/api/telemetry/live-point", json=point_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["recorded"] is True
    assert data["waterbody_id"] == "sevastopol_bays"
    assert data["total_track_points"] >= 1

    # Fetch track GeoJSON
    track_res = client.get("/api/telemetry/live-track")
    assert track_res.status_code == 200
    track = track_res.json()
    assert track["type"] == "FeatureCollection"
    assert any(f["properties"].get("role") == "live_sounding_point" for f in track["features"])


def test_cleanup_reactivate_black_sea():
    """Restore default Black Sea grid so subsequent regression tests remain standard."""
    res = client.post("/api/waterbody/activate", json={"waterbody_id": "black_sea"})
    assert res.status_code == 200
