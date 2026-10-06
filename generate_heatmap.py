"""
Bathymetric Heatmap Generator
=============================
Reprojects regional bathymetric GeoTIFF elevation grids to EPSG:3857 (Web Mercator)
and produces a color-classified RGBA raster overlay for ECDIS/MapLibre visualization.

Depth classifications:
- Danger / Shallow (< 5.0m): Red (#EF4444)
- Warning / Caution (5.0m - 20.0m): Amber (#F59E0B)
- Safe / Deep (> 20.0m): Cyan/Blue (#38BDF8)
"""

import argparse
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform_bounds
from PIL import Image


def generate_heatmap(
    input_path: Path,
    output_path: Path,
    scale_factor: int = 4,
    dst_crs: str = "EPSG:3857",
) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Source raster not found at: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading bathymetry from {input_path}...")
    with rasterio.open(input_path) as src:
        dst_width = max(1, src.width // scale_factor)
        dst_height = max(1, src.height // scale_factor)

        transform, width, height = calculate_default_transform(
            src.crs,
            dst_crs,
            src.width,
            src.height,
            *src.bounds,
            dst_width=dst_width,
            dst_height=dst_height,
        )

        dst_data = np.zeros((1, height, width), dtype=np.float32)

        print(f"Reprojecting to {dst_crs} ({width}x{height})...")
        reproject(
            source=rasterio.band(src, 1),
            destination=dst_data,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=dst_crs,
            resampling=Resampling.nearest,
        )

        bounds_4326 = transform_bounds(
            dst_crs,
            "EPSG:4326",
            *rasterio.transform.array_bounds(height, width, transform),
        )

    # Convert negative elevation to positive depth (meters)
    depths = -dst_data[0]
    depths = np.nan_to_num(depths, nan=-9999.0)

    # Color classification
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    shallow = (depths > 0) & (depths <= 5.0)
    medium = (depths > 5.0) & (depths <= 20.0)
    deep = depths > 20.0

    rgba[shallow] = [239, 68, 68, 180]   # Red
    rgba[medium] = [245, 158, 11, 150]   # Amber
    rgba[deep] = [56, 189, 248, 100]     # Cyan

    img = Image.fromarray(rgba)
    img.save(output_path, optimize=True)

    print(f"Heatmap written to: {output_path}")
    print(f"Geographic bounds (EPSG:4326): {bounds_4326}")


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
        default=4,
        help="Downscaling factor for preview raster (default: 4)",
    )

    args = parser.parse_args()
    generate_heatmap(args.input, args.output, args.scale_factor)
