"""
Unit tests for Sonar Bathymetric Processing & Interpolation
===========================================================
Validates CSV, NMEA 0183, GPX parsing, IDW 2D surface interpolation,
contour generation, and IHO S-44 Shoal-bias fusion.
"""

import pytest
import numpy as np
from backend.core.sonar import (
    parse_csv_soundings,
    parse_nmea_soundings,
    parse_gpx_soundings,
    parse_sonar_payload,
    interpolate_sonar_bathymetry,
    fuse_soundings_into_grid
)
from backend.core.grid import BathymetricGrid


def test_parse_csv_soundings_standard():
    csv_data = """lat,lon,depth
44.595,33.428,14.5
44.596,33.429,12.3
44.597,33.430,8.2
"""
    soundings = parse_csv_soundings(csv_data)
    assert len(soundings) == 3
    assert soundings[0]["lat"] == 44.595
    assert soundings[0]["lon"] == 33.428
    assert soundings[0]["depth_m"] == 14.5


def test_parse_csv_soundings_auto_headers_semicolon():
    csv_data = """Latitude;Longitude;Sounding
44.600;33.450;5.4
44.601;33.451;4.8
"""
    soundings = parse_csv_soundings(csv_data)
    assert len(soundings) == 2
    assert soundings[0]["depth_m"] == 5.4


def test_parse_nmea_soundings():
    nmea_log = """
$GPGGA,123519,4435.700,N,03325.680,E,1,08,0.9,545.4,M,46.9,M,,*47
$SDDPT,14.2,0.0*57
$GPRMC,123520,A,4435.710,N,03325.690,E,5.2,180.0,230324,003.1,W*70
$SDDBT,40.0,f,12.19,M,6.6,F*3A
"""
    soundings = parse_nmea_soundings(nmea_log)
    assert len(soundings) >= 2
    assert abs(soundings[0]["lat"] - 44.595) < 0.001
    assert abs(soundings[0]["lon"] - 33.428) < 0.001
    assert soundings[0]["depth_m"] == 14.2


def test_parse_gpx_soundings():
    gpx_xml = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1">
  <trk>
    <trkseg>
      <trkpt lat="44.500" lon="33.400">
        <ele>-15.5</ele>
      </trkpt>
      <trkpt lat="44.501" lon="33.401">
        <depth>8.4</depth>
      </trkpt>
    </trkseg>
  </trk>
</gpx>
"""
    soundings = parse_gpx_soundings(gpx_xml)
    assert len(soundings) == 2
    assert soundings[0]["depth_m"] == 15.5
    assert soundings[1]["depth_m"] == 8.4


def test_interpolate_sonar_bathymetry():
    # Synthetic soundings in a small bay
    soundings = [
        {"lat": 44.500, "lon": 33.400, "depth_m": 12.0},
        {"lat": 44.502, "lon": 33.400, "depth_m": 8.0},
        {"lat": 44.500, "lon": 33.402, "depth_m": 6.0},
        {"lat": 44.502, "lon": 33.402, "depth_m": 2.5},
        {"lat": 44.501, "lon": 33.401, "depth_m": 5.0},
    ]

    res = interpolate_sonar_bathymetry(soundings, grid_size=30)
    assert res["success"] is True
    assert res["statistics"]["soundings_count"] == 5
    assert res["statistics"]["min_depth_m"] == 2.5
    assert res["statistics"]["max_depth_m"] == 12.0
    assert len(res["geojson"]["features"]) >= 1

    # Check survey boundary polygon
    boundary = [f for f in res["geojson"]["features"] if f["properties"].get("role") == "survey_boundary"]
    assert len(boundary) == 1


def test_fuse_soundings_into_grid_shoal_bias():
    depths = np.full((10, 10), 20.0, dtype=np.float32)
    grid = BathymetricGrid(
        lat_min=44.0, lat_max=45.0,
        lon_min=33.0, lon_max=34.0,
        depths=depths
    )

    # Sounder recorded a shallow 3.5m shoal at (33.5, 44.5)
    soundings = [{"lon": 33.5, "lat": 44.5, "depth_m": 3.5}]
    updated = fuse_soundings_into_grid(grid, soundings)
    assert updated == 1

    r, c = grid.lonlat_to_cell(33.5, 44.5)
    assert grid.get_depth(r, c) == 3.5

    # If sounder measures 25.0m deeper, shoal-bias keeps the 3.5m danger!
    deeper = [{"lon": 33.5, "lat": 44.5, "depth_m": 25.0}]
    updated_deeper = fuse_soundings_into_grid(grid, deeper)
    assert updated_deeper == 0
    assert grid.get_depth(r, c) == 3.5
