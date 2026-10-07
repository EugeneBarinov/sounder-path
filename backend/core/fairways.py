"""
ECDIS Navigational Fairways & Constraints Module
================================================
Manages S-57 / ENC vector features, traffic separation schemes (TSS),
dredged approach channels, and restricted navigational zones (RESARE).
Rasterizes vector fairway corridors directly onto bathymetric routing grids.
"""

from typing import Dict, Any, Optional
from pathlib import Path
import json
import numpy as np
from affine import Affine

try:
    from rasterio.features import rasterize
    _HAS_RASTERIO_FEATURES = True
except ImportError:
    _HAS_RASTERIO_FEATURES = False


DEFAULT_FAIRWAYS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "fairways.geojson"


def load_fairways_geojson(path: Path | str = DEFAULT_FAIRWAYS_PATH) -> Dict[str, Any]:
    """Load fairways and navigational restrictions GeoJSON feature collection."""
    path = Path(path)
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def rasterize_fairway_weights(
    grid_rows: int,
    grid_cols: int,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    geojson_path: Path | str = DEFAULT_FAIRWAYS_PATH,
) -> np.ndarray:
    """
    Rasterize vector fairways and restricted zones into a 2D float32 modifier grid.
    
    Weights semantics:
    - 1.0: Open water (unmodified bathymetric transit cost)
    - 0.30 .. 0.50: Designated channels & TSS (cost discount, attracting passages)
    - >= 500.0: Restricted maritime areas / military zones (strictly avoided / unnavigable)
    """
    weights = np.ones((grid_rows, grid_cols), dtype=np.float32)
    
    fc = load_fairways_geojson(geojson_path)
    features = fc.get("features", [])
    if not features or not _HAS_RASTERIO_FEATURES:
        return weights

    lat_step = (lat_max - lat_min) / grid_rows
    lon_step = (lon_max - lon_min) / grid_cols
    transform = Affine.translation(lon_min, lat_max) * Affine.scale(lon_step, -lat_step)

    # 1. Rasterize Fairway / TSS attraction polygons first
    fairway_shapes = []
    for feat in features:
        props = feat.get("properties", {})
        cat = props.get("category", "")
        factor = float(props.get("cost_factor", 1.0))
        geom = feat.get("geometry")
        if geom and cat in ("fairway", "tss") and factor < 1.0:
            fairway_shapes.append((geom, factor))

    if fairway_shapes:
        fw_weights = rasterize(
            fairway_shapes,
            out_shape=(grid_rows, grid_cols),
            fill=1.0,
            transform=transform,
            dtype=np.float32,
        )
        weights = np.minimum(weights, fw_weights)

    # 2. Rasterize Restricted / Danger Areas on top (safety priority)
    restricted_shapes = []
    for feat in features:
        props = feat.get("properties", {})
        cat = props.get("category", "")
        geom = feat.get("geometry")
        if geom and cat == "restricted":
            factor = float(props.get("cost_factor", 1000.0))
            restricted_shapes.append((geom, factor))

    if restricted_shapes:
        res_weights = rasterize(
            restricted_shapes,
            out_shape=(grid_rows, grid_cols),
            fill=1.0,
            transform=transform,
            all_touched=True,
            dtype=np.float32,
        )
        restricted_mask = res_weights > 1.0

        # Navigational Safety Buffer: 1-cell CPA margin around restricted zones
        # Guarantees vessels never clip polygon perimeters or corner vertices
        dilated_mask = restricted_mask.copy()
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                shifted = np.roll(np.roll(restricted_mask, dr, axis=0), dc, axis=1)
                if dr < 0:
                    shifted[dr:, :] = False
                elif dr > 0:
                    shifted[:dr, :] = False
                if dc < 0:
                    shifted[:, dc:] = False
                elif dc > 0:
                    shifted[:, :dc] = False
                dilated_mask |= shifted

        weights[dilated_mask] = 1000.0

    return weights
