"""
Bottleneck & Choke Point Analysis Module (PIANC & IMO Feasibility)
==================================================================
Identifies navigational limiting sections, calculates depth deficits,
maximum allowable static draft, speed adaptation for squat mitigation,
and tidal requirements.
"""

from typing import List, Dict, Any, Optional
import math
from backend.core.dukc import (
    calculate_pianc_squat,
    calculate_turning_heel,
    calculate_wave_response,
    calculate_tidal_window,
)


def analyze_route_bottlenecks(
    profile: List[Dict[str, Any]],
    draft_m: float,
    speed_knots: float,
    ukc_net_margin_m: float = 1.0,
    block_coefficient: float = 0.65,
    turning_radius_m: float = 250.0,
    wave_height_m: float = 0.0,
    tide_offset_m: float = 0.0,
    beam_m: Optional[float] = None,
    gm_m: float = 1.5,
    exposed_waters: bool = False,
) -> Dict[str, Any]:
    """
    Scans the bathymetric profile of a route to identify the critical choke point.
    Computes:
    - Minimum clearance sounding and exact geographic location.
    - Depth deficit (if any).
    - Maximum allowable static draft at current speed.
    - Speed adaptation advisory: recommended reduced speed to lower squat and pass safely.
    - Tidal window requirements to clear the bottleneck.
    """
    if not profile:
        return {"has_bottleneck": False, "bottlenecks": []}

    wave_allowance = calculate_wave_response(wave_height_m, exposed_waters=exposed_waters)
    heel_info = calculate_turning_heel(speed_knots, turning_radius_m, draft_m, beam_m=beam_m, gm_m=gm_m)
    heel_sinkage = heel_info["heel_sinkage_m"]

    min_clearance = math.inf
    shallowest_sounding: Optional[Dict[str, Any]] = None

    for pt in profile:
        chart_depth = pt.get("depth", 0.0)
        eff_depth = chart_depth + tide_offset_m
        h_over_t = (eff_depth / draft_m) if draft_m > 0 else 0.0
        squat = calculate_pianc_squat(speed_knots, block_coefficient, h_over_t)

        total_req_depth = draft_m + squat + heel_sinkage + wave_allowance + ukc_net_margin_m
        net_clearance = eff_depth - total_req_depth

        if net_clearance < min_clearance:
            min_clearance = net_clearance
            shallowest_sounding = {
                "lon": pt.get("lon"),
                "lat": pt.get("lat"),
                "chart_depth_m": round(chart_depth, 2),
                "effective_depth_m": round(eff_depth, 2),
                "distance_nm": round(pt.get("distance_from_start_m", 0.0) / 1852.0, 2),
                "squat_m": round(squat, 2),
                "net_clearance_m": round(net_clearance, 2),
                "required_depth_m": round(total_req_depth, 2),
            }

    if not shallowest_sounding:
        return {"has_bottleneck": False}

    is_grounding_hazard = min_clearance < 0.0
    is_critical_margin = 0.0 <= min_clearance < 0.5

    # Maximum allowable draft at current speed over the choke point
    choke_eff_depth = shallowest_sounding["effective_depth_m"]
    choke_squat = shallowest_sounding["squat_m"]
    max_allowable_draft = max(0.5, choke_eff_depth - choke_squat - heel_sinkage - wave_allowance - ukc_net_margin_m)

    # Speed adaptation calculation:
    # If clearance is negative, check if reducing speed can eliminate enough squat
    speed_adaptation: Optional[Dict[str, Any]] = None
    if is_grounding_hazard:
        depth_deficit = abs(min_clearance)
        # Allowable squat = effective depth - (draft + heel + wave + ukc)
        allowable_squat = choke_eff_depth - (draft_m + heel_sinkage + wave_allowance + ukc_net_margin_m)
        if allowable_squat > 0.0:
            k_factor = 50.0
            safe_cb = max(0.40, min(0.95, block_coefficient))
            safe_v_sq = (allowable_squat * k_factor) / safe_cb
            if safe_v_sq > 0.0:
                safe_speed = math.sqrt(safe_v_sq)
                # Ships lose steerage / rudder authority below ~3.0 knots
                if safe_speed >= 3.0 and speed_knots > 3.0:
                    rec_speed = round(min(safe_speed, max(3.0, speed_knots - 0.5)), 1)
                    actual_squat_at_rec = (safe_cb * (rec_speed ** 2)) / k_factor
                    squat_red = max(0.0, choke_squat - actual_squat_at_rec)
                    speed_adaptation = {
                        "is_viable": True,
                        "current_speed_knots": round(speed_knots, 1),
                        "recommended_speed_knots": rec_speed,
                        "squat_reduction_m": round(squat_red, 2),
                        "advisory": (
                            f"Reduce speed from {speed_knots:.1f} kts to {rec_speed:.1f} kts "
                            f"in bottleneck section ({shallowest_sounding['distance_nm']} NM from departure) "
                            f"to reduce squat by {squat_red:.2f}m and restore safe UKC."
                        ),
                    }
                else:
                    speed_adaptation = {
                        "is_viable": False,
                        "current_speed_knots": round(speed_knots, 1),
                        "recommended_speed_knots": None,
                        "squat_reduction_m": 0.0,
                        "advisory": (
                            f"Speed reduction alone cannot clear depth deficit ({depth_deficit:.2f}m): "
                            f"required safe speed ({safe_speed:.1f} kts) is below minimum steerage speed (3.0 kts). "
                            f"Passage requires tidal surge or draft lightering."
                        ),
                    }
        else:
            speed_adaptation = {
                "is_viable": False,
                "current_speed_knots": round(speed_knots, 1),
                "recommended_speed_knots": None,
                "squat_reduction_m": 0.0,
                "advisory": (
                    f"Zero speed squat cannot eliminate depth deficit ({depth_deficit:.2f}m). "
                    f"Static draft ({draft_m:.2f}m) + required UKC exceeds water depth ({choke_eff_depth:.2f}m)."
                ),
            }

    # Tidal window requirement for this bottleneck:
    # Compares charted depth with total required water depth (without double-subtracting tide_offset)
    tidal_analysis = calculate_tidal_window(
        min_chart_depth_m=shallowest_sounding["chart_depth_m"],
        required_safe_depth_m=shallowest_sounding["required_depth_m"],
    )

    return {
        "has_critical_bottleneck": is_grounding_hazard or is_critical_margin,
        "is_grounding_hazard": is_grounding_hazard,
        "shallowest_point": shallowest_sounding,
        "max_allowable_draft_m": round(max_allowable_draft, 2),
        "depth_deficit_m": round(abs(min_clearance), 2) if is_grounding_hazard else 0.0,
        "speed_adaptation": speed_adaptation,
        "tidal_window": tidal_analysis,
    }
