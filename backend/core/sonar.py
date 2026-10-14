"""
Sonar Bathymetric Processing & Spatial Interpolation Module
===========================================================
Supports ingestion of recreational and hydrographic echo sounder data:
- CSV / TXT / TSV (flexible column mappings)
- NMEA 0183 ($SDDPT, $SDDBT, $INDPT, $GPGGA, $GPRMC)
- GPX tracks with depth extensions (<ele>, <depth>)
- GeoJSON Point collections

Provides 2D Inverse Distance Weighting (IDW) interpolation, vector isobath
contouring via contourpy, and conservative IHO S-44 Shoal-bias grid fusion.
"""

from typing import List, Dict, Any, Tuple, Optional
import io
import csv
import re
import math
import xml.etree.ElementTree as ET
import numpy as np

try:
    import contourpy
    CONTOURPY_AVAILABLE = True
except ImportError:
    CONTOURPY_AVAILABLE = False


def _dm_to_deg(dm_str: str, hemi: str) -> Optional[float]:
    """Convert NMEA DDMM.MMMM or DDDMM.MMMM coordinate string to decimal degrees."""
    try:
        val = float(dm_str)
        if hemi.upper() in ("N", "S"):
            # Latitude: DDMM.MMMM (first 2 digits are degrees)
            deg = math.floor(val / 100.0)
            mins = val - (deg * 100.0)
        else:
            # Longitude: DDDMM.MMMM (first 3 digits are degrees)
            deg = math.floor(val / 100.0)
            mins = val - (deg * 100.0)
        dec = deg + (mins / 60.0)
        if hemi.upper() in ("S", "W"):
            dec = -dec
        return dec
    except (ValueError, TypeError):
        return None


def parse_nmea_soundings(text: str) -> List[Dict[str, float]]:
    """
    Parse NMEA 0183 sentences from echo sounder / GPS data logs.
    Correlates depth readings ($SDDPT, $SDDBT) with the most recent GPS fix ($GPGGA, $GPRMC).
    """
    soundings: List[Dict[str, float]] = []
    current_lat: Optional[float] = None
    current_lon: Optional[float] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("$"):
            continue

        # Strip checksum if present
        if "*" in line:
            line = line.split("*")[0]

        parts = line.split(",")
        cmd = parts[0].upper()

        # Parse position sentences ($GPGGA, $GNGGA, $GLGGA)
        if cmd.endswith("GGA") and len(parts) >= 6:
            lat = _dm_to_deg(parts[2], parts[3])
            lon = _dm_to_deg(parts[4], parts[5])
            if lat is not None and lon is not None:
                current_lat = lat
                current_lon = lon

        # Parse recommended minimum navigation ($GPRMC, $GNRMC)
        elif cmd.endswith("RMC") and len(parts) >= 7:
            status = parts[2].upper()
            if status == "A":  # Active / Valid fix
                lat = _dm_to_deg(parts[3], parts[4])
                lon = _dm_to_deg(parts[5], parts[6])
                if lat is not None and lon is not None:
                    current_lat = lat
                    current_lon = lon

        # Parse Depth sentence ($SDDPT, $INDPT, $YXDPT)
        elif cmd.endswith("DPT") and len(parts) >= 2:
            try:
                depth_m = float(parts[1])
                offset_m = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
                total_depth = depth_m + offset_m
                if current_lat is not None and current_lon is not None and 0.1 <= total_depth <= 11000.0:
                    soundings.append({
                        "lon": round(current_lon, 6),
                        "lat": round(current_lat, 6),
                        "depth_m": round(total_depth, 2)
                    })
            except (ValueError, TypeError):
                pass

        # Parse Depth Below Transducer sentence ($SDDBT)
        elif cmd.endswith("DBT") and len(parts) >= 5:
            try:
                # Part 3 is depth in meters
                if parts[3]:
                    depth_m = float(parts[3])
                elif parts[1]:  # feet -> meters
                    depth_m = float(parts[1]) * 0.3048
                else:
                    continue

                if current_lat is not None and current_lon is not None and 0.1 <= depth_m <= 11000.0:
                    soundings.append({
                        "lon": round(current_lon, 6),
                        "lat": round(current_lat, 6),
                        "depth_m": round(depth_m, 2)
                    })
            except (ValueError, TypeError):
                pass

    return soundings


def parse_csv_soundings(text: str) -> List[Dict[str, float]]:
    """
    Parse CSV / TSV text containing sounder coordinates and depths.
    Supports auto-detection of column headers and positions.
    """
    soundings: List[Dict[str, float]] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return soundings

    # Detect delimiter
    first_line = lines[0]
    delimiter = "\t" if "\t" in first_line else (";" if ";" in first_line else ",")

    reader = csv.reader(lines, delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return soundings

    # Check header
    header = [h.strip().lower() for h in rows[0]]
    lat_idx, lon_idx, depth_idx = -1, -1, -1

    for idx, col in enumerate(header):
        if col in ("lat", "latitude", "y", "широта"):
            lat_idx = idx
        elif col in ("lon", "lng", "longitude", "x", "долгота"):
            lon_idx = idx
        elif col in ("depth", "depth_m", "depth_meters", "z", "глубина", "sounding", "ele", "water_depth"):
            depth_idx = idx

    data_rows = rows[1:] if (lat_idx != -1 and lon_idx != -1) else rows

    # Fallback heuristic if no standard header matched
    if lat_idx == -1 or lon_idx == -1 or depth_idx == -1:
        if rows and len(rows[0]) >= 3:
            # Try 0=lat, 1=lon, 2=depth or 0=lon, 1=lat, 2=depth
            try:
                v0, v1, v2 = float(rows[0][0]), float(rows[0][1]), float(rows[0][2])
                if abs(v0) <= 90.0 and abs(v1) <= 180.0:
                    lat_idx, lon_idx, depth_idx = 0, 1, 2
                elif abs(v1) <= 90.0 and abs(v0) <= 180.0:
                    lon_idx, lat_idx, depth_idx = 0, 1, 2
                data_rows = rows
            except (ValueError, TypeError):
                if len(rows) > 1:
                    try:
                        v0, v1, v2 = float(rows[1][0]), float(rows[1][1]), float(rows[1][2])
                        if abs(v0) <= 90.0 and abs(v1) <= 180.0:
                            lat_idx, lon_idx, depth_idx = 0, 1, 2
                        elif abs(v1) <= 90.0 and abs(v0) <= 180.0:
                            lon_idx, lat_idx, depth_idx = 0, 1, 2
                        data_rows = rows[1:]
                    except (ValueError, TypeError):
                        pass

    if lat_idx == -1 or lon_idx == -1 or depth_idx == -1:
        return soundings

    for row in data_rows:
        if len(row) <= max(lat_idx, lon_idx, depth_idx):
            continue
        try:
            lat = float(row[lat_idx].replace(",", "."))
            lon = float(row[lon_idx].replace(",", "."))
            raw_depth = float(row[depth_idx].replace(",", "."))
            depth = abs(raw_depth)  # Bathymetric depth is positive below surface

            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and 0.1 <= depth <= 11000.0:
                soundings.append({
                    "lon": round(lon, 6),
                    "lat": round(lat, 6),
                    "depth_m": round(depth, 2)
                })
        except (ValueError, TypeError):
            continue

    return soundings


def parse_gpx_soundings(text: str) -> List[Dict[str, float]]:
    """Parse GPX track points containing depth measurements (<ele>, <depth>, Garmin extensions)."""
    soundings: List[Dict[str, float]] = []
    try:
        root = ET.fromstring(text)
    except Exception:
        return soundings

    # Strip XML namespaces for simple tag matching
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    for pt in root.iter("trkpt"):
        try:
            lat = float(pt.attrib.get("lat", 0.0))
            lon = float(pt.attrib.get("lon", 0.0))
        except (ValueError, TypeError):
            continue

        depth_m = None
        # Check direct <depth> tag
        depth_elem = pt.find("depth")
        if depth_elem is not None and depth_elem.text:
            try:
                depth_m = float(depth_elem.text)
            except ValueError:
                pass

        # Check <ele> tag (often negative elevation for marine depth)
        if depth_m is None:
            ele_elem = pt.find("ele")
            if ele_elem is not None and ele_elem.text:
                try:
                    ele_val = float(ele_elem.text)
                    depth_m = abs(ele_val)
                except ValueError:
                    pass

        # Check <extensions> tags
        if depth_m is None:
            ext = pt.find("extensions")
            if ext is not None:
                for sub in ext.iter():
                    if "depth" in sub.tag.lower() and sub.text:
                        try:
                            depth_m = float(sub.text)
                            break
                        except ValueError:
                            pass

        if depth_m is not None and 0.1 <= depth_m <= 11000.0 and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            soundings.append({
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "depth_m": round(depth_m, 2)
            })

    return soundings


def parse_sonar_payload(content_str: str, filename: str = "") -> List[Dict[str, float]]:
    """
    Unified entry point for parsing any echo sounder / bathymetry upload.
    Dispatches to NMEA, GPX, or CSV parser based on structure and extension.
    """
    fname = filename.lower()
    text = content_str.strip()

    if fname.endswith(".gpx") or "<gpx" in text[:300].lower():
        return parse_gpx_soundings(text)

    if fname.endswith((".nmea", ".log")) or text.startswith("$") or "\n$" in text[:500]:
        nmea_res = parse_nmea_soundings(text)
        if nmea_res:
            return nmea_res

    # Default to CSV / Delimited parser
    return parse_csv_soundings(text)


def interpolate_sonar_bathymetry(
    soundings: List[Dict[str, float]],
    grid_size: int = 70,
    power: float = 2.0,
    search_radius_m: float = 1200.0,
) -> Dict[str, Any]:
    """
    Interpolate unstructured sounding points into a 2D high-resolution bathymetric surface
    using vectorized Inverse Distance Weighting (IDW) and extract vector isobaths via contourpy.
    """
    if not soundings:
        return {
            "success": False,
            "error": "Нет валидных точек промеров глубин.",
            "statistics": {},
            "geojson": {"type": "FeatureCollection", "features": []}
        }

    lons = np.array([p["lon"] for p in soundings], dtype=np.float64)
    lats = np.array([p["lat"] for p in soundings], dtype=np.float64)
    depths = np.array([p["depth_m"] for p in soundings], dtype=np.float64)

    min_lon, max_lon = float(lons.min()), float(lons.max())
    min_lat, max_lat = float(lats.min()), float(lats.max())
    min_d, max_d = float(depths.min()), float(depths.max())
    mean_d = float(depths.mean())

    # Add 5% padding around survey extent
    pad_lon = max(0.001, (max_lon - min_lon) * 0.05)
    pad_lat = max(0.001, (max_lat - min_lat) * 0.05)
    bound_lon_min = min_lon - pad_lon
    bound_lon_max = max_lon + pad_lon
    bound_lat_min = min_lat - pad_lat
    bound_lat_max = max_lat + pad_lat

    # Compute planar scaling factors in meters
    mean_lat_rad = math.radians((bound_lat_min + bound_lat_max) / 2.0)
    m_per_deg_lat = 111139.0
    m_per_deg_lon = 111139.0 * math.cos(mean_lat_rad)

    # Build regular grid
    x_coords = np.linspace(bound_lon_min, bound_lon_max, grid_size)
    y_coords = np.linspace(bound_lat_min, bound_lat_max, grid_size)
    grid_x, grid_y = np.meshgrid(x_coords, y_coords)

    # Convert sounding points to metric coordinates
    pts_x_m = lons * m_per_deg_lon
    pts_y_m = lats * m_per_deg_lat

    grid_x_m = grid_x * m_per_deg_lon
    grid_y_m = grid_y * m_per_deg_lat

    # Vectorized IDW over 2D grid
    # For performance on larger surveys, process in blocks
    interpolated_grid = np.zeros_like(grid_x, dtype=np.float32)
    valid_mask = np.zeros_like(grid_x, dtype=bool)

    flat_gx = grid_x_m.ravel()
    flat_gy = grid_y_m.ravel()
    num_cells = flat_gx.size
    flat_z = np.zeros(num_cells, dtype=np.float32)
    flat_valid = np.zeros(num_cells, dtype=bool)

    chunk_size = 500
    for i in range(0, num_cells, chunk_size):
        chunk_end = min(i + chunk_size, num_cells)
        cgx = flat_gx[i:chunk_end, np.newaxis]  # (K, 1)
        cgy = flat_gy[i:chunk_end, np.newaxis]

        # Distances to all sounding points: (K, N)
        dx = cgx - pts_x_m[np.newaxis, :]
        dy = cgy - pts_y_m[np.newaxis, :]
        dist_sq = dx * dx + dy * dy
        dist = np.sqrt(dist_sq)

        # Apply search radius mask
        in_radius = dist <= search_radius_m
        weights = np.where(in_radius, 1.0 / np.maximum(dist ** power, 1e-4), 0.0)
        sum_weights = weights.sum(axis=1)

        has_neighbors = sum_weights > 0.0
        sum_weighted_z = (weights * depths[np.newaxis, :]).sum(axis=1)

        flat_z[i:chunk_end] = np.where(has_neighbors, sum_weighted_z / np.maximum(sum_weights, 1e-12), 0.0)
        flat_valid[i:chunk_end] = has_neighbors

    interpolated_grid = flat_z.reshape((grid_size, grid_size))
    valid_mask = flat_valid.reshape((grid_size, grid_size))

    # Generate isobaths using contourpy
    features: List[Dict[str, Any]] = []

    # 1. Survey boundary polygon
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [bound_lon_min, bound_lat_min],
                [bound_lon_max, bound_lat_min],
                [bound_lon_max, bound_lat_max],
                [bound_lon_min, bound_lat_max],
                [bound_lon_min, bound_lat_min]
            ]]
        },
        "properties": {
            "role": "survey_boundary",
            "points_count": len(soundings),
            "min_depth_m": round(min_d, 2),
            "max_depth_m": round(max_d, 2),
            "mean_depth_m": round(mean_d, 2),
        }
    })

    # 2. Extract contour lines
    if CONTOURPY_AVAILABLE and (max_d - min_d) >= 0.5:
        # Determine suitable contour interval
        depth_range = max_d - min_d
        if depth_range <= 5.0:
            step = 0.5
        elif depth_range <= 15.0:
            step = 1.0
        elif depth_range <= 40.0:
            step = 2.0
        elif depth_range <= 100.0:
            step = 5.0
        else:
            step = 10.0

        levels = np.arange(math.ceil(min_d), math.floor(max_d) + step, step)
        levels = [round(float(l), 1) for l in levels if min_d <= l <= max_d]

        try:
            cont_gen = contourpy.contour_generator(
                x_coords, y_coords, interpolated_grid, name="serial"
            )
            for lvl in levels:
                lines = cont_gen.lines(lvl)
                for line in lines:
                    if len(line) >= 2:
                        features.append({
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[round(float(p[0]), 6), round(float(p[1]), 6)] for p in line]
                            },
                            "properties": {
                                "role": "custom_isobath",
                                "depth": lvl,
                                "label": f"{lvl:.1f} м"
                            }
                        })
        except Exception:
            pass

    # 3. Add decimated sounding points for visual display (max 300 points)
    sample_step = max(1, len(soundings) // 250)
    for i in range(0, len(soundings), sample_step):
        s = soundings[i]
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [s["lon"], s["lat"]]
            },
            "properties": {
                "role": "sounding_point",
                "depth": s["depth_m"],
                "label": f"{s['depth_m']:.1f}"
            }
        })

    # Compute survey area in square kilometers
    survey_w_km = (max_lon - min_lon) * m_per_deg_lon / 1000.0
    survey_h_km = (max_lat - min_lat) * m_per_deg_lat / 1000.0
    area_sq_km = round(survey_w_km * survey_h_km, 3)

    return {
        "success": True,
        "statistics": {
            "soundings_count": len(soundings),
            "min_depth_m": round(min_d, 2),
            "max_depth_m": round(max_d, 2),
            "mean_depth_m": round(mean_d, 2),
            "area_sq_km": area_sq_km,
            "bounds": [round(bound_lon_min, 6), round(bound_lat_min, 6), round(bound_lon_max, 6), round(bound_lat_max, 6)]
        },
        "geojson": {
            "type": "FeatureCollection",
            "features": features
        }
    }


def fuse_soundings_into_grid(grid: Any, soundings: List[Dict[str, float]], is_dredged: bool = False) -> int:
    """
    Fuses custom sounder measurements directly into the active BathymetricGrid.
    Enforces IHO S-44 Shoal-bias safety rule: shallower depths always take precedence
    over coarse satellite or background bathymetry.
    Returns the number of grid cells successfully updated.
    """
    if not soundings or grid is None:
        return 0

    updated_count = 0
    for s in soundings:
        lon, lat, depth = s["lon"], s["lat"], s["depth_m"]
        if not (grid.lat_min <= lat <= grid.lat_max and grid.lon_min <= lon <= grid.lon_max):
            continue

        r, c = grid.lonlat_to_cell(lon, lat)
        if 0 <= r < grid.rows and 0 <= c < grid.cols:
            if grid.land_mask is not None and grid.land_mask[r, c] and not is_dredged:
                continue

            curr_d = float(grid.depths[r, c])
            if grid.nodata_mask is not None and grid.nodata_mask[r, c]:
                grid.depths[r, c] = max(0.0, depth)
                grid.nodata_mask[r, c] = False
                updated_count += 1
            elif curr_d > 0.0 and depth > 0.0:
                # IHO S-44 Shoal-bias rule: retain shallower depth for safety
                if depth < curr_d:
                    grid.depths[r, c] = depth
                    updated_count += 1
            elif is_dredged and depth > 0.0:
                grid.depths[r, c] = depth
                if grid.land_mask is not None:
                    grid.land_mask[r, c] = False
                updated_count += 1

    return updated_count
