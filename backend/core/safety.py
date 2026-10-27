"""
ECDIS Route Safety & Corridor Verification Engine
=================================================
Implements automated route verification and safety contour checking
in compliance with IEC 61174:2015 Section 6.8 and IMO Resolution A.893(21).
Generates swept safety corridors (XTD) and real-time navigational alarms.
"""

from typing import List, Dict, Any, Tuple, Optional
import math
import numpy as np

from backend.core.kinematics import calculate_distance_m


DEG_TO_RAD = math.pi / 180.0
RAD_TO_DEG = 180.0 / math.pi
METERS_PER_DEG_LAT = 111139.0


def _point_in_polygon(lon: float, lat: float, coordinates: List[List[List[float]]]) -> bool:
    """
    Ray-casting точка-во-многоугольнике для GeoJSON Polygon координат [lon, lat].

    Проверяет, попадает ли точка (lon, lat) во внешнее кольцо полигона.
    Работает с любым количеством вершин, включая сложные прибрежные контуры RESARE.
    """
    if not coordinates or not coordinates[0]:
        return False

    ring = coordinates[0]  # Внешнее кольцо (exterior ring)
    n = len(ring)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]

        if ((yi > lat) != (yj > lat)):
            x_intersect = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_intersect:
                inside = not inside
        j = i

    return inside


def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate forward true compass bearing between two coordinates."""
    phi1 = lat1 * DEG_TO_RAD
    phi2 = lat2 * DEG_TO_RAD
    dlam = (lon2 - lon1) * DEG_TO_RAD

    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    theta = math.atan2(y, x) * RAD_TO_DEG
    return (theta + 360.0) % 360.0


def compute_route_segment_xtds(
    route_coords: List[List[float]],
    waypoints: List[Dict[str, Any]],
    default_port_xtd_m: float = 185.2,
    default_stbd_xtd_m: float = 185.2,
) -> List[Tuple[float, float]]:
    """
    Computes per-segment Cross-Track Distance (port_xtd_m, stbd_xtd_m) along route_coords
    by mapping each route segment to its corresponding passage plan leg.
    Ensures that every leg's unique XTD specified in waypoints (e.g. narrow in harbor,
    wide in open sea) is verified against bathymetry and obstacles.
    """
    n_segs = max(1, len(route_coords) - 1)
    if not waypoints or len(waypoints) < 2 or len(route_coords) < 2:
        return [(default_port_xtd_m, default_stbd_xtd_m)] * n_segs

    # Extract per-leg XTDs from waypoints (wp[i] defines leg from wp[i] to wp[i+1])
    n_legs = len(waypoints) - 1
    leg_xtds: List[Tuple[float, float]] = []
    for i in range(n_legs):
        wp = waypoints[i]
        p_raw = wp.get("port_xtd_nm")
        s_raw = wp.get("stbd_xtd_nm")
        p_m = float(p_raw) * 1852.0 if (p_raw is not None and float(p_raw) > 0.0) else default_port_xtd_m
        s_m = float(s_raw) * 1852.0 if (s_raw is not None and float(s_raw) > 0.0) else default_stbd_xtd_m
        leg_xtds.append((p_m, s_m))

    # If route_coords directly match waypoints
    if len(route_coords) == len(waypoints):
        return leg_xtds

    # Project each waypoint along route_coords to find bounding indices
    wp_indices: List[int] = [0]
    for m in range(1, len(waypoints) - 1):
        wp = waypoints[m]
        w_lat = float(wp.get("lat") if wp.get("lat") is not None else wp.get("latitude", 0.0))
        w_lon = float(wp.get("lon") if wp.get("lon") is not None else (wp.get("lng") if wp.get("lng") is not None else wp.get("longitude", 0.0)))
        prev_idx = wp_indices[-1]
        best_k = prev_idx
        best_dist = math.inf
        for k in range(prev_idx, len(route_coords)):
            d = calculate_distance_m(w_lat, w_lon, route_coords[k][1], route_coords[k][0])
            if d < best_dist:
                best_dist = d
                best_k = k
        wp_indices.append(best_k)
    wp_indices.append(len(route_coords) - 1)

    # Assign XTDs to each segment of route_coords
    result: List[Tuple[float, float]] = []
    for j in range(n_segs):
        leg_idx = 0
        for m in range(n_legs):
            if wp_indices[m] <= j < wp_indices[m + 1]:
                leg_idx = m
                break
            elif j >= wp_indices[n_legs]:
                leg_idx = n_legs - 1

        # Check if segment is at turn junction transition between leg_idx - 1 and leg_idx
        if leg_idx > 0 and abs(j - wp_indices[leg_idx]) <= 2:
            prev_p, prev_s = leg_xtds[leg_idx - 1]
            cur_p, cur_s = leg_xtds[leg_idx]
            result.append((max(prev_p, cur_p), max(prev_s, cur_s)))
        else:
            result.append(leg_xtds[leg_idx])

    return result


def generate_safety_corridor_polygon(
    route_coords: List[List[float]],
    port_xtd_m: float = 185.2,
    stbd_xtd_m: float = 185.2,
    seg_xtds: Optional[List[Tuple[float, float]]] = None,
) -> Dict[str, Any]:
    """
    Constructs a 2D polygonal safety corridor (swept channel) around a route LineString.
    Computes normal offsets on port and starboard sides with circular endcaps at bow and stern.
    Supports per-segment XTD widths when seg_xtds is provided.
    Returns a GeoJSON Feature representing the corridor polygon.
    """
    if len(route_coords) < 2:
        return {"type": "Feature", "properties": {"role": "corridor"}, "geometry": {"type": "Polygon", "coordinates": []}}

    left_points: List[List[float]] = []
    right_points: List[List[float]] = []

    n = len(route_coords)
    bearings: List[float] = []

    for i in range(n):
        lon_i, lat_i = route_coords[i][0], route_coords[i][1]
        m_per_deg_lon = METERS_PER_DEG_LAT * math.cos(lat_i * DEG_TO_RAD)
        if abs(m_per_deg_lon) < 1.0:
            m_per_deg_lon = 1.0

        # Calculate forward/bisector direction vector at vertex i
        if i == 0:
            brg = calculate_bearing_deg(lat_i, lon_i, route_coords[i + 1][1], route_coords[i + 1][0])
        elif i == n - 1:
            brg = calculate_bearing_deg(route_coords[i - 1][1], route_coords[i - 1][0], lat_i, lon_i)
        else:
            b1 = calculate_bearing_deg(route_coords[i - 1][1], route_coords[i - 1][0], lat_i, lon_i)
            b2 = calculate_bearing_deg(lat_i, lon_i, route_coords[i + 1][1], route_coords[i + 1][0])
            diff = (b2 - b1 + 180.0) % 360.0 - 180.0
            brg = (b1 + diff / 2.0 + 360.0) % 360.0
        bearings.append(brg)

        # Perpendicular normal bearings: Port (-90 deg), Starboard (+90 deg)
        port_brg = (brg - 90.0) * DEG_TO_RAD
        stbd_brg = (brg + 90.0) * DEG_TO_RAD

        if seg_xtds and len(seg_xtds) > 0:
            if i == 0:
                cur_p, cur_s = seg_xtds[0]
            elif i == n - 1:
                cur_p, cur_s = seg_xtds[-1]
            else:
                p1, s1 = seg_xtds[min(i - 1, len(seg_xtds) - 1)]
                p2, s2 = seg_xtds[min(i, len(seg_xtds) - 1)]
                cur_p, cur_s = max(p1, p2), max(s1, s2)
        else:
            cur_p, cur_s = port_xtd_m, stbd_xtd_m

        # Left (Port) offset
        lat_left = lat_i + (cur_p * math.cos(port_brg)) / METERS_PER_DEG_LAT
        lon_left = lon_i + (cur_p * math.sin(port_brg)) / m_per_deg_lon
        left_points.append([round(lon_left, 6), round(lat_left, 6)])

        # Right (Starboard) offset
        lat_right = lat_i + (cur_s * math.cos(stbd_brg)) / METERS_PER_DEG_LAT
        lon_right = lon_i + (cur_s * math.sin(stbd_brg)) / m_per_deg_lon
        right_points.append([round(lon_right, 6), round(lat_right, 6)])

    # Rounded endcap at terminal waypoint (n - 1) sweeping clockwise from port to starboard
    bow_endcap: List[List[float]] = []
    last_lon, last_lat = route_coords[-1][0], route_coords[-1][1]
    last_brg = bearings[-1]
    m_per_deg_lon_last = METERS_PER_DEG_LAT * math.cos(last_lat * DEG_TO_RAD)
    hdg_rad_last = last_brg * DEG_TO_RAD
    ux_last, uy_last = math.sin(hdg_rad_last), math.cos(hdg_rad_last)
    nx_last, ny_last = uy_last, -ux_last  # Starboard normal

    bow_p = seg_xtds[-1][0] if (seg_xtds and len(seg_xtds) > 0) else port_xtd_m
    bow_s = seg_xtds[-1][1] if (seg_xtds and len(seg_xtds) > 0) else stbd_xtd_m

    for step in range(1, 8):
        frac = step / 8.0
        rel_ang_rad = (-0.5 + frac) * math.pi
        # Longitudinal forward displacement along track
        fwd_dist = 0.5 * (bow_p + bow_s) * math.cos(rel_ang_rad)
        # Lateral displacement: port if rel_ang_rad < 0, else stbd
        lat_dist = bow_p * math.sin(rel_ang_rad) if rel_ang_rad < 0 else bow_s * math.sin(rel_ang_rad)

        dx_m = fwd_dist * ux_last + lat_dist * nx_last
        dy_m = fwd_dist * uy_last + lat_dist * ny_last

        lat_c = last_lat + dy_m / METERS_PER_DEG_LAT
        lon_c = last_lon + dx_m / m_per_deg_lon_last
        bow_endcap.append([round(lon_c, 6), round(lat_c, 6)])

    # Rounded endcap at initial waypoint (0) sweeping clockwise from starboard to port around stern
    stern_endcap: List[List[float]] = []
    first_lon, first_lat = route_coords[0][0], route_coords[0][1]
    first_brg = bearings[0]
    m_per_deg_lon_first = METERS_PER_DEG_LAT * math.cos(first_lat * DEG_TO_RAD)
    hdg_rad_first = first_brg * DEG_TO_RAD
    ux_first, uy_first = math.sin(hdg_rad_first), math.cos(hdg_rad_first)
    nx_first, ny_first = uy_first, -ux_first

    stern_p = seg_xtds[0][0] if (seg_xtds and len(seg_xtds) > 0) else port_xtd_m
    stern_s = seg_xtds[0][1] if (seg_xtds and len(seg_xtds) > 0) else stbd_xtd_m

    for step in range(1, 8):
        frac = step / 8.0
        rel_ang_rad = (0.5 - frac) * math.pi
        aft_dist = -0.5 * (stern_p + stern_s) * math.cos(rel_ang_rad)
        lat_dist = stern_s * math.sin(rel_ang_rad) if rel_ang_rad > 0 else stern_p * math.sin(rel_ang_rad)

        dx_m = aft_dist * ux_first + lat_dist * nx_first
        dy_m = aft_dist * uy_first + lat_dist * ny_first

        lat_c = first_lat + dy_m / METERS_PER_DEG_LAT
        lon_c = first_lon + dx_m / m_per_deg_lon_first
        stern_endcap.append([round(lon_c, 6), round(lat_c, 6)])

    # Assemble polygon: left forward -> bow endcap -> right backward -> stern endcap -> close
    corridor_ring = left_points + bow_endcap + list(reversed(right_points)) + stern_endcap + [left_points[0]]

    return {
        "type": "Feature",
        "properties": {
            "role": "corridor",
            "port_xtd_m": port_xtd_m,
            "stbd_xtd_m": stbd_xtd_m,
            "total_width_m": port_xtd_m + stbd_xtd_m,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [corridor_ring],
        },
    }


def scan_corridor_raster_swath(
    route_coords: List[List[float]],
    port_xtd_m: float,
    stbd_xtd_m: float,
    grid: Any,
    safety_depth: float,
    seg_xtds: Optional[List[Tuple[float, float]]] = None,
) -> List[Dict[str, Any]]:
    """
    Exhaustively scans the entire 2D area (raster swath) of the Cross-Track Safety Corridor (XTD).
    Checks 100% of grid cells within [port_xtd, stbd_xtd] bounds along each leg without premature exit.
    Supports per-segment/per-leg XTD dimensions when seg_xtds is provided.
    Classifies hazards by type: SHALLOW_WATER, LAND, RESTRICTED_ZONE, NODATA_UNVERIFIED.
    """
    if len(route_coords) < 2 or not hasattr(grid, "lonlat_to_cell") or not hasattr(grid, "get_cell_coords"):
        return []

    # Safeguard against NaN / Inf / non-positive safety depth threshold: Fail-Closed
    if not math.isfinite(safety_depth) or safety_depth <= 0.0:
        raise ValueError(
            f"Invalid safety_depth threshold ({safety_depth}). Safety depth must be a positive finite float."
        )

    hazards: List[Dict[str, Any]] = []
    visited_cells = set()
    n = len(route_coords)

    # Calculate cell footprint radius for conservative coverage
    lat_step = (grid.lat_max - grid.lat_min) / max(1, grid.rows)
    lon_step = (grid.lon_max - grid.lon_min) / max(1, grid.cols)

    for seg in range(n - 1):
        p0 = route_coords[seg]
        p1 = route_coords[seg + 1]

        lon0, lat0 = p0[0], p0[1]
        lon1, lat1 = p1[0], p1[1]

        lat_mid = (lat0 + lat1) / 2.0
        m_per_deg_lat = METERS_PER_DEG_LAT
        m_per_deg_lon = METERS_PER_DEG_LAT * math.cos(lat_mid * DEG_TO_RAD)
        if abs(m_per_deg_lon) < 1.0:
            m_per_deg_lon = 1.0

        cell_radius_m = 0.5 * math.hypot(lat_step * m_per_deg_lat, lon_step * m_per_deg_lon)

        dx = (lon1 - lon0) * m_per_deg_lon
        dy = (lat1 - lat0) * m_per_deg_lat
        seg_len = math.hypot(dx, dy)
        if seg_len < 1.0:
            continue

        ux = dx / seg_len
        uy = dy / seg_len
        nx = uy   # Unit normal pointing starboard
        ny = -ux

        if seg_xtds and seg < len(seg_xtds):
            cur_port_xtd_m, cur_stbd_xtd_m = seg_xtds[seg]
        else:
            cur_port_xtd_m, cur_stbd_xtd_m = port_xtd_m, stbd_xtd_m

        r_endcap = 0.5 * (cur_port_xtd_m + cur_stbd_xtd_m)
        max_xtd = max(cur_port_xtd_m, cur_stbd_xtd_m)
        max_reach = max(max_xtd, r_endcap) + cell_radius_m + 50.0
        d_lat_deg = max_reach / m_per_deg_lat
        d_lon_deg = max_reach / m_per_deg_lon

        min_lat = min(lat0, lat1) - d_lat_deg
        max_lat = max(lat0, lat1) + d_lat_deg
        min_lon = min(lon0, lon1) - d_lon_deg
        max_lon = max(lon0, lon1) + d_lon_deg

        # Unverified data coverage detection: corridor leaves bathymetry dataset
        if min_lat < grid.lat_min or max_lat > grid.lat_max or min_lon < grid.lon_min or max_lon > grid.lon_max:
            if not any(h.get("hazard_type") == "NODATA_UNVERIFIED" for h in hazards):
                hazards.append({
                    "is_unverified_coverage": True,
                    "hazard_type": "NODATA_UNVERIFIED",
                    "lon": round(lon0, 6),
                    "lat": round(lat0, 6),
                    "depth": 0.0,
                    "xtd_offset_m": round(max_xtd, 1),
                    "port_xtd_m": round(cur_port_xtd_m, 1),
                    "stbd_xtd_m": round(cur_stbd_xtd_m, 1),
                    "is_restricted": False,
                })

        r_top, c_left = grid.lonlat_to_cell(min_lon, max_lat)
        r_bot, c_right = grid.lonlat_to_cell(max_lon, min_lat)

        r_start = max(0, min(r_top, r_bot))
        r_end = min(grid.rows - 1, max(r_top, r_bot))
        c_start = max(0, min(c_left, c_right))
        c_end = min(grid.cols - 1, max(c_left, c_right))

        for r in range(r_start, r_end + 1):
            for c in range(c_start, c_end + 1):
                if (r, c) in visited_cells:
                    continue

                lat_c, lon_c = grid.get_cell_coords(r, c)
                wx = (lon_c - lon0) * m_per_deg_lon
                wy = (lat_c - lat0) * m_per_deg_lat

                # Projection along leg and cross-track offset
                s = wx * ux + wy * uy
                d_xtd = wx * nx + wy * ny  # > 0 starboard, < 0 port

                lat_xtd = cur_stbd_xtd_m if d_xtd >= 0.0 else cur_port_xtd_m

                # Physical intersection of cell footprint with corridor swath & endcaps
                is_inside = False
                if 0.0 <= s <= seg_len:
                    # Directly along leg
                    if -cur_port_xtd_m - cell_radius_m <= d_xtd <= cur_stbd_xtd_m + cell_radius_m:
                        is_inside = True
                elif s < 0.0:
                    if seg == 0:
                        # Stern endcap: semi-ellipse matching generate_safety_corridor_polygon
                        norm_s = (-s) / max(1.0, r_endcap + cell_radius_m)
                        norm_d = abs(d_xtd) / max(1.0, lat_xtd + cell_radius_m)
                        if norm_s * norm_s + norm_d * norm_d <= 1.0:
                            is_inside = True
                    else:
                        # Intermediate vertex junction tolerance
                        if s >= -cell_radius_m and (-cur_port_xtd_m - cell_radius_m <= d_xtd <= cur_stbd_xtd_m + cell_radius_m):
                            is_inside = True
                elif s > seg_len:
                    if seg == n - 2:
                        # Bow endcap: semi-ellipse matching generate_safety_corridor_polygon
                        norm_s = (s - seg_len) / max(1.0, r_endcap + cell_radius_m)
                        norm_d = abs(d_xtd) / max(1.0, lat_xtd + cell_radius_m)
                        if norm_s * norm_s + norm_d * norm_d <= 1.0:
                            is_inside = True
                    else:
                        # Intermediate vertex junction tolerance
                        if s <= seg_len + cell_radius_m and (-cur_port_xtd_m - cell_radius_m <= d_xtd <= cur_stbd_xtd_m + cell_radius_m):
                            is_inside = True

                if is_inside:
                    visited_cells.add((r, c))
                    depth_val = float(grid.depths[r, c])
                    is_restricted = grid.is_restricted(r, c)
                    is_nodata = bool(grid.nodata_mask[r, c]) if getattr(grid, "nodata_mask", None) is not None else False
                    is_land = (depth_val <= 0.0 and not is_nodata) or (getattr(grid, "land_mask", None) is not None and bool(grid.land_mask[r, c]))

                    # --- Depth / restriction / land / nodata hazards ----------
                    if depth_val <= safety_depth or is_restricted or is_nodata or is_land:
                        if is_restricted:
                            h_type = "RESTRICTED_ZONE"
                            is_unverified = False
                        elif is_nodata:
                            h_type = "NODATA_UNVERIFIED"
                            is_unverified = True
                        elif is_land:
                            h_type = "LAND"
                            is_unverified = False
                        else:
                            h_type = "SHALLOW_WATER"
                            is_unverified = False

                        hazards.append({
                            "r": r,
                            "c": c,
                            "lon": round(lon_c, 6),
                            "lat": round(lat_c, 6),
                            "depth": round(depth_val, 2),
                            "xtd_offset_m": round(d_xtd, 1),
                            "port_xtd_m": round(cur_port_xtd_m, 1),
                            "stbd_xtd_m": round(cur_stbd_xtd_m, 1),
                            "is_restricted": is_restricted,
                            "is_unverified_coverage": is_unverified,
                            "hazard_type": h_type,
                        })

                    # --- ZOC (Zone of Confidence) hazard ----------------------
                    # Low survey confidence means charted depth may be unreliable
                    # even when depth appears safe. Classes D(5), U(6), unknown(0)
                    # trigger a low-confidence hazard inside the XTD corridor.
                    zoc_class = grid.get_zoc_class(r, c) if hasattr(grid, "get_zoc_class") else 0
                    if zoc_class in (0, 5, 6):
                        zoc_class_name = {0: "UNKNOWN", 5: "D", 6: "U"}.get(zoc_class, f"ZOC{zoc_class}")
                        hazards.append({
                            "r": r,
                            "c": c,
                            "lon": round(lon_c, 6),
                            "lat": round(lat_c, 6),
                            "depth": round(depth_val, 2),
                            "xtd_offset_m": round(d_xtd, 1),
                            "port_xtd_m": round(cur_port_xtd_m, 1),
                            "stbd_xtd_m": round(cur_stbd_xtd_m, 1),
                            "is_restricted": False,
                            "is_unverified_coverage": True,
                            "hazard_type": "ZOC_LOW_CONFIDENCE",
                            "zoc_class": zoc_class_name,
                        })

    # Group hazards by category to guarantee worst hazard of EACH category is included
    by_type: Dict[str, List[Dict[str, Any]]] = {
        "NODATA_UNVERIFIED": [],
        "LAND": [],
        "RESTRICTED_ZONE": [],
        "SHALLOW_WATER": [],
        "ZOC_LOW_CONFIDENCE": [],
    }
    for h in hazards:
        ht = h.get("hazard_type", "SHALLOW_WATER")
        if ht in by_type:
            by_type[ht].append(h)
        else:
            by_type.setdefault(ht, []).append(h)

    # Sort each category individually to find its worst representative
    by_type["NODATA_UNVERIFIED"].sort(key=lambda h: abs(h.get("xtd_offset_m", 0.0)))
    by_type["LAND"].sort(key=lambda h: (h.get("depth", 0.0), abs(h.get("xtd_offset_m", 0.0))))
    by_type["RESTRICTED_ZONE"].sort(key=lambda h: abs(h.get("xtd_offset_m", 0.0)))
    by_type["SHALLOW_WATER"].sort(key=lambda h: (h.get("depth", 0.0), abs(h.get("xtd_offset_m", 0.0))))
    by_type["ZOC_LOW_CONFIDENCE"].sort(key=lambda h: (5 - h.get("zoc_class", 0) if h.get("zoc_class", 0) in (5, 6) else 5, abs(h.get("xtd_offset_m", 0.0))))

    selected: List[Dict[str, Any]] = []
    seen_ids = set()

    # Priority 1: Pick the worst representative of EACH detected hazard type
    for cat in ["NODATA_UNVERIFIED", "LAND", "RESTRICTED_ZONE", "SHALLOW_WATER", "ZOC_LOW_CONFIDENCE"]:
        cat_list = by_type.get(cat, [])
        if cat_list:
            top_h = cat_list[0]
            selected.append(top_h)
            seen_ids.add((top_h.get("r"), top_h.get("c"), top_h.get("hazard_type")))

    # Priority 2: Fill remaining slots up to limit (30) with remaining hazards sorted by severity
    remaining = [
        h for h in hazards
        if (h.get("r"), h.get("c"), h.get("hazard_type")) not in seen_ids
    ]
    remaining.sort(key=lambda h: (
        0 if h.get("is_unverified_coverage") else 1,
        h.get("depth", 0.0),
        0 if h.get("is_restricted") else 1
    ))

    limit = 30
    slots_left = max(0, limit - len(selected))
    selected.extend(remaining[:slots_left])
    return selected


def verify_ecdis_route_safety(
    waypoints: List[Dict[str, Any]],
    profile: List[Dict[str, Any]],
    fairways_coll: Dict[str, Any],
    draft: float,
    dynamic_squat: float,
    ukc: float,
    speed_knots: float,
    min_chart_depth_m: Optional[float] = None,
    dynamic_draft_m: Optional[float] = None,
    corridor_feature: Optional[Dict[str, Any]] = None,
    grid: Optional[Any] = None,
    requested_radius_m: Optional[float] = None,
    port_xtd_m: float = 185.2,
    stbd_xtd_m: float = 185.2,
    route_coords: Optional[List[List[float]]] = None,
) -> Dict[str, Any]:
    """
    Automated ECDIS Route Checking per IEC 61174:2015 and IMO Res. A.893(21).
    Evaluates:
    1. Safety Contour violations (Depth < Dynamic Draft + UKC).
    2. Critical UKC warnings (Clearance < 0.35m margin).
    3. Maximum Rate of Turn (ROT) feasibility based on vessel speed.
    4. Turning radius leg constraints against vessel minimum turning circle (CRITICAL block).
    5. Full 2D Cross-track safety corridor (XTD) area raster scanning for submerged shoals and uncharted waters.
    """
    alarms: List[Dict[str, Any]] = []
    safety_depth = min_chart_depth_m if min_chart_depth_m is not None else (draft + dynamic_squat + ukc)
    effective_dyn_draft = dynamic_draft_m if dynamic_draft_m is not None else (draft + dynamic_squat)

    if not math.isfinite(safety_depth) or safety_depth <= 0.0:
        return {
            "is_safe": False,
            "status": "CRITICAL_HAZARD",
            "alarms_count": 1,
            "alarms": [{
                "severity": "CRITICAL",
                "code": "ALM-00-INVALID-SAFETY-DEPTH",
                "title": "Invalid Safety Depth",
                "detail": f"Calculated safety depth threshold ({safety_depth}) is non-finite or non-positive.",
                "distance_nm": 0.0,
            }],
            "min_clearance_m": 0.0,
            "shallow_samples_count": 0,
            "grounding_samples_count": 0,
            "summary": "Safety check failed: safety depth threshold is non-finite or non-positive.",
            "corridor_hazards": [],
        }

    # 1. Depth Soundings Safety Check along track centerline
    min_observed_clearance = math.inf
    shallow_samples = 0
    grounding_samples = 0

    for pt in profile:
        clr = pt.get("clearance", 0.0)
        dist_nm = pt.get("distance_from_start_m", 0.0) / 1852.0
        if clr < min_observed_clearance:
            min_observed_clearance = clr

        if clr <= 0.0:
            grounding_samples += 1
            if grounding_samples == 1:
                alarms.append({
                    "severity": "CRITICAL",
                    "code": "ALM-01-GROUNDING-HAZARD",
                    "title": "Grounding Danger",
                    "detail": f"Seabed depth ({pt['depth']:.1f}m) penetrates Safety Contour ({safety_depth:.1f}m) at {dist_nm:.1f} NM.",
                    "distance_nm": round(dist_nm, 1),
                })
        elif clr < 0.35:
            shallow_samples += 1
            if shallow_samples == 1:
                alarms.append({
                    "severity": "WARNING",
                    "code": "ALM-02-CRITICAL-UKC",
                    "title": "Marginal Under-Keel Clearance",
                    "detail": f"Observed UKC ({clr:.2f}m) is less than 0.35m above required limit at {dist_nm:.1f} NM.",
                    "distance_nm": round(dist_nm, 1),
                })

    # 2. Maneuverability & Rate of Turn Verification
    rot_threshold = 30.0 if draft >= 7.0 else (60.0 if draft >= 3.0 else 120.0)
    vessel_class_label = "Commercial Deep-Draft" if draft >= 7.0 else ("Medium Craft" if draft >= 3.0 else "Light Craft")

    for idx, wp in enumerate(waypoints, start=1):
        rot = abs(wp.get("rot_deg_min", 0.0))
        turn = abs(wp.get("turn_angle_deg", 0.0))
        if rot > rot_threshold:
            alarms.append({
                "severity": "WARNING",
                "code": "ALM-03-EXCESSIVE-ROT",
                "title": f"High Rate of Turn (ROT) at WP{idx}",
                "detail": (
                    f"Turn deflection {turn:.1f}° requires ROT {rot:.1f}°/min, "
                    f"exceeding recommended limit for {vessel_class_label} ({rot_threshold:.0f}°/min). "
                    f"Consider increasing turning radius R to at least {int(wp.get('turn_radius_m', 150) * (rot / rot_threshold))}m."
                ),
                "waypoint_index": idx,
            })

        # CRITICAL MANEUVERING ENFORCEMENT: If vessel cannot physically execute commanded turning circle,
        # or if turn arc was rejected due to short leg or obstacle, route is unexecutable and fails audit!
        is_feasible = wp.get("maneuver_feasible", True)
        actual_r = wp.get("turn_radius_m", requested_radius_m if requested_radius_m else 150.0)
        radius_violation = (
            requested_radius_m is not None
            and requested_radius_m > 10.0
            and turn >= 4.0
            and actual_r < requested_radius_m * 0.98
        )
        if not is_feasible or radius_violation:
            alarms.append({
                "severity": "CRITICAL",
                "code": "ALM-05-MANEUVERING-LIMIT-VIOLATION",
                "title": f"Unexecutable Turn Maneuver at WP{idx}",
                "detail": (
                    f"Available leg length restricts turning radius to {actual_r:.0f}m "
                    f"(vessel minimum required: {requested_radius_m:.0f}m). "
                    f"Turn exceeds physical maneuvering limits; route cannot be safely executed by this vessel."
                ) if radius_violation else (
                    f"Maneuver at WP{idx} cannot be safely filleted within available navigable water. "
                    f"Turning arc intersects shoal or cannot be geometrically inscribed."
                ),
                "waypoint_index": idx,
            })

    # 3. Full 2D Cross-Track Safety Corridor (XTD) Area Swath Scanning along actual filleted route
    corridor_hazards: List[Dict[str, Any]] = []
    active_route_coords = (
        route_coords
        if (route_coords and len(route_coords) >= 2)
        else [[wp["lon"], wp["lat"]] for wp in waypoints]
    )
    if grid and hasattr(grid, "lonlat_to_cell") and len(active_route_coords) >= 2:
        seg_xtds = compute_route_segment_xtds(
            active_route_coords,
            waypoints=waypoints,
            default_port_xtd_m=port_xtd_m,
            default_stbd_xtd_m=stbd_xtd_m,
        )
        corridor_hazards = scan_corridor_raster_swath(
            route_coords=active_route_coords,
            port_xtd_m=port_xtd_m,
            stbd_xtd_m=stbd_xtd_m,
            grid=grid,
            safety_depth=safety_depth,
            seg_xtds=seg_xtds,
        )

        # Alarm 1: Unverified coverage / NoData (XTD extends into uncharted waters)
        unverified_h = next((h for h in corridor_hazards if h.get("is_unverified_coverage") or h.get("hazard_type") == "NODATA_UNVERIFIED"), None)
        if unverified_h:
            h_p_xtd = unverified_h.get("port_xtd_m", port_xtd_m)
            h_s_xtd = unverified_h.get("stbd_xtd_m", stbd_xtd_m)
            alarms.append({
                "severity": "CRITICAL",
                "code": "ALM-06-UNVERIFIED-DATA-COVERAGE",
                "title": "Uncharted Waters Inside XTD Safety Corridor",
                "detail": (
                    f"XTD safety corridor ({h_p_xtd:.0f}m port / {h_s_xtd:.0f}m stbd) extends outside verified bathymetry "
                    f"or into unsurveyed NoData area near ({unverified_h['lon']:.4f}, {unverified_h['lat']:.4f}). "
                    f"Navigating within corridor risks uncharted hazards."
                ),
                "hazard_location": [unverified_h["lon"], unverified_h["lat"]],
            })

        # Alarm 2: Submerged shoals / shallow water inside XTD
        shallow_hazards = [h for h in corridor_hazards if h.get("hazard_type") == "SHALLOW_WATER"]
        if shallow_hazards:
            worst_shallow = min(shallow_hazards, key=lambda h: h["depth"])
            w_p_xtd = worst_shallow.get("port_xtd_m", port_xtd_m)
            w_s_xtd = worst_shallow.get("stbd_xtd_m", stbd_xtd_m)
            alarms.append({
                "severity": "CRITICAL",
                "code": "ALM-04-XTD-CORRIDOR-HAZARD",
                "title": "Safety Corridor Shoal / Grounding Danger Inside XTD",
                "detail": (
                    f"Submerged shoal ({worst_shallow['depth']:.1f}m <= {safety_depth:.1f}m) detected inside full 2D "
                    f"XTD safety corridor ({w_p_xtd:.0f}m port / {w_s_xtd:.0f}m stbd) at ({worst_shallow['lon']:.4f}, {worst_shallow['lat']:.4f}), offset {abs(worst_shallow['xtd_offset_m']):.0f}m "
                    f"to {'Starboard' if worst_shallow['xtd_offset_m'] > 0 else 'Port'}. Vessel drifting within allowed XTD will ground."
                ),
                "hazard_depth_m": worst_shallow["depth"],
                "hazard_location": [worst_shallow["lon"], worst_shallow["lat"]],
            })

        # Alarm 3: Prohibited / Restricted navigational areas (RESARE) inside XTD
        restr_hazards = [h for h in corridor_hazards if h.get("hazard_type") == "RESTRICTED_ZONE"]
        if restr_hazards:
            worst_restr = restr_hazards[0]
            r_p_xtd = worst_restr.get("port_xtd_m", port_xtd_m)
            r_s_xtd = worst_restr.get("stbd_xtd_m", stbd_xtd_m)
            alarms.append({
                "severity": "CRITICAL",
                "code": "ALM-08-RESTRICTED-ZONE-VIOLATION",
                "title": "Prohibited / Restricted Area (RESARE) Inside XTD Corridor",
                "detail": (
                    f"XTD safety corridor ({r_p_xtd:.0f}m port / {r_s_xtd:.0f}m stbd) intersects prohibited or restricted navigational zone at "
                    f"({worst_restr['lon']:.4f}, {worst_restr['lat']:.4f}), offset {abs(worst_restr['xtd_offset_m']):.0f}m "
                    f"to {'Starboard' if worst_restr['xtd_offset_m'] > 0 else 'Port'}."
                ),
                "hazard_location": [worst_restr["lon"], worst_restr["lat"]],
            })

        # Alarm 4: Terrestrial land / shoreline penetration inside XTD
        land_hazards = [h for h in corridor_hazards if h.get("hazard_type") == "LAND"]
        if land_hazards:
            worst_land = land_hazards[0]
            l_p_xtd = worst_land.get("port_xtd_m", port_xtd_m)
            l_s_xtd = worst_land.get("stbd_xtd_m", stbd_xtd_m)
            alarms.append({
                "severity": "CRITICAL",
                "code": "ALM-09-LAND-TERRESTRIAL-HAZARD",
                "title": "Terrestrial Landmass / Shoreline Penetration Inside XTD",
                "detail": (
                    f"XTD safety corridor ({l_p_xtd:.0f}m port / {l_s_xtd:.0f}m stbd) penetrates dry land at ({worst_land['lon']:.4f}, {worst_land['lat']:.4f}), "
                    f"offset {abs(worst_land['xtd_offset_m']):.0f}m to {'Starboard' if worst_land['xtd_offset_m'] > 0 else 'Port'}."
                ),
                "hazard_location": [worst_land["lon"], worst_land["lat"]],
            })

        # Alarm 5: Low survey confidence (ZOC D / U / unknown) inside XTD
        zoc_hazards = [h for h in corridor_hazards if h.get("hazard_type") == "ZOC_LOW_CONFIDENCE"]
        if zoc_hazards:
            worst_zoc = zoc_hazards[0]
            z_p_xtd = worst_zoc.get("port_xtd_m", port_xtd_m)
            z_s_xtd = worst_zoc.get("stbd_xtd_m", stbd_xtd_m)
            alarms.append({
                "severity": "WARNING",
                "code": "ALM-11-ZOC-LOW-CONFIDENCE",
                "title": "Low Survey Confidence Zone Inside XTD Corridor",
                "detail": (
                    f"XTD safety corridor ({z_p_xtd:.0f}m port / {z_s_xtd:.0f}m stbd) passes through a Zone of Confidence "
                    f"{worst_zoc.get('zoc_class', '?')} area at ({worst_zoc['lon']:.4f}, {worst_zoc['lat']:.4f}). "
                    f"Charted depth may be unreliable; consider additional soundings before passage."
                ),
                "hazard_location": [worst_zoc["lon"], worst_zoc["lat"]],
            })

    # 5. Waypoint-In-Restricted-Zone (RESARE) check via fairways_coll
    # fairways_coll передаётся в функцию, но ранее не использовался.
    # Теперь проверяем, не попадает ли каждая путевая точка внутрь полигона
    # ограниченной зоны (RESARE) — полигоны боевой подготовки, заповедников,
    # районов затонувших судов и т.п. Это защита на уровне путевых точек,
    # дополняющая сканирование коридора XTD по сетке батиметрии.
    if fairways_coll:
        for idx, wp in enumerate(waypoints, start=1):
            wp_lon = float(wp.get("lon") if wp.get("lon") is not None else wp.get("lng", 0.0))
            wp_lat = float(wp.get("lat") if wp.get("lat") is not None else wp.get("latitude", 0.0))

            for feature in fairways_coll.get("features", []):
                props = feature.get("properties", {})
                cat = props.get("category", "")
                if cat != "restricted":
                    continue
                geom = feature.get("geometry", {})
                if geom.get("type") != "Polygon":
                    continue
                if _point_in_polygon(wp_lon, wp_lat, geom.get("coordinates", [])):
                    zone_name = props.get("name", f"Zone {feature.get('id', idx)}")
                    zone_desc = props.get("description", "")
                    alarms.append({
                        "severity": "CRITICAL",
                        "code": "ALM-10-WAYPOINT-IN-RESARE",
                        "title": f"Waypoint WP{idx} Inside Restricted Zone ({zone_name})",
                        "detail": (
                            f"Планируемая путевая точка WP{idx} ({wp_lon:.4f}, {wp_lat:.4f}) "
                            f"попадает внутри ограниченной навигационной зоны '{zone_name}' (RESARE). "
                            f"{zone_desc} Маршрут необходимо перепроложить, чтобы избежать запретной зоны."
                        ),
                        "waypoint_index": idx,
                        "hazard_location": [wp_lon, wp_lat],
                    })
                    break

    is_grid_available = (grid is not None and hasattr(grid, "lonlat_to_cell") and len(active_route_coords) >= 2)
    if not is_grid_available:
        alarms.append({
            "severity": "WARNING",
            "code": "ALM-07-GRID-UNAVAILABLE",
            "title": "Bathymetric Raster Grid Unavailable",
            "detail": "No raster grid was supplied; 2D corridor area verification could not be evaluated.",
        })

    # 4. Overall ECDIS Status Assessment
    has_critical = any(a["severity"] == "CRITICAL" for a in alarms)
    has_warning = any(a["severity"] == "WARNING" for a in alarms)

    if has_critical:
        status = "CRITICAL_HAZARD"
        summary = "ECDIS Route Check: FAILED. Critical bathymetric clearance or maneuvering hazards detected."
    elif has_warning:
        status = "WARNING_ADVISORY"
        summary = f"ECDIS Route Check: CAUTION. {len(alarms)} operational warnings require navigator review."
    else:
        status = "SAFE_PASSED"
        summary = "ECDIS Route Check: PASSED. No violations detected across depth, UKC, ROT, and XTD safety checks."

    return {
        "status": status,
        "is_safe": not has_critical,
        "summary": summary,
        "alarms_count": len(alarms),
        "alarms": alarms,
        "corridor_hazards": corridor_hazards,
        "metrics": {
            "safety_depth_m": round(safety_depth, 2),
            "dynamic_draft_m": round(effective_dyn_draft, 2),
            "dynamic_squat_m": round(dynamic_squat, 2),
            "min_clearance_m": round(min_observed_clearance, 2) if min_observed_clearance != math.inf else 0.0,
            "rot_threshold_deg_min": rot_threshold,
            "corridor_checked_2d_raster": is_grid_available,
        },
    }
