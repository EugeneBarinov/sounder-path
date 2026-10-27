"""
Test Suite: Commercial-Grade Field Capabilities
================================================
Covers:
1. Spatial Deadband Decimation (anchored/docked stationary points filtering)
2. Rolling Median Heave / Wave filter (vertical wave oscillation smoothing)
3. Survey Multi-format Export (CSV XYZ, GeoJSON, GPX 1.1)
4. Serial COM port API lifecycle
5. Offline Vendor Assets availability
"""

import pytest
import time
import json
import numpy as np
from fastapi.testclient import TestClient
from backend.main import app, waterbody_mgr, nmea_mgr
from backend.core.waterbodies import Waterbody
from backend.core.grid import BathymetricGrid


@pytest.fixture
def client():
    return TestClient(app)


def test_offline_vendor_assets_served(client):
    """Verify MapLibre and Chart.js are served locally without CDN dependence."""
    r1 = client.get("/vendor/maplibre-gl.js")
    assert r1.status_code == 200
    assert len(r1.content) > 100000

    r2 = client.get("/vendor/maplibre-gl.css")
    assert r2.status_code == 200
    assert len(r2.content) > 10000

    r3 = client.get("/vendor/chart.js")
    assert r3.status_code == 200
    assert len(r3.content) > 50000


def test_spatial_deadband_decimation():
    """Verify stationary boat does not append redundant points to survey track."""
    grid = BathymetricGrid(
        lat_min=44.60, lat_max=44.62,
        lon_min=33.50, lon_max=33.52,
        depths=np.full((20, 20), 10.0, dtype=np.float32)
    )
    test_wb = Waterbody(
        id="test_deadband_wb",
        name="Test Deadband Waterbody",
        category="sea",
        description="Deadband test",
        bounds=[33.50, 44.60, 33.52, 44.62],
        center=[33.51, 44.61],
        zoom=14,
        grid=grid
    )
    waterbody_mgr.waterbodies["test_deadband_wb"] = test_wb
    waterbody_mgr.set_active("test_deadband_wb")

    try:
        # Clear initial track
        test_wb.live_track_points = []
        test_wb.last_logged_lat = None
        test_wb.last_logged_lon = None

        lat, lon = 44.6100, 33.5100

        # 1. First point should be recorded
        res1 = waterbody_mgr.log_live_telemetry(lat=lat, lon=lon, depth_m=10.0)
        assert res1["recorded"] is True
        assert res1["is_stationary"] is False
        assert len(test_wb.live_track_points) == 1

        # 2. Second point at same location within 1 second should be flagged as stationary
        res2 = waterbody_mgr.log_live_telemetry(lat=lat, lon=lon, depth_m=10.0)
        assert res2["is_stationary"] is True
        # Still 1 point in track, redundant noise discarded!
        assert len(test_wb.live_track_points) == 1

        # 3. Third point moved 5 meters away should be recorded
        lat_moved = lat + (5.0 / 111139.0)
        res3 = waterbody_mgr.log_live_telemetry(lat=lat_moved, lon=lon, depth_m=10.2)
        assert res3["is_stationary"] is False
        assert len(test_wb.live_track_points) == 2
    finally:
        waterbody_mgr.set_active("black_sea")


def test_rolling_median_heave_filter():
    """Verify rolling median smooths vertical wave oscillations and suppresses aeration spikes."""
    grid = BathymetricGrid(
        lat_min=44.60, lat_max=44.62,
        lon_min=33.50, lon_max=33.52,
        depths=np.full((20, 20), 10.0, dtype=np.float32)
    )
    test_wb = Waterbody(
        id="test_heave_wb",
        name="Test Heave Waterbody",
        category="sea",
        description="Heave test",
        bounds=[33.50, 44.60, 33.52, 44.62],
        center=[33.51, 44.61],
        zoom=14,
        grid=grid
    )
    waterbody_mgr.waterbodies["test_heave_wb"] = test_wb
    waterbody_mgr.set_active("test_heave_wb")

    try:
        test_wb.recent_soundings.clear()

        # Feed wave-oscillating depths: 10.0, 11.0 (wave crest), 9.0 (wave trough)
        waterbody_mgr.log_live_telemetry(lat=44.6101, lon=33.5101, depth_m=10.0)
        waterbody_mgr.log_live_telemetry(lat=44.6102, lon=33.5101, depth_m=11.0)
        res3 = waterbody_mgr.log_live_telemetry(lat=44.6103, lon=33.5101, depth_m=9.0)

        # Median of [10.0, 11.0, 9.0] is 10.0m
        assert res3["effective_depth_m"] == 10.0

        # Feed sudden aeration spike (e.g. 1.2m caused by cavitation bubbles under keel in 10m water)
        res4 = waterbody_mgr.log_live_telemetry(lat=44.6104, lon=33.5101, depth_m=1.2)
        # Spike should be suppressed by median filter
        assert res4["effective_depth_m"] >= 9.0
    finally:
        waterbody_mgr.set_active("black_sea")


def test_hydrographic_survey_export_formats(client):
    """Verify survey export in CSV (XYZ), GeoJSON, and GPX 1.1."""
    test_wb = waterbody_mgr.get_active()
    test_wb.live_track_points = [
        {"lat": 44.6150, "lon": 33.5250, "depth_m": 8.5, "sog_knots": 6.2, "cog_deg": 180.0, "timestamp": 1700000000.0},
        {"lat": 44.6160, "lon": 33.5260, "depth_m": 9.2, "sog_knots": 6.5, "cog_deg": 185.0, "timestamp": 1700000010.0}
    ]

    # 1. CSV Export
    r_csv = client.get("/api/surveys/export?format=csv")
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["Content-Type"]
    assert "attachment; filename=" in r_csv.headers["Content-Disposition"]
    csv_text = r_csv.content.decode("utf-8")
    assert "Longitude,Latitude,Depth_m" in csv_text
    assert "33.5250000,44.6150000,8.50" in csv_text

    # 2. GeoJSON Export
    r_geo = client.get("/api/surveys/export?format=geojson")
    assert r_geo.status_code == 200
    assert "geo+json" in r_geo.headers["Content-Type"]
    geo_data = r_geo.json()
    assert geo_data["type"] == "FeatureCollection"
    assert len(geo_data["features"]) == 2
    assert geo_data["features"][0]["properties"]["depth_m"] == 8.5

    # 3. GPX 1.1 Export
    r_gpx = client.get("/api/surveys/export?format=gpx")
    assert r_gpx.status_code == 200
    assert "gpx+xml" in r_gpx.headers["Content-Type"]
    gpx_text = r_gpx.content.decode("utf-8")
    assert "<gpx version=\"1.1\"" in gpx_text
    assert "<trkpt" in gpx_text
    assert "<depth>8.50</depth>" in gpx_text


def test_serial_com_api_endpoints(client):
    """Verify Serial COM start and stop endpoints."""
    # Test start
    r_start = client.post("/api/nmea/serial/start", json={"port": "COM99_TEST", "baudrate": 4800})
    assert r_start.status_code == 200
    res = r_start.json()
    assert res["port"] == "COM99_TEST"
    assert "status" in res

    # Test stop
    r_stop = client.post("/api/nmea/serial/stop")
    assert r_stop.status_code == 200
    res_stop = r_stop.json()
    assert res_stop["stopped"] is True
    assert res_stop["status"]["serial_is_running"] is False
