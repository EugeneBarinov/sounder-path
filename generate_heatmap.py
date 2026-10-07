"""
Bathymetric Heatmap Generator
=============================
Reprojects marine bathymetric GeoTIFF elevation grids to EPSG:3857 (Web Mercator)
and produces a color-classified RGBA raster overlay for ECDIS/MapLibre visualization.
Focuses precisely on the marine navigation area to ensure pixel-perfect alignment
with coastal charts.

Depth classifications:
- Danger / Shallow (< 5.0m): Red (#EF4444)
- Warning / Caution (5.0m - 20.0m): Amber (#F59E0B)
- Safe / Deep (> 20.0m): Cyan/Blue (#38BDF8)
"""

import argparse
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform_bounds
from PIL import Image


def generate_heatmap(
    input_path: Path,
    output_path: Path,
    scale_factor: int = 3,
    dst_crs: str = "EPSG:3857",
) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Source raster not found at: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading bathymetry from {input_path}...")
    with rasterio.open(input_path) as src:
        # Exact marine coverage bounds in Black Sea / Sea of Azov
        b_left = float(src.bounds.left)      # 33.12291666666667
        b_bottom = float(src.bounds.bottom)  # 43.12291666666667
        b_right = 41.0                       # Covers entire Crimean coast, Kerch, Taman, Novorossiysk
        b_top = 47.3                         # Covers Sea of Azov & Karkinit bay (sea ends at ~47.28)

        win = from_bounds(b_left, b_bottom, b_right, b_top, src.transform)
        win_data = src.read(1, window=win)
        win_transform = src.window_transform(win)

        dst_w = max(1, win_data.shape[1] // scale_factor)
        dst_h = max(1, win_data.shape[0] // scale_factor)

        dst_transform, dst_w, dst_h = calculate_default_transform(
            src.crs,
            dst_crs,
            win_data.shape[1],
            win_data.shape[0],
            b_left,
            b_bottom,
            b_right,
            b_top,
            dst_width=dst_w,
            dst_height=dst_h,
        )

        dst_data = np.zeros((dst_h, dst_w), dtype=np.float32)

        print(f"Reprojecting marine sector to {dst_crs} ({dst_w}x{dst_h})...")
        reproject(
            source=win_data,
            destination=dst_data,
            src_transform=win_transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )

        bounds_4326 = transform_bounds(
            dst_crs,
            "EPSG:4326",
            *rasterio.transform.array_bounds(dst_h, dst_w, dst_transform),
        )

    # Invert negative elevation to positive depth (meters)
    depths = -dst_data
    depths = np.nan_to_num(depths, nan=0.0)
    depths = np.where(depths < 0.0, 0.0, depths)

    # Color classification
    rgba = np.zeros((dst_h, dst_w, 4), dtype=np.uint8)

    shallow = (depths > 0) & (depths <= 5.0)
    medium = (depths > 5.0) & (depths <= 20.0)
    deep = depths > 20.0

    rgba[shallow] = [239, 68, 68, 190]   # Red
    rgba[medium] = [245, 158, 11, 160]   # Amber
    rgba[deep] = [56, 189, 248, 110]     # Cyan

    img = Image.fromarray(rgba)
    img.save(output_path, optimize=True)

    # Exact MapLibre GL coordinates: [ [top-left], [top-right], [bottom-right], [bottom-left] ]
    maplibre_coords = [
        [round(bounds_4326[0], 6), round(bounds_4326[3], 6)],
        [round(bounds_4326[2], 6), round(bounds_4326[3], 6)],
        [round(bounds_4326[2], 6), round(bounds_4326[1], 6)],
        [round(bounds_4326[0], 6), round(bounds_4326[1], 6)],
    ]

    metadata = {
        "bounds_4326": bounds_4326,
        "maplibre_coordinates": maplibre_coords,
        "width": dst_w,
        "height": dst_h,
    }

    meta_path = output_path.parent / "heatmap_bounds.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"Heatmap written to: {output_path}")
    print(f"Bounds metadata written to: {meta_path}")
    print(f"MapLibre coordinates: {maplibre_coords}")


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Generate bathymetric map overlay from GeoTIFF.")
    parser.add_argument(
        "--input",
        type=Path,
        default=base_dir / "data" / "raw" / "E8_2024.tif",
        help="Input bathymetry GeoTIFF path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=base_dir / "frontend" / "heatmap.png",
        help="Output PNG overlay path",
    )
    parser.add_argument(
        "--scale-factor",
        type=int,
        default=3,
        help="Downscaling factor for preview raster (default: 3)",
    )

    args = parser.parse_args()
    generate_heatmap(args.input, args.output, args.scale_factor)
