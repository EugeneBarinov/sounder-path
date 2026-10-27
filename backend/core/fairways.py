"""
ECDIS Navigational Fairways & Constraints Module
================================================
Manages S-57 / ENC vector features, traffic separation schemes (TSS),
dredged approach channels, and restricted navigational zones (RESARE).
Rasterizes vector fairway corridors directly onto bathymetric routing grids.
"""

from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import json
import logging
import numpy as np
from affine import Affine

try:
    from rasterio.features import rasterize
    _HAS_RASTERIO_FEATURES = True
except ImportError:
    _HAS_RASTERIO_FEATURES = False

# Optional pyproj for CRS transformation of non-WGS84 GeoJSON sources.
try:
    from pyproj import Transformer, CRS
    _HAS_PYPROJ = True
except ImportError:
    _HAS_PYPROJ = False

logger = logging.getLogger("seapath.fairways")

WGS84_EPSG = 4326

DEFAULT_FAIRWAYS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "fairways.geojson"

# ZOC (Zone of Confidence) class index mapping (IHO S-57 CATZOC).
# 0 = unknown / lowest confidence, 1 = A1 (highest), ..., 6 = U (unassessed).
_ZOC_CLASS_TO_INDEX = {
    "A1": 1,
    "A2": 2,
    "B": 3,
    "C": 4,
    "D": 5,
    "U": 6,
}


def _zoc_class_index(props: Dict[str, Any]) -> int:
    """Resolve ZOC class index from feature properties (default 0 = unknown)."""
    raw = props.get("zoc_class", props.get("CATZOC", ""))
    if isinstance(raw, str):
        return _ZOC_CLASS_TO_INDEX.get(raw.strip().upper(), 0)
    if isinstance(raw, (int, float)):
        return max(0, min(6, int(raw)))
    return 0


def rasterize_zoc_mask(
    grid_rows: int,
    grid_cols: int,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    geojson_path: Path | str = DEFAULT_FAIRWAYS_PATH,
) -> np.ndarray:
    """
    Rasterize ZOC (Zone of Confidence) classes from fairways GeoJSON into a
    2D uint8 mask. Cells not covered by any ZOC feature default to 0 (unknown).

    Weights semantics (grid.py.get_zoc_penalty):
      1=A1, 2=A2: 0.0 penalty (highest survey confidence)
      3=B:        0.1
      4=C:        0.3
      5=D:        0.6
      6=U:        0.8 (unassessed)
      0=unknown:  0.8 (lowest confidence)
    """
    zoc_mask = np.zeros((grid_rows, grid_cols), dtype=np.uint8)

    fc = load_fairways_geojson(geojson_path)
    features = fc.get("features", [])
    if not features or not _HAS_RASTERIO_FEATURES:
        return zoc_mask

    lat_step = max(1e-9, (lat_max - lat_min) / max(1, grid_rows))
    lon_step = max(1e-9, (lon_max - lon_min) / max(1, grid_cols))
    transform = Affine.translation(lon_min, lat_max) @ Affine.scale(lon_step, -lat_step)

    # Rasterize each ZOC class separately so the highest-confidence class wins
    # where polygons overlap (minimum class index = best confidence).
    for class_index in range(1, 7):
        class_name = {1: "A1", 2: "A2", 3: "B", 4: "C", 5: "D", 6: "U"}[class_index]
        shapes = []
        for feat in features:
            props = feat.get("properties", {})
            if _zoc_class_index(props) != class_index:
                continue
            geom = feat.get("geometry")
            if geom:
                shapes.append((geom, float(class_index)))

        if not shapes:
            continue

        class_weights = rasterize(
            shapes,
            out_shape=(grid_rows, grid_cols),
            fill=0.0,
            transform=transform,
            dtype=np.float32,
        )
        # Where this class is present, overwrite with its index (process in
        # ascending order so lower-confidence classes overwrite higher ones,
        # ending with worst confidence on top where overlaps exist).
        zoc_mask = np.where(class_weights > 0.5, class_index, zoc_mask)

    return zoc_mask


def _detect_fc_crs(fc: Dict[str, Any]) -> Optional[int]:
    """
    Detect EPSG code from GeoJSON FeatureCollection crs member.

    Supports:
      - {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} -> 4326
      - {"type": "name", "properties": {"name": "EPSG:XXXX"}} -> XXXX
      - {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::XXXX"}} -> XXXX
    Returns None if CRS is missing or unrecognised.
    """
    crs = fc.get("crs")
    if not crs or not isinstance(crs, dict):
        return None
    props = crs.get("properties", {}) if isinstance(crs.get("properties"), dict) else {}
    name = props.get("name", "")
    if not isinstance(name, str):
        return None

    name_upper = name.upper()
    if "CRS84" in name_upper or "OGC:1.3:CRS84" in name_upper or "WGS84" in name_upper:
        return WGS84_EPSG

    # Extract trailing digits after EPSG:
    import re
    m = re.search(r"EPSG:?::?(\d+)", name_upper)
    if m:
        return int(m.group(1))

    return None


def _transform_geometry_to_wgs84(
    geom: Dict[str, Any],
    source_epsg: int,
) -> Dict[str, Any]:
    """
    Transform a GeoJSON geometry from source_epsg to EPSG:4326 using pyproj.

    If pyproj is not available, returns the geometry unchanged and logs a
    warning — coordinates are assumed to already be in WGS84 (best-effort).
    Supports Polygon and MultiPolygon (fairway / restricted areas are areas).
    """
    if not _HAS_PYPROJ:
        logger.warning(
            "pyproj not installed: cannot transform fairways from EPSG:%d to EPSG:%d. "
            "Assuming coordinates are already WGS84 — results may be shifted.",
            source_epsg, WGS84_EPSG,
        )
        return geom

    try:
        transformer = Transformer.from_crs(
            f"EPSG:{source_epsg}", f"EPSG:{WGS84_EPSG}", always_xy=True
        )

        def _transform_coords(coords):
            if isinstance(coords[0], (int, float)):
                # Single coordinate pair [lon, lat]
                return list(transformer.transform(coords[0], coords[1]))
            # Nested rings
            return [_transform_coords(c) for c in coords]

        geom_type = geom.get("type")
        if geom_type == "Polygon":
            return {
                "type": "Polygon",
                "coordinates": [_transform_coords(ring) for ring in geom.get("coordinates", [])],
            }
        if geom_type == "MultiPolygon":
            return {
                "type": "MultiPolygon",
                "coordinates": [
                    [_transform_coords(ring) for ring in poly.get("coordinates", [])]
                    for poly in geom.get("coordinates", [])
                ],
            }
    except Exception as exc:  # noqa: BLE001 — never let CRS transform break route calc
        logger.warning(
            "Failed to transform fairways from EPSG:%d to EPSG:%d: %s. "
            "Assuming coordinates are already WGS84.", source_epsg, WGS84_EPSG, exc
        )

    return geom


def load_fairways_geojson(path: Path | str = DEFAULT_FAIRWAYS_PATH) -> Dict[str, Any]:
    """Load fairways and navigational restrictions GeoJSON feature collection.

    Performs CRS validation: if the FeatureCollection declares a CRS other than
    EPSG:4326, geometries are transformed to WGS84 (when pyproj is available)
    so they align with the bathymetric grid. A warning is logged otherwise.
    """
    path = Path(path)
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}

    with open(path, "r", encoding="utf-8") as f:
        fc = json.load(f)

    source_epsg = _detect_fc_crs(fc)
    if source_epsg is not None and source_epsg != WGS84_EPSG:
        logger.warning(
            "Fairways GeoJSON declares CRS EPSG:%d; transforming to EPSG:%d before rasterization.",
            source_epsg, WGS84_EPSG,
        )
        transformed_features: List[Dict[str, Any]] = []
        for feat in fc.get("features", []):
            geom = feat.get("geometry")
            if geom is not None:
                feat = dict(feat)
                feat["geometry"] = _transform_geometry_to_wgs84(geom, source_epsg)
            transformed_features.append(feat)
        fc = dict(fc)
        fc["features"] = transformed_features
        fc["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:OGC:1.3:CRS84"}}

    return fc


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

    lat_step = max(1e-9, (lat_max - lat_min) / max(1, grid_rows))
    lon_step = max(1e-9, (lon_max - lon_min) / max(1, grid_cols))
    transform = Affine.translation(lon_min, lat_max) @ Affine.scale(lon_step, -lat_step)

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
