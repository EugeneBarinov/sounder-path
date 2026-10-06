"""
Bathymetric Grid Processing Module
==================================
Manages 2D bathymetric elevation grids loaded from regional GeoTIFF rasters.
Provides spatial conversions, conservative downsampling, Bresenham raycasting,
and supplemental survey soundings data fusion.
"""

from typing import Tuple, List, Dict, Any
import csv
import os
from pathlib import Path
import numpy as np
import rasterio


class BathymetricGrid:
    """
    Structured 2D bathymetric grid.
    Depths are maintained as positive metric values (meters below surface datum).
    Landmass and unnavigable/nodata cells are clipped to 0.0m.
    """

    def __init__(
        self,
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
        depths: np.ndarray,
    ):
        self.lat_min = float(lat_min)
        self.lat_max = float(lat_max)
        self.lon_min = float(lon_min)
        self.lon_max = float(lon_max)
        self.depths = depths
        self.rows, self.cols = depths.shape

    @classmethod
    def load_from_geotiff(cls, filepath: str | Path) -> "BathymetricGrid":
        """
        Load bathymetric raster from a GeoTIFF dataset.
        EMODnet elevation models represent seabed depths as negative elevations,
        which are inverted into positive water-column depths.
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Bathymetry dataset not found at: {path}")

        with rasterio.open(path) as src:
            data = src.read(1).astype(np.float32)
            # Invert negative elevation to positive depth
            depths = -data
            depths = np.nan_to_num(depths, nan=0.0)

            # Mask terrestrial elevation (values above sea level become <= 0.0)
            depths = np.where(depths < 0.0, 0.0, depths)
            bounds = src.bounds

        return cls(
            lat_min=bounds.bottom,
            lat_max=bounds.top,
            lon_min=bounds.left,
            lon_max=bounds.right,
            depths=depths,
        )

    def downsample(self, factor: int = 10) -> "BathymetricGrid":
        """
        Generate a downsampled grid for hierarchical routing.
        Uses block-minimum pooling to guarantee conservative depth estimations
        (worst-case shoal clearance) across each aggregated zone.
        """
        h = (self.rows // factor) * factor
        w = (self.cols // factor) * factor

        cropped = self.depths[:h, :w]
        blocks = cropped.reshape(h // factor, factor, w // factor, factor)
        coarse_depths = blocks.min(axis=(1, 3))

        return BathymetricGrid(
            lat_min=self.lat_min,
            lat_max=self.lat_max,
            lon_min=self.lon_min,
            lon_max=self.lon_max,
            depths=coarse_depths,
        )

    def get_cell_coords(self, r: int, c: int) -> Tuple[float, float]:
        """Convert grid index (row, col) to center coordinates (lat, lon)."""
        lat_step = (self.lat_max - self.lat_min) / self.rows
        lon_step = (self.lon_max - self.lon_min) / self.cols
        lat = self.lat_max - (r + 0.5) * lat_step
        lon = self.lon_min + (c + 0.5) * lon_step
        return lat, lon

    def lonlat_to_cell(self, lon: float, lat: float) -> Tuple[int, int]:
        """Convert geographic coordinates (lon, lat) to bounded grid indices (row, col)."""
        lat_step = (self.lat_max - self.lat_min) / self.rows
        lon_step = (self.lon_max - self.lon_min) / self.cols

        r = int((self.lat_max - lat) / lat_step)
        c = int((lon - self.lon_min) / lon_step)

        r = max(0, min(self.rows - 1, r))
        c = max(0, min(self.cols - 1, c))
        return r, c

    def sample_depth_along_segment(self, r0: int, c0: int, r1: int, c1: int) -> List[float]:
        """
        Sample continuous depth values along a line segment using Bresenham traversal.
        Matches line-of-sight verification to eliminate interpolation discrepancies.
        """
        readings: List[float] = []
        dr = abs(r1 - r0)
        dc = abs(c1 - c0)
        sr = 1 if r0 < r1 else -1
        sc = 1 if c0 < c1 else -1
        err = dr - dc

        r, c = r0, c0
        while True:
            if 0 <= r < self.rows and 0 <= c < self.cols:
                readings.append(float(self.depths[r, c]))
            else:
                readings.append(0.0)

            if r == r1 and c == c1:
                break

            e2 = 2 * err
            if e2 > -dc:
                err -= dc
                r += sr
            if e2 < dr:
                err += dr
                c += sc

        return readings

    def fuse_csv_data(self, filepath: str | Path) -> None:
        """
        Fuse supplemental high-resolution soundings (CSV format) into the active grid.
        Expected CSV headers: lat, lon, depth
        """
        path = Path(filepath)
        if not path.exists():
            return

        lat_step = (self.lat_max - self.lat_min) / self.rows
        lon_step = (self.lon_max - self.lon_min) / self.cols

        with open(path, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    lat = float(row["lat"])
                    lon = float(row["lon"])
                    depth = float(row["depth"])
                except (ValueError, KeyError):
                    continue

                if not (self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max):
                    continue

                r = int((self.lat_max - lat) / lat_step)
                c = int((lon - self.lon_min) / lon_step)

                if 0 <= r < self.rows and 0 <= c < self.cols:
                    self.depths[r, c] = depth

    def to_geojson(self) -> Dict[str, Any]:
        """Export grid boundary coverage as a GeoJSON FeatureCollection."""
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "coverage": True,
                    "rows": self.rows,
                    "cols": self.cols,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [self.lon_min, self.lat_max],
                        [self.lon_max, self.lat_max],
                        [self.lon_max, self.lat_min],
                        [self.lon_min, self.lat_min],
                        [self.lon_min, self.lat_max],
                    ]],
                },
            }],
        }
