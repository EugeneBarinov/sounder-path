import pytest
from fastapi.testclient import TestClient
from backend.main import app, waterbody_mgr


@pytest.fixture
def client():
    return TestClient(app)


def test_crimea_dataset_summary(client):
    """Test retrieval of Crimea hydrographic dataset summary."""
    resp = client.get("/api/crimea/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dataset_loaded"] is True
    assert data["lighthouses_count"] == 25
    assert data["anchorages_count"] >= 8
    assert data["piers_and_marinas_count"] >= 1000
    assert "bathymetry_115m" in data
    assert data["bathymetry_115m"]["available"] is True


def test_crimea_lighthouses(client):
    """Test Crimean lighthouses endpoint."""
    resp = client.get("/api/crimea/lighthouses")
    assert resp.status_code == 200
    lights = resp.json()
    assert isinstance(lights, list)
    assert len(lights) == 25
    names = [l["name"] for l in lights]
    # Check for famous Crimean lighthouses
    assert any("Херсонес" in n for n in names)
    assert any("Меганом" in n for n in names)
    assert any("Еникаль" in n for n in names)


def test_crimea_anchorages(client):
    """Test official Crimean anchorages and roadsteads."""
    resp = client.get("/api/crimea/anchorages")
    assert resp.status_code == 200
    anc = resp.json()
    assert isinstance(anc, list)
    assert len(anc) >= 8
    names = [a["name"] for a in anc]
    assert any("Севастопол" in n for n in names)
    assert any("Инкерман" in n for n in names)
    assert any("Балаклав" in n for n in names)
    assert any("Донузлав" in n for n in names)


def test_crimea_piers(client):
    """Test Crimean piers and marinas endpoint with bbox filtering."""
    resp = client.get("/api/crimea/piers?limit=50")
    assert resp.status_code == 200
    piers = resp.json()
    assert len(piers) <= 50
    assert len(piers) > 0

    # Filter for Sevastopol region: lon 33.45 to 33.65, lat 44.55 to 44.65
    resp_filtered = client.get("/api/crimea/piers?min_lon=33.45&min_lat=44.55&max_lon=33.65&max_lat=44.65&limit=100")
    assert resp_filtered.status_code == 200
    sev_piers = resp_filtered.json()
    assert len(sev_piers) > 0
    for p in sev_piers:
        assert 33.45 <= p["lon"] <= 33.65
        assert 44.55 <= p["lat"] <= 44.65


def test_crimea_rivers_search(client):
    """Test searching for Crimean rivers and waterways in local catalog."""
    resp = client.get("/api/geo/search?q=Кача")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    first = data[0]
    assert "Кача" in first["name"] or "Кача" in first["display_name"]
    assert first.get("is_crimea_official") is True or first.get("is_preset") is True


def test_crimea_marina_search(client):
    """Test searching for Crimean marinas in local catalog."""
    resp = client.get("/api/geo/search?q=Золотой+символ")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    # Either local preset (balaklava_bay) or crimea pier record
    names = [d["name"] for d in data]
    assert any("Золотой символ" in n or "Балаклав" in n for n in names)
