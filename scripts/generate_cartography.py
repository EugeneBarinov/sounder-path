"""
Cartographic Assets Generator for SeaPath ECDIS
==============================================
Generates professional nautical cartography assets:
1. High-resolution Bathymetric Shaded Relief (Hillshade) with IHO S-52 depth ramp.
2. Vector Depth Contours (Isobaths) with Marching Squares at [1, 2, 3, 5, 10, 15, 20, 30, 50, 100, 200, 500, 1000, 1500, 2000] meters.
3. Spot Soundings with nautical meters/decimeters formatting and peak shoal detection.
"""

import json
from pathlib import Path
import numpy as np
from PIL import Image
import rasterio
from rasterio.windows import from_bounds
import contourpy

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_RAW = BASE_DIR / "data" / "raw"
FRONTEND_DIR = BASE_DIR / "frontend"
TIF_PATH = DATA_RAW / "E8_2024.tif"

BOUNDS_4326 = [33.12291666666667, 43.122706433930475, 41.00004634664479, 47.3]
MAPLIBRE_COORDS = [
    [33.122917, 47.3],
    [41.000046, 47.3],
    [41.000046, 43.122706],
    [33.122917, 43.122706]
]

CONTOUR_LEVELS = [1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 1500.0, 2000.0]


def generate_cartography():
    print(f"Loading GeoTIFF bathymetry from {TIF_PATH}...")
    if not TIF_PATH.exists():
        print(f"Error: {TIF_PATH} not found.")
        return False

    with rasterio.open(TIF_PATH) as src:
        win = from_bounds(*BOUNDS_4326, src.transform)
        raw = src.read(1, window=win)
        print(f"Extracted raster shape: {raw.shape}")

    # Clean nodata / nan -> convert negative elevations to positive metric depths
    raw = np.where(np.isnan(raw), 0.0, raw)
    depths = np.clip(-raw, 0.0, 3500.0)

    # Subsample by factor 3 for optimal resolution and lightweight assets
    sub = 3
    d = depths[::sub, ::sub]
    ny, nx = d.shape
    print(f"Grid dimensions after subsampling: {ny}x{nx}")

    xs = np.linspace(BOUNDS_4326[0], BOUNDS_4326[2], nx)
    ys = np.linspace(BOUNDS_4326[3], BOUNDS_4326[1], ny)

    # -------------------------------------------------------------------------
    # 1. Generate Bathymetric Hillshade & Shaded Relief Raster
    # -------------------------------------------------------------------------
    print("Computing multi-directional shaded relief and bathymetric color ramp...")
    # Gradients for slope and aspect
    dy, dx = np.gradient(d)
    slope = np.pi / 2.0 - np.arctan(np.sqrt(dx * dx + dy * dy))
    aspect = np.arctan2(-dx, dy)

    # Sun illumination from NW (315 deg) at 45 deg altitude
    alt_rad = 45.0 * np.pi / 180.0
    az_rad = 315.0 * np.pi / 180.0
    shade = np.sin(alt_rad) * np.sin(slope) + np.cos(alt_rad) * np.cos(slope) * np.cos(az_rad - aspect)
    # Balanced contrast for underwater topography
    shade = np.clip(0.65 + 0.45 * (shade - 0.5), 0.45, 1.25)

    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    water_mask = d > 0.05

    # Logarithmic depth normalization for natural maritime color gradations
    log_d = np.log1p(np.clip(d, 0.0, 2200.0)) / np.log1p(2200.0)

    # IHO S-52 inspired marine color ramp:
    # 0-5m (Very Shallow): Teal / Aquamarine (34, 197, 246)
    # 5-50m (Coastal / Shelf): Mid Blue (14, 116, 185)
    # >50m (Deep Ocean Basin): Deep Abyssal Indigo (8, 28, 58)
    r_chan = np.where(log_d < 0.4, 30 * (1 - log_d / 0.4) + 14 * (log_d / 0.4), 14 * (1 - (log_d - 0.4) / 0.6) + 8 * ((log_d - 0.4) / 0.6)) * shade
    g_chan = np.where(log_d < 0.4, 185 * (1 - log_d / 0.4) + 116 * (log_d / 0.4), 116 * (1 - (log_d - 0.4) / 0.6) + 28 * ((log_d - 0.4) / 0.6)) * shade
    b_chan = np.where(log_d < 0.4, 235 * (1 - log_d / 0.4) + 185 * (log_d / 0.4), 185 * (1 - (log_d - 0.4) / 0.6) + 68 * ((log_d - 0.4) / 0.6)) * shade

    rgba[water_mask, 0] = np.clip(r_chan[water_mask], 0, 255).astype(np.uint8)
    rgba[water_mask, 1] = np.clip(g_chan[water_mask], 0, 255).astype(np.uint8)
    rgba[water_mask, 2] = np.clip(b_chan[water_mask], 0, 255).astype(np.uint8)
    rgba[water_mask, 3] = 195  # 76% opacity for smooth layering over basemap

    hillshade_path = FRONTEND_DIR / "bathymetry_hillshade.png"
    img = Image.fromarray(rgba, "RGBA")
    img.save(hillshade_path, optimize=True)
    print(f"Saved bathymetric hillshade to {hillshade_path} ({hillshade_path.stat().st_size / 1024:.1f} KB)")

    # -------------------------------------------------------------------------
    # 2. Generate Vector Depth Contours (Isobaths)
    # -------------------------------------------------------------------------
    print("Generating vector isobaths via contourpy Marching Squares...")
    c_gen = contourpy.contour_generator(xs, ys, d)
    features = []

    for lvl in CONTOUR_LEVELS:
        lines = c_gen.lines(lvl)
        is_index = lvl in [5.0, 10.0, 20.0, 50.0, 100.0, 200.0]
        label_text = f"{int(lvl) if lvl.is_integer() else lvl}m"

        for line in lines:
            if len(line) < 3:
                continue
            coords = [[round(float(pt[0]), 4), round(float(pt[1]), 4)] for pt in line]
            features.append({
                "type": "Feature",
                "properties": {
                    "depth": float(lvl),
                    "label": label_text,
                    "is_index": is_index
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords
                }
            })

    contours_geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    contours_path = FRONTEND_DIR / "contours.geojson"
    with open(contours_path, "w", encoding="utf-8") as f:
        json.dump(contours_geojson, f, separators=(",", ":"))
    print(f"Saved {len(features)} vector isobaths to {contours_path} ({contours_path.stat().st_size / (1024 * 1024):.2f} MB)")

    # -------------------------------------------------------------------------
    # 3. Generate Nautical Spot Soundings
    # -------------------------------------------------------------------------
    print("Extracting representative spot soundings and critical shoals...")
    soundings = []
    step_y = 28
    step_x = 42

    for r in range(step_y // 2, ny, step_y):
        for c in range(step_x // 2, nx, step_x):
            val = float(d[r, c])
            if val < 0.5 or val > 1200.0:
                continue

            # Check if this cell is a local shallow peak
            r_min = max(0, r - 3)
            r_max = min(ny, r + 4)
            c_min = max(0, c - 3)
            c_max = min(nx, c + 4)
            local_patch = d[r_min:r_max, c_min:c_max]
            is_peak = bool(val == np.min(local_patch) and val < 20.0)

            lon = xs[c]
            lat = ys[r]
            meters = int(val)
            decimeters = int(round((val - meters) * 10)) % 10

            soundings.append({
                "type": "Feature",
                "properties": {
                    "depth": round(val, 1),
                    "meters": meters,
                    "decimeters": decimeters,
                    "label": f"{val:.1f}",
                    "is_peak": is_peak
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(lon, 4), round(lat, 4)]
                }
            })

    soundings_geojson = {
        "type": "FeatureCollection",
        "features": soundings
    }
    soundings_path = FRONTEND_DIR / "soundings.geojson"
    with open(soundings_path, "w", encoding="utf-8") as f:
        json.dump(soundings_geojson, f, separators=(",", ":"))
    print(f"Saved {len(soundings)} spot soundings to {soundings_path} ({soundings_path.stat().st_size / 1024:.1f} KB)")

    print("\nAll cartographic assets generated successfully!")
    return True


if __name__ == "__main__":
    generate_cartography()
