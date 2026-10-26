"""
Bathymetric Grid Processing Module
==================================
Manages 2D bathymetric elevation grids loaded from regional GeoTIFF rasters.
Provides spatial conversions, conservative downsampling, Bresenham raycasting,
and supplemental survey soundings data fusion.
"""

from typing import Tuple, List, Dict, Any, Optional
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
        fairway_weights: Optional[np.ndarray] = None,
        land_mask: Optional[np.ndarray] = None,
        nodata_mask: Optional[np.ndarray] = None,
    ):
        self.lat_min = float(lat_min)
        self.lat_max = float(lat_max)
        self.lon_min = float(lon_min)
        self.lon_max = float(lon_max)
        self.depths = depths
        self.rows, self.cols = depths.shape
        self.fairway_weights = (
            fairway_weights
            if fairway_weights is not None
            else np.ones((self.rows, self.cols), dtype=np.float32)
        )
        self.land_mask = (
            land_mask
            if land_mask is not None
            else (depths <= 0.0)
        )
        self.nodata_mask = (
            nodata_mask
            if nodata_mask is not None
            else np.zeros((self.rows, self.cols), dtype=bool)
        )
        self.is_synthetic = False
        self.surveyed_mask = np.zeros((self.rows, self.cols), dtype=bool)

    @property
    def width(self) -> int:
        return self.cols

    @property
    def height(self) -> int:
        return self.rows

    def get_depth(self, r: int, c: int) -> float:
        """Returns seabed water depth in meters, or 0.0 if out of bounds."""
        if 0 <= r < self.rows and 0 <= c < self.cols:
            return float(self.depths[r, c])
        return 0.0

    def get_fairway_weight(self, r: int, c: int) -> float:
        """Returns fairway transit modifier for grid cell [0.3..1.0..1000.0]."""
        if 0 <= r < self.rows and 0 <= c < self.cols:
            return float(self.fairway_weights[r, c])
        return 1.0

    def is_restricted(self, r: int, c: int) -> bool:
        """Returns True if cell is inside a prohibited/danger navigational zone."""
        return self.get_fairway_weight(r, c) >= 500.0

    def attach_fairways(self, fairway_weights: np.ndarray) -> None:
        """Attach precomputed 2D fairway and navigational restrictions weights."""
        if fairway_weights.shape == (self.rows, self.cols):
            self.fairway_weights = fairway_weights

    def is_within_coverage(self, lon: float, lat: float) -> bool:
        """Checks if geographic coordinate lies within raster bounding box."""
        return (self.lon_min <= lon <= self.lon_max) and (self.lat_min <= lat <= self.lat_max)

    @classmethod
    def load_from_geotiff(cls, filepath: str | Path) -> "BathymetricGrid":
        """
        Load bathymetric raster from a GeoTIFF dataset.
        Automatically reprojects any CRS (e.g. EPSG:3857, UTM) to WGS84 (EPSG:4326).
        EMODnet elevation models represent seabed depths as negative elevations,
        which are inverted into positive water-column depths.
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Bathymetry dataset not found at: {path}")

        with rasterio.open(path) as src:
            if src.crs is None:
                raise ValueError(f"GeoTIFF dataset at {path} has no defined Coordinate Reference System (CRS).")

            is_wgs84 = False
            if hasattr(src.crs, "to_epsg"):
                epsg_code = src.crs.to_epsg()
                is_wgs84 = (epsg_code == 4326)

            if not is_wgs84:
                # Automatic on-the-fly reprojection to WGS84 (EPSG:4326)
                from rasterio.warp import calculate_default_transform, reproject, Resampling
                target_crs = "EPSG:4326"
                dst_transform, dst_width, dst_height = calculate_default_transform(
                    src.crs, target_crs, src.width, src.height, *src.bounds
                )
                nodata_val = float(src.nodata) if src.nodata is not None else -9999.0
                destination = np.full((dst_height, dst_width), nodata_val, dtype=np.float32)
                reproject(
                    source=rasterio.band(src, 1),
                    destination=destination,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=target_crs,
                    resampling=Resampling.bilinear,
                    dst_nodata=nodata_val,
                )
                finite_mask = np.isfinite(destination) & (destination != nodata_val)
                nodata_mask = (destination == nodata_val) | (~np.isfinite(destination))
                destination = np.where(finite_mask, destination, 0.0)
                land_mask = (~nodata_mask) & (destination > 0.0)
                depths = -destination
                depths = np.nan_to_num(depths, nan=0.0, posinf=0.0, neginf=0.0)
                depths = np.where((depths < 0.0) | land_mask | nodata_mask, 0.0, depths)

                lon_min = dst_transform.c
                lat_max = dst_transform.f
                lon_max = lon_min + dst_width * dst_transform.a
                lat_min = lat_max + dst_height * dst_transform.e

                grid_obj = cls(
                    lat_min=min(lat_min, lat_max),
                    lat_max=max(lat_min, lat_max),
                    lon_min=min(lon_min, lon_max),
                    lon_max=max(lon_min, lon_max),
                    depths=depths,
                    land_mask=land_mask,
                    nodata_mask=nodata_mask,
                )
                grid_obj.is_synthetic = False
                return grid_obj

            # Native EPSG:4326 reading
            raw_data = src.read(1, masked=True)
            if src.nodata is not None:
                raw_data = np.ma.masked_equal(raw_data, src.nodata)

            nodata_mask = np.ma.getmaskarray(raw_data)
            unmasked_elev = raw_data.filled(0.0).astype(np.float32)
            finite_mask = np.isfinite(unmasked_elev)
            nodata_mask = nodata_mask | (~finite_mask)
            unmasked_elev = np.where(finite_mask, unmasked_elev, 0.0)

            # Terrestrial elevation above sea level is dry land
            land_mask = (~nodata_mask) & (unmasked_elev > 0.0)

            # Invert negative elevation to positive water depth
            depths = -unmasked_elev
            depths = np.nan_to_num(depths, nan=0.0, posinf=0.0, neginf=0.0)

            # Mask terrestrial elevation and nodata pixels to 0.0m
            depths = np.where((depths < 0.0) | land_mask | nodata_mask, 0.0, depths)
            bounds = src.bounds

        grid_obj = cls(
            lat_min=bounds.bottom,
            lat_max=bounds.top,
            lon_min=bounds.left,
            lon_max=bounds.right,
            depths=depths,
            land_mask=land_mask,
            nodata_mask=nodata_mask,
        )
        grid_obj.is_synthetic = False
        return grid_obj

    @classmethod
    def create_synthetic(
        cls,
        lat_min: float = 43.12,
        lat_max: float = 46.50,
        lon_min: float = 33.12,
        lon_max: float = 40.00,
        rows: int = 400,
        cols: int = 600,
    ) -> "BathymetricGrid":
        """
        Generates a synthetic bathymetric grid for demonstration mode
        when regional GeoTIFF datasets are not present locally.
        """
        lats = np.linspace(lat_max, lat_min, rows)
        lons = np.linspace(lon_min, lon_max, cols)
        lon_grid, lat_grid = np.meshgrid(lons, lats)

        # Realistic shelf-to-basin depth gradient
        norm_lat = (lat_max - lat_grid) / (lat_max - lat_min)
        depths = 15.0 + 900.0 * (norm_lat ** 1.8)

        # Synthetic shoreline / shallow sandbanks
        coast_mask = (lat_grid > 45.3) & (lon_grid < 34.2)
        depths[coast_mask] = 0.0

        grid_obj = cls(
            lat_min=lat_min,
            lat_max=lat_max,
            lon_min=lon_min,
            lon_max=lon_max,
            depths=depths.astype(np.float32),
        )
        grid_obj.is_synthetic = True
        return grid_obj

    def downsample(self, factor: int = 10) -> "BathymetricGrid":
        """
        Generate a downsampled grid for hierarchical routing.
        Uses block-minimum pooling to guarantee conservative depth estimations
        (worst-case shoal clearance) across each aggregated zone.
        Recalculates spatial bounds to match the cropped cell extents.
        """
        h = (self.rows // factor) * factor
        w = (self.cols // factor) * factor

        lat_step = (self.lat_max - self.lat_min) / max(1, self.rows)
        lon_step = (self.lon_max - self.lon_min) / max(1, self.cols)

        # Adjust bounds to exactly correspond to cropped pixel matrix
        cropped_lat_min = self.lat_max - h * lat_step
        cropped_lon_max = self.lon_min + w * lon_step

        cropped = self.depths[:h, :w]
        blocks = cropped.reshape(h // factor, factor, w // factor, factor)
        coarse_depths = blocks.min(axis=(1, 3))

        coarse_fw = None
        if self.fairway_weights is not None and self.fairway_weights.shape == (self.rows, self.cols):
            cropped_fw = self.fairway_weights[:h, :w]
            fw_blocks = cropped_fw.reshape(h // factor, factor, w // factor, factor)
            # Preserves navigational danger (>=500) via max pooling, else min pooling for fairway discount
            max_fw = fw_blocks.max(axis=(1, 3))
            min_fw = fw_blocks.min(axis=(1, 3))
            coarse_fw = np.where(max_fw >= 500.0, max_fw, min_fw).astype(np.float32)

        coarse_land = None
        if self.land_mask is not None and self.land_mask.shape == (self.rows, self.cols):
            cropped_land = self.land_mask[:h, :w]
            land_blocks = cropped_land.reshape(h // factor, factor, w // factor, factor)
            coarse_land = land_blocks.any(axis=(1, 3))

        coarse_nodata = None
        if self.nodata_mask is not None and self.nodata_mask.shape == (self.rows, self.cols):
            cropped_nodata = self.nodata_mask[:h, :w]
            nodata_blocks = cropped_nodata.reshape(h // factor, factor, w // factor, factor)
            coarse_nodata = nodata_blocks.any(axis=(1, 3))

        grid_obj = BathymetricGrid(
            lat_min=cropped_lat_min,
            lat_max=self.lat_max,
            lon_min=self.lon_min,
            lon_max=cropped_lon_max,
            depths=coarse_depths,
            fairway_weights=coarse_fw,
            land_mask=coarse_land,
            nodata_mask=coarse_nodata,
        )
        grid_obj.is_synthetic = getattr(self, "is_synthetic", False)
        return grid_obj

    def get_cell_coords(self, r: int, c: int) -> Tuple[float, float]:
        """Convert grid index (row, col) to center coordinates (lat, lon)."""
        lat_step = max(1e-9, (self.lat_max - self.lat_min) / max(1, self.rows))
        lon_step = max(1e-9, (self.lon_max - self.lon_min) / max(1, self.cols))
        lat = self.lat_max - (r + 0.5) * lat_step
        lon = self.lon_min + (c + 0.5) * lon_step
        return lat, lon

    def lonlat_to_cell(self, lon: float, lat: float) -> Tuple[int, int]:
        """Convert geographic coordinates (lon, lat) to bounded grid indices (row, col)."""
        lat_step = max(1e-9, (self.lat_max - self.lat_min) / max(1, self.rows))
        lon_step = max(1e-9, (self.lon_max - self.lon_min) / max(1, self.cols))

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

        with open(path, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                norm_row = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
                try:
                    lat_val = norm_row.get("lat") or norm_row.get("latitude")
                    lon_val = norm_row.get("lon") or norm_row.get("lng") or norm_row.get("longitude")
                    depth_val = norm_row.get("depth") or norm_row.get("depth_m") or norm_row.get("sounding")
                    if lat_val is None or lon_val is None or depth_val is None:
                        continue
                    lat = float(lat_val)
                    lon = float(lon_val)
                    depth = float(depth_val)
                    if not (np.isfinite(lat) and np.isfinite(lon) and np.isfinite(depth)):
                        continue
                    if depth < 0.0 or depth > 11000.0:
                        continue
                except (ValueError, TypeError):
                    continue

                if not (self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max):
                    continue

                r, c = self.lonlat_to_cell(lon, lat)

                if 0 <= r < self.rows and 0 <= c < self.cols:
                    dredged_val = str(norm_row.get("dredged") or norm_row.get("is_canal") or "").strip().lower()
                    is_dredged = dredged_val in ("1", "true", "yes", "dredged")
                    # Terrestrial Land Protection:
                    # Sounding points cannot overwrite known dry land unless explicitly flagged as a dredged channel
                    if self.land_mask is not None and self.land_mask[r, c] and not is_dredged:
                        continue

                    curr_d = float(self.depths[r, c])
                    # If cell was missing data (nodata), sounding establishes initial surveyed depth
                    if self.nodata_mask is not None and self.nodata_mask[r, c]:
                        self.depths[r, c] = max(0.0, depth)
                        self.nodata_mask[r, c] = False
                    elif curr_d > 0.0 and depth > 0.0:
                        # IHO S-44 Shoal-bias safety rule:
                        # In navigable water, retain the shallower (more hazardous) depth to prevent grounding.
                        self.depths[r, c] = min(curr_d, depth)
                    elif is_dredged and depth > 0.0:
                        self.depths[r, c] = depth
                        if self.land_mask is not None:
                            self.land_mask[r, c] = False

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
                    "is_synthetic": getattr(self, "is_synthetic", False),
                    "bounds": [self.lon_min, self.lat_min, self.lon_max, self.lat_max],
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
