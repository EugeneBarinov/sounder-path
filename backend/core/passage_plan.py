"""
Passage Planning Module (IMO Resolution A.893(21) / SOLAS V Reg. 34)
=====================================================================
Generates standardized nautical voyage plan documents including:
- Hydrodynamic Under Keel Clearance (DUKC) budget
- Bunkering & fuel consumption calculations
- Navigational waypoints log with nautical bearings and distances
- Limiting shallow bottlenecks and contingency ports of refuge
- Port State Control (PSC) compliant signature sign-off
"""

from typing import Dict, Any, List, Optional
import math
from datetime import datetime, timezone


def _dec_to_dmm(deg: float, is_lat: bool) -> str:
    """Format decimal degrees into nautical standard DD°MM.MM' N/S/E/W format."""
    hemi = ("N" if deg >= 0 else "S") if is_lat else ("E" if deg >= 0 else "W")
    val = abs(deg)
    d = int(val)
    m = (val - d) * 60.0
    pad = 2 if is_lat else 3
    return f"{d:0{pad}d}°{m:05.2f}'{hemi}"


def generate_passage_plan(
    route_data: Dict[str, Any],
    vessel_profile: str = "yacht",
    vessel_name: str = "M/V SeaPath",
    custom_params: Optional[Dict[str, float]] = None,
    ports_db: Optional[List[Dict[str, Any]]] = None,
    anchorages_db: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Constructs an official IMO Resolution A.893(21) Passage Plan document.
    """
    props = route_data.get("properties", {})
    waypoints = route_data.get("waypoints", [])
    profile = route_data.get("profile", [])
    bottleneck = route_data.get("bottleneck", {})

    params = custom_params or {}
    draft_m = float(params.get("draft", props.get("draft", 1.2)))
    speed_knots = float(params.get("speed_knots", props.get("speed_knots", 10.0)))
    base_ukc_m = float(params.get("ukc", props.get("base_ukc_m", 0.5)))
    wave_hs_m = float(params.get("wave_height_m", 0.0))
    tide_m = float(params.get("tide_offset_m", 0.0))
    xtd_m = float(params.get("xtd_m", 185.2))

    # Fuel rates: Liters per hour for small/medium craft, or tons per hour for cargo
    default_burn_l_h = {
        "yacht": 18.0,
        "ferry": 420.0,
        "cargo": 1200.0,
        "custom": 25.0
    }.get(vessel_profile.lower(), 20.0)

    burn_rate_l_h = float(params.get("fuel_burn_l_h", default_burn_l_h))

    dist_nm = float(props.get("distance_nm", 0.0))
    dist_m = dist_nm * 1852.0

    # Hydrodynamics
    cb = 0.82 if vessel_profile == "cargo" else (0.65 if vessel_profile == "ferry" else 0.50)
    squat_m = (cb * (speed_knots ** 2)) / 100.0
    wave_allowance_m = 0.35 * wave_hs_m
    dynamic_draft_m = draft_m + squat_m + wave_allowance_m
    required_chart_depth_m = max(0.5, dynamic_draft_m + base_ukc_m - tide_m)

    ete_hours = (dist_nm / speed_knots) if speed_knots > 0 else 0.0
    ete_mins = int(round(ete_hours * 60))
    ete_formatted = f"{ete_mins // 60} ч {ete_mins % 60:02d} мин"

    # Fuel estimation
    fuel_nominal_liters = round(ete_hours * burn_rate_l_h, 1)
    fuel_reserve_liters = round(fuel_nominal_liters * 0.20, 1)  # IMO +20% contingency reserve
    fuel_total_liters = round(fuel_nominal_liters + fuel_reserve_liters, 1)

    # Waypoints tabular itinerary
    wp_log: List[Dict[str, Any]] = []
    cum_dist_nm = 0.0

    for i, wp in enumerate(waypoints):
        leg_nm = float(wp.get("leg_distance_nm", 0.0))
        cum_dist_nm += leg_nm
        coords = wp.get("coords", [0.0, 0.0])
        role = wp.get("role", "via")

        leg_time_sec = (leg_nm / speed_knots * 3600.0) if speed_knots > 0 else 0.0
        leg_time_min = round(leg_time_sec / 60.0)

        role_name = "Отход (A)" if role == "start" else ("Приход (B)" if role == "goal" else f"РТ {i + 1}")

        wp_log.append({
            "index": i + 1,
            "role": role_name,
            "coords_deg": [round(coords[0], 5), round(coords[1], 5)],
            "lat_dmm": _dec_to_dmm(coords[1], is_lat=True),
            "lon_dmm": _dec_to_dmm(coords[0], is_lat=False),
            "bearing_true": round(float(wp.get("leg_bearing_deg", 0.0)), 1) if i < len(waypoints) - 1 else None,
            "leg_dist_nm": round(leg_nm, 2),
            "cum_dist_nm": round(cum_dist_nm, 2),
            "speed_knots": speed_knots,
            "leg_ete_min": leg_time_min,
            "turn_radius_m": float(wp.get("turn_radius_m", 50.0)),
            "wop_dist_m": round(float(wp.get("wop_distance_m", 0.0)), 1)
        })

    # Find departure and destination port metadata
    start_pt = waypoints[0]["coords"] if waypoints else [0, 0]
    goal_pt = waypoints[-1]["coords"] if waypoints else [0, 0]

    dep_port = None
    arr_port = None

    if ports_db:
        # Match closest port within 8 km
        def find_nearest_port(pt):
            best = None
            best_d = 999999.0
            for p in ports_db:
                c = p.get("approach_coords") or p.get("port_coords") or [0, 0]
                dx = (c[0] - pt[0]) * 111139.0 * math.cos(math.radians(pt[1]))
                dy = (c[1] - pt[1]) * 111139.0
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < best_d and dist <= 15000.0:
                    best_d = dist
                    best = p
            return best

        dep_port = find_nearest_port(start_pt)
        arr_port = find_nearest_port(goal_pt)

    # Ports of refuge & emergency anchorages
    safe_havens: List[Dict[str, Any]] = []
    if anchorages_db:
        for anc in anchorages_db[:4]:
            safe_havens.append({
                "name": anc.get("name"),
                "short_name": anc.get("short_name"),
                "coords": anc.get("coords"),
                "depth_range_m": f"{anc.get('depth_min_m', 0)}-{anc.get('depth_max_m', 0)} м",
                "holding_ground": anc.get("holding_ground"),
                "shelter": anc.get("shelter"),
                "vhf_channel": anc.get("vhf_channel")
            })

    # Summary of bottleneck hazard
    bottleneck_info = None
    if bottleneck and bottleneck.get("coords"):
        b_coords = bottleneck.get("coords")
        bottleneck_info = {
            "coords_dmm": f"{_dec_to_dmm(b_coords[1], is_lat=True)} {_dec_to_dmm(b_coords[0], is_lat=False)}",
            "depth_m": round(float(bottleneck.get("depth", 0.0)), 2),
            "clearance_m": round(float(bottleneck.get("clearance", 0.0)), 2),
            "distance_from_start_nm": round(float(bottleneck.get("distance_from_start_m", 0.0)) / 1852.0, 2),
            "recommended_speed_knots": bottleneck.get("speed_adaptation", {}).get("recommended_speed_knots")
        }

    return {
        "passage_plan_id": f"PP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}",
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "compliance": "IMO Res. A.893(21), SOLAS Chapter V Reg. 34, PIANC MarCom WG 121",
        "vessel": {
            "name": vessel_name,
            "type_profile": vessel_profile,
            "static_draft_m": draft_m,
            "speed_knots": speed_knots,
            "block_coefficient": cb,
            "fuel_burn_rate_l_h": burn_rate_l_h
        },
        "voyage_summary": {
            "departure": dep_port.get("name") if dep_port else "Точка отхода",
            "destination": arr_port.get("name") if arr_port else "Точка прибытия",
            "departure_vhf": dep_port.get("vhf_channel") if dep_port else "VHF Ch 16",
            "destination_vhf": arr_port.get("vhf_channel") if arr_port else "VHF Ch 16",
            "total_distance_nm": round(dist_nm, 2),
            "estimated_time_enroute": ete_formatted,
            "planned_speed_knots": speed_knots,
            "corridor_xtd_m": xtd_m
        },
        "dukc_safety_budget": {
            "static_draft_m": draft_m,
            "barrass_squat_m": round(squat_m, 2),
            "wave_allowance_m": round(wave_allowance_m, 2),
            "tide_offset_m": round(tide_m, 2),
            "dynamic_draft_m": round(dynamic_draft_m, 2),
            "base_ukc_required_m": base_ukc_m,
            "minimum_safety_chart_depth_m": round(required_chart_depth_m, 2),
            "actual_min_depth_on_route_m": round(float(props.get("min_chart_depth_m", 0.0)), 2),
            "safety_status": "SAFE" if props.get("is_safe", True) else "CRITICAL_HAZARD"
        },
        "bunkering_fuel_budget": {
            "nominal_consumption_liters": fuel_nominal_liters,
            "reserve_contingency_liters": fuel_reserve_liters,
            "total_fuel_required_liters": fuel_total_liters,
            "reserve_percentage": 20
        },
        "waypoints": wp_log,
        "critical_bottleneck": bottleneck_info,
        "ports_of_refuge": safe_havens,
        "communications": {
            "international_distress": "VHF Ch 16 (156.800 MHz)",
            "calling_working": "VHF Ch 14 / 12 / 69"
        }
    }
