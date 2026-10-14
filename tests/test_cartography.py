"""
Tests for Cartography Endpoints and Assets
=========================================
Validates the generation, API serving, and GeoJSON structure of
nautical vector isobaths, spot soundings, and bathymetric hillshade.
"""

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_cartography_contours_api():
    """Verify that /api/cartography/contours returns a valid GeoJSON FeatureCollection with isobaths."""
    response = client.get("/api/cartography/contours")
    assert response.status_code == 200
    data = response.json()
    assert data.get("type") == "FeatureCollection"
    features = data.get("features", [])
    assert len(features) > 100

    # Validate feature schema
    sample = features[0]
    assert sample["type"] == "Feature"
    assert "geometry" in sample
    assert sample["geometry"]["type"] in ["LineString", "MultiLineString"]
    props = sample["properties"]
    assert "depth" in props
    assert "label" in props
    assert "is_index" in props

    # Verify standard depth levels exist
    depths = {f["properties"]["depth"] for f in features}
    assert 5.0 in depths
    assert 10.0 in depths
    assert 20.0 in depths


def test_cartography_soundings_api():
    """Verify that /api/cartography/soundings returns nautical spot soundings with integer meters and decimeters."""
    response = client.get("/api/cartography/soundings")
    assert response.status_code == 200
    data = response.json()
    assert data.get("type") == "FeatureCollection"
    features = data.get("features", [])
    assert len(features) > 50

    sample = features[0]
    assert sample["type"] == "Feature"
    assert sample["geometry"]["type"] == "Point"
    props = sample["properties"]
    assert "depth" in props
    assert "meters" in props
    assert "decimeters" in props
    assert "label" in props
    assert "is_peak" in props


def test_cartography_hillshade_static():
    """Verify that bathymetry_hillshade.png is served statically and is a valid PNG."""
    response = client.get("/bathymetry_hillshade.png")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "image/png"
    # PNG signature: 89 50 4E 47 0D 0A 1A 0A
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(response.content) > 100_000
