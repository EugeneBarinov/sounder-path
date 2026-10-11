"""
Maritime Kinematics, Turn Arcs & Wheel Over Point (WOP) Module
==============================================================
Implements IMO Resolution A.893(21) bridge passage planning standards:
- Fillet circular turn arcs with radius R tangent to incoming and outgoing legs.
- Point of Curvature (PC) and Point of Tangency (PT).
- Wheel Over Point (WOP) with bridge rudder execution delay.
- Rate of Turn (ROT) computation.
"""

from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
import math

DEG_TO_RAD = math.pi / 180.0
RAD_TO_DEG = 180.0 / math.pi
METERS_PER_DEG_LAT = 111139.0
KNOTS_TO_MS = 0.514444


def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate forward true compass bearing between two coordinates in degrees."""
    phi1 = lat1 * DEG_TO_RAD
    phi2 = lat2 * DEG_TO_RAD
    dlam = (lon2 - lon1) * DEG_TO_RAD

    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    theta = math.atan2(y, x) * RAD_TO_DEG
    return (theta + 360.0) % 360.0


def calculate_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute rhumb line / flat approximation distance in meters."""
    mean_lat = (lat1 + lat2) / 2.0
    m_per_deg_lon = METERS_PER_DEG_LAT * math.cos(mean_lat * DEG_TO_RAD)
    dy = (lat2 - lat1) * METERS_PER_DEG_LAT
    dx = (lon2 - lon1) * m_per_deg_lon
    return math.sqrt(dx * dx + dy * dy)


def point_along_bearing(lat: float, lon: float, bearing_deg: float, distance_m: float) -> Tuple[float, float]:
    """Compute new lat/lon from origin along given compass bearing by distance in meters."""
    brg_rad = bearing_deg * DEG_TO_RAD
    m_per_deg_lon = METERS_PER_DEG_LAT * math.cos(lat * DEG_TO_RAD)
    if abs(m_per_deg_lon) < 1.0:
        m_per_deg_lon = 1.0

    d_lat = (distance_m * math.cos(brg_rad)) / METERS_PER_DEG_LAT
    d_lon = (distance_m * math.sin(brg_rad)) / m_per_deg_lon
    return lat + d_lat, lon + d_lon


def calculate_turn_arc_geometry(
    prev_lon: float,
    prev_lat: float,
    wp_lon: float,
    wp_lat: float,
    next_lon: float,
    next_lat: float,
    radius_m: float,
    speed_knots: float,
    rudder_delay_sec: float = 12.0,
    arc_segments: int = 12,
) -> Optional[Dict[str, Any]]:
    """
    Computes a circular fillet turn arc tangent to legs (prev -> wp) and (wp -> next).
    Returns geometry of arc, PC, PT, and WOP coordinates.
    """
    brg_in = calculate_bearing_deg(prev_lat, prev_lon, wp_lat, wp_lon)
    brg_out = calculate_bearing_deg(wp_lat, wp_lon, next_lat, next_lon)

    # Angle deflection [-180, 180]
    turn_diff = (brg_out - brg_in + 180.0) % 360.0 - 180.0
    turn_angle = abs(turn_diff)

    if turn_angle < 3.0 or turn_angle > 170.0:
        return None  # Negligible turn or reverse course

    turn_direction = "STARBOARD" if turn_diff > 0 else "PORT"

    # Tangent distance from vertex: T = R * tan(theta / 2)
    theta_rad = turn_angle * DEG_TO_RAD
    tangent_dist_m = radius_m * math.tan(theta_rad / 2.0)

    dist_in = calculate_distance_m(prev_lat, prev_lon, wp_lat, wp_lon)
    dist_out = calculate_distance_m(wp_lat, wp_lon, next_lat, next_lon)

    # Cap tangent distance to 40% of available leg lengths to prevent overlap with adjacent legs
    max_tangent = min(dist_in, dist_out) * 0.40
    effective_tangent_m = min(tangent_dist_m, max_tangent)
    effective_radius_m = effective_tangent_m / math.tan(theta_rad / 2.0)
    is_feasible = (effective_tangent_m >= tangent_dist_m * 0.98)

    # Rate of Turn: ROT = (5556 / pi) * (V / R)
    rot_deg_min = (5556.0 / math.pi) * (speed_knots / effective_radius_m) if effective_radius_m > 0 else 0.0

    # Wheel Over Point: WOP = T + V * t_rudder
    v_ms = speed_knots * KNOTS_TO_MS
    wop_dist_m = effective_tangent_m + (v_ms * rudder_delay_sec)

    # PC: Point of Curvature (on incoming leg, backward from WP)
    back_brg_in = (brg_in + 180.0) % 360.0
    pc_lat, pc_lon = point_along_bearing(wp_lat, wp_lon, back_brg_in, effective_tangent_m)

    # PT: Point of Tangency (on outgoing leg, forward from WP)
    pt_lat, pt_lon = point_along_bearing(wp_lat, wp_lon, brg_out, effective_tangent_m)

    # WOP: Wheel Over Point (on incoming leg, backward from WP)
    wop_lat, wop_lon = point_along_bearing(wp_lat, wp_lon, back_brg_in, min(wop_dist_m, dist_in * 0.95))

    # Center of circular arc
    # Perpendicular to incoming leg at PC
    normal_pc = (brg_in + 90.0) if turn_direction == "STARBOARD" else (brg_in - 90.0)
    center_lat, center_lon = point_along_bearing(pc_lat, pc_lon, normal_pc, effective_radius_m)

    # Angles from center to PC and PT
    angle_center_to_pc = calculate_bearing_deg(center_lat, center_lon, pc_lat, pc_lon)
    angle_center_to_pt = calculate_bearing_deg(center_lat, center_lon, pt_lat, pt_lon)

    # Interpolate arc points
    arc_points: List[List[float]] = []
    diff_center = (angle_center_to_pt - angle_center_to_pc + 180.0) % 360.0 - 180.0

    for step in range(arc_segments + 1):
        fraction = step / float(arc_segments)
        cur_brg = (angle_center_to_pc + diff_center * fraction) % 360.0
        a_lat, a_lon = point_along_bearing(center_lat, center_lon, cur_brg, effective_radius_m)
        arc_points.append([round(a_lon, 6), round(a_lat, 6)])

    return {
        "turn_direction": turn_direction,
        "turn_angle_deg": round(turn_angle, 1),
        "effective_radius_m": round(effective_radius_m, 1),
        "rot_deg_min": round(rot_deg_min, 1),
        "tangent_dist_m": round(effective_tangent_m, 1),
        "wop_dist_m": round(wop_dist_m, 1),
        "is_feasible": is_feasible,
        "pc": [round(pc_lon, 6), round(pc_lat, 6)],
        "pt": [round(pt_lon, 6), round(pt_lat, 6)],
        "wop": [round(wop_lon, 6), round(wop_lat, 6)],
        "arc_coordinates": arc_points,
    }


def generate_turn_arcs_feature_collection(
    waypoints: List[Dict[str, Any]],
    radius_m: float,
    speed_knots: float,
) -> Dict[str, Any]:
    """
    Builds GeoJSON FeatureCollection of all circular turn arcs and WOP markers
    along a passage plan.
    """
    features: List[Dict[str, Any]] = []

    if len(waypoints) < 3:
        return {"type": "FeatureCollection", "features": []}

    for i in range(1, len(waypoints) - 1):
        prev = waypoints[i - 1]
        cur = waypoints[i]
        nxt = waypoints[i + 1]

        geom = calculate_turn_arc_geometry(
            prev_lon=prev["lon"],
            prev_lat=prev["lat"],
            wp_lon=cur["lon"],
            wp_lat=cur["lat"],
            next_lon=nxt["lon"],
            next_lat=nxt["lat"],
            radius_m=radius_m,
            speed_knots=speed_knots,
        )

        if not geom:
            continue

        is_feasible = cur.get("maneuver_feasible", True)

        # Feature 1: Turn Arc LineString
        features.append({
            "type": "Feature",
            "properties": {
                "role": "turn_arc",
                "wp_index": cur.get("index", i + 1),
                "turn_direction": geom["turn_direction"],
                "turn_angle_deg": geom["turn_angle_deg"],
                "rot_deg_min": geom["rot_deg_min"],
                "radius_m": geom["effective_radius_m"],
                "maneuver_feasible": is_feasible,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": geom["arc_coordinates"],
            },
        })

        # Feature 2: WOP Marker Point
        features.append({
            "type": "Feature",
            "properties": {
                "role": "wop_marker",
                "wp_index": cur.get("index", i + 1),
                "label": f"WOP {cur.get('index', i + 1)}",
                "distance_before_wp_m": geom["wop_dist_m"],
                "rot_deg_min": geom["rot_deg_min"],
                "turn_direction": geom["turn_direction"],
                "maneuver_feasible": is_feasible,
            },
            "geometry": {
                "type": "Point",
                "coordinates": geom["wop"],
            },
        })

    return {"type": "FeatureCollection", "features": features}


def interpolate_linear_points(p0: List[float], p1: List[float], step_m: float = 50.0) -> List[List[float]]:
    """Interpolates points between p0=[lon, lat] and p1=[lon, lat] every step_m meters."""
    dist_m = calculate_distance_m(p0[1], p0[0], p1[1], p1[0])
    if dist_m <= step_m:
        return [p0, p1]
    steps = max(1, int(dist_m / step_m))
    pts: List[List[float]] = []
    for s in range(steps + 1):
        frac = s / float(steps)
        lon = round(p0[0] + (p1[0] - p0[0]) * frac, 6)
        lat = round(p0[1] + (p1[1] - p0[1]) * frac, 6)
        pts.append([lon, lat])
    return pts


def construct_filleted_path(
    waypoints: List[Dict[str, Any]],
    default_radius_m: float = 150.0,
    speed_knots: float = 10.0,
    step_m: float = 50.0,
) -> Tuple[List[List[float]], List[Dict[str, Any]]]:
    """
    Constructs a continuous filleted navigation track through waypoints, replacing
    sharp course alterations with circular fillet turn arcs based on each waypoint's radius.
    Returns (dense_track_coordinates, turn_geometries).
    """
    n = len(waypoints)
    if n == 0:
        return [], []
    if n == 1:
        wp0 = waypoints[0]
        lon = wp0.get("lon") if wp0.get("lon") is not None else wp0.get("longitude", 0.0)
        lat = wp0.get("lat") if wp0.get("lat") is not None else wp0.get("latitude", 0.0)
        return [[float(lon), float(lat)]], []

    # Extract coordinates
    coords = []
    for wp in waypoints:
        lon = float(wp.get("lon") if wp.get("lon") is not None else (wp.get("lng") if wp.get("lng") is not None else wp.get("longitude", 0.0)))
        lat = float(wp.get("lat") if wp.get("lat") is not None else wp.get("latitude", 0.0))
        coords.append([lon, lat])

    if n == 2:
        return interpolate_linear_points(coords[0], coords[1], step_m=step_m), []

    # Calculate turn geometries for each intermediate waypoint
    turn_geoms: List[Optional[Dict[str, Any]]] = [None] * n
    for i in range(1, n - 1):
        raw_r = waypoints[i].get("turn_radius_m")
        req_r = float(raw_r if raw_r is not None and raw_r > 0.0 else default_radius_m)
        if req_r > 0.0:
            geom = calculate_turn_arc_geometry(
                prev_lon=coords[i - 1][0],
                prev_lat=coords[i - 1][1],
                wp_lon=coords[i][0],
                wp_lat=coords[i][1],
                next_lon=coords[i + 1][0],
                next_lat=coords[i + 1][1],
                radius_m=req_r,
                speed_knots=speed_knots,
            )
            if geom:
                geom["wp_index"] = i + 1
                geom["requested_radius_m"] = req_r
                turn_geoms[i] = geom

    # Stitch the continuous path: straight leg -> turn arc -> straight leg ...
    full_track: List[List[float]] = []
    current_pt = coords[0]

    for i in range(1, n):
        geom = turn_geoms[i] if i < n - 1 else None
        if geom is not None:
            # Straight segment up to Point of Curvature (PC)
            pc_pt = geom["pc"]
            seg_to_pc = interpolate_linear_points(current_pt, pc_pt, step_m=step_m)
            if full_track:
                full_track.extend(seg_to_pc[1:])
            else:
                full_track.extend(seg_to_pc)

            # Circular arc segment (from PC to PT)
            arc_pts = geom["arc_coordinates"]
            if arc_pts:
                full_track.extend(arc_pts[1:])
                current_pt = geom["pt"]
            else:
                current_pt = pc_pt
        else:
            # Straight segment up to waypoint i
            dest_pt = coords[i]
            seg = interpolate_linear_points(current_pt, dest_pt, step_m=step_m)
            if full_track:
                full_track.extend(seg[1:])
            else:
                full_track.extend(seg)
            current_pt = dest_pt

    active_geoms = [g for g in turn_geoms if g is not None]
    return full_track, active_geoms


def splice_turn_arcs_into_route(
    route_coords: List[List[float]],
    turn_geometries: List[Dict[str, Any]],
) -> List[List[float]]:
    """
    Slices and splices circular fillet turn arcs into an existing route line,
    replacing sharp corner vertices between PC and PT with smooth circular arc points.
    """
    if len(route_coords) < 3 or not turn_geometries:
        return route_coords

    result_route = list(route_coords)

    # Process turns in reverse order so coordinate replacement indices don't shift earlier turns
    sorted_turns = sorted(
        [tg for tg in turn_geometries if tg.get("arc_coordinates") and len(tg["arc_coordinates"]) >= 2],
        key=lambda tg: tg.get("wp_index", 0),
        reverse=True,
    )

    for tg in sorted_turns:
        arc_pts = tg["arc_coordinates"]
        pc = tg["pc"]
        pt = tg["pt"]

        # Find closest point indices in route to PC and PT
        pc_idx = -1
        pc_min_d = float("inf")
        pt_idx = -1
        pt_min_d = float("inf")

        for idx, coord in enumerate(result_route):
            d_pc = calculate_distance_m(coord[1], coord[0], pc[1], pc[0])
            if d_pc < pc_min_d:
                pc_min_d = d_pc
                pc_idx = idx

            d_pt = calculate_distance_m(coord[1], coord[0], pt[1], pt[0])
            if d_pt < pt_min_d:
                pt_min_d = d_pt
                pt_idx = idx

        # Only splice if PC and PT are well-ordered and within a reasonable distance
        if 0 <= pc_idx < pt_idx < len(result_route) and pc_min_d < 300.0 and pt_min_d < 300.0:
            result_route = result_route[:pc_idx] + arc_pts + result_route[pt_idx + 1:]

    return result_route


