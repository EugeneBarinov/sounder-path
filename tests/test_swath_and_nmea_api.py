"""
Integration tests for NMEA 0183 REST/WebSocket endpoints and Swath Bathymetric Fusion
=====================================================================================
Tests:
- /api/nmea/status returns accurate state and packet diagnostics
- /api/nmea/feed parses raw sentences and updates vessel state
- /api/telemetry/live-point executes acoustic swath fusion (IDW footprint)
- Deeper water sounder measurement updates grid (shoal-bias bug resolved)
"""

import pytest
import numpy as np
from fastapi.testclient import TestClient
from backend.main import app, nmea_mgr, waterbody_mgr
from backend.core.grid import BathymetricGrid
from backend.core.waterbodies import Waterbody


@pytest.fixture
def client():
    return TestClient(app)


def test_api_nmea_status(client):
    response = client.get("/api/nmea/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_running" in data
    assert "host" in data
    assert "port" in data
    assert "vessel_state" in data
    assert "fix_status" in data["vessel_state"]


def test_api_nmea_feed_valid_sentence(client):
    # SDDPT sentence: depth 18.5m, offset 0.0m
    body = "SDDPT,18.5,0.0"
    cs = 0
    for c in body:
        cs ^= ord(c)
    sentence = f"${body}*{cs:02X}"

    response = client.post("/api/nmea/feed", json={"sentence": sentence, "source": "TEST_RUNNER"})
    assert response.status_code == 200
    res = response.json()
    assert res["accepted"] is True
    assert res["vessel_state"]["depth_m"] == 18.5
    assert res["vessel_state"]["depth_valid"] is True


def test_api_nmea_feed_corrupted_sentence(client):
    # Corrupted sentence with invalid checksum
    bad_sentence = "$SDDPT,18.5,0.0*FF"
    response = client.post("/api/nmea/feed", json={"sentence": bad_sentence, "source": "TEST_RUNNER"})
    assert response.status_code == 200
    res = response.json()
    assert res["accepted"] is False


def test_swath_fusion_accepts_deeper_water_and_interpolates_footprint():
    # Construct a high-resolution hydrographic grid (~2.2m per cell) initialized with 2.0m depth
    depths = np.full((25, 25), 2.0, dtype=np.float32)
    grid = BathymetricGrid(
        lat_min=44.6100, lat_max=44.6105,
        lon_min=33.5100, lon_max=33.5105,
        depths=depths
    )
    test_wb = Waterbody(
        id="test_swath_wb",
        name="Test Swath Waterbody",
        category="sea",
        description="Hydrographic swath acoustic test waterbody",
        bounds=[33.5100, 44.6100, 33.5105, 44.6105],
        center=[33.51025, 44.61025],
        zoom=18,
        grid=grid
    )
    waterbody_mgr.waterbodies["test_swath_wb"] = test_wb
    waterbody_mgr.set_active("test_swath_wb")

    try:
        center_lat, center_lon = 44.61025, 33.51025
        r, c = grid.lonlat_to_cell(center_lon, center_lat)

        # Initial depth is 2.0m
        assert grid.depths[r, c] == 2.0

        # Vessel sounder measures 14.0m (deeper dredged water) with 30° acoustic cone
        res = waterbody_mgr.log_live_telemetry(
            lat=center_lat,
            lon=center_lon,
            depth_m=14.0,
            sog_knots=4.0,
            cog_deg=90.0,
            beam_angle_deg=30.0,
            survey_mode=True
        )

        assert res["recorded"] is True
        assert res["fused_into_grid"] is True
        assert res["swath_cells_updated"] >= 1

        # Key check 1: Center cell is now 14.0m, NOT blocked at 2.0m!
        assert grid.depths[r, c] == 14.0

        # Key check 2: Adjacent cell in acoustic footprint is also updated
        adj_depth = grid.depths[r + 1, c]
        assert adj_depth > 2.0  # Has been updated by beam footprint
    finally:
        waterbody_mgr.set_active("black_sea")
