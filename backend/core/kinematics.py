"""
Maritime Kinematics, Turn Arcs & Wheel Over Point (WOP) Module
==============================================================
Implements IMO Resolution A.893(21) bridge passage planning standards:
- Fillet circular turn arcs with radius R tangent to incoming and outgoing legs.
- Point of Curvature (PC) and Point of Tangency (PT).
- Wheel Over Point (WOP) with bridge rudder execution delay.
- Rate of Turn (ROT) computation.
"""

from typing import List, Dict, Any, Tuple
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

    # Cap tangent distance to 85% of available leg lengths to prevent overlap
    max_tangent = min(dist_in, dist_out) * 0.85
    effective_tangent_m = min(tangent_dist_m, max_tangent)
    effective_radius_m = effective_tangent_m / math.tan(theta_rad / 2.0)

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
            },
            "geometry": {
                "type": "Point",
                "coordinates": geom["wop"],
            },
        })

    return {"type": "FeatureCollection", "features": features}
