"""
Dynamic Under-Keel Clearance (DUKC) & Hydrodynamic Budget Module
================================================================
Implements PIANC MarCom WG 121, IMO Res. A.893(21), and IHO standards
for vessel dynamic motion in shallow water and confined fairways:
- PIANC / Barrass shallow-water squat with bottom suction amplification.
- Turning heel sinkage (centripetal tilt during course changes).
- Wave action motion allowance (heave, pitch, roll response).
- Dynamic water level and tidal window feasibility calculation.
"""

from typing import Dict, Any, Optional
import math


# Physical constants
GRAVITY_G = 9.80665              # Acceleration due to gravity (m/s^2)
KNOTS_TO_MS = 0.514444           # 1 knot in meters per second
RAD_TO_DEG = 180.0 / math.pi
DEG_TO_RAD = math.pi / 180.0


def calculate_pianc_squat(
    speed_knots: float,
    block_coefficient: float = 0.65,
    h_over_t: float = 0.0,
) -> float:
    """
    Computes dynamic ship sinkage in meters based on Barrass / PIANC formulations.
    Formula: Delta_h = (Cb * V^2) / K
    where:
      - K = 100 for open, deep water (h/T >= 1.5)
      - K = 50 for confined, shallow water (h/T <= 1.2, bottom suction effect)
      - Smooth linear interpolation for 1.2 < h/T < 1.5
    """
    k = 100.0
    if h_over_t > 0.0:
        if h_over_t <= 1.2:
            k = 50.0
        elif h_over_t < 1.5:
            k = 50.0 + 50.0 * ((h_over_t - 1.2) / 0.3)
    return (block_coefficient * (speed_knots ** 2)) / k


def calculate_turning_heel(
    speed_knots: float,
    turning_radius_m: float,
    draft_m: float,
    beam_m: Optional[float] = None,
    gm_m: float = 1.5,
) -> Dict[str, float]:
    """
    Calculates steady heel angle and outboard bilge keel sinkage during a turn.
    
    Centripetal acceleration causes heel towards the outside of the turn:
      tan(theta) = (V^2 * (KG - d/2)) / (g * R * GM)
    where:
      V: vessel speed in m/s
      R: turning radius in meters
      KG - d/2: estimated vertical center of gravity lever arm (~ 0.25 * d)
      GM: transverse metacentric height (typically 1.0 - 2.5m for merchant vessels)
      
    Outboard bilge immersion increase:
      Delta_d = (Beam / 2) * sin(theta)
    """
    if speed_knots <= 0.0 or turning_radius_m <= 10.0 or draft_m <= 0.0:
        return {"heel_angle_deg": 0.0, "heel_sinkage_m": 0.0}

    # Estimate beam if not provided (typical cargo/ferry ratio B/T ~ 2.5)
    beam = beam_m if beam_m and beam_m > 0 else max(3.0, 2.5 * draft_m)
    v_ms = speed_knots * KNOTS_TO_MS

    # Lever arm: vertical center of gravity above center of buoyancy
    lever_arm = max(0.5, 0.25 * draft_m)
    gm = max(0.5, gm_m)

    tan_theta = (v_ms ** 2 * lever_arm) / (GRAVITY_G * turning_radius_m * gm)
    # Clamp maximum realistic steady heel angle to 20 degrees for safety
    theta_rad = min(math.atan(tan_theta), 20.0 * DEG_TO_RAD)
    heel_deg = theta_rad * RAD_TO_DEG

    # Additional draft on the lower (outboard) bilge keel
    heel_sinkage = (beam / 2.0) * math.sin(theta_rad)

    return {
        "heel_angle_deg": round(heel_deg, 2),
        "heel_sinkage_m": round(heel_sinkage, 3),
        "beam_m": round(beam, 1),
    }


def calculate_wave_response(
    significant_wave_height_m: float,
    exposed_waters: bool = False,
) -> float:
    """
    Computes required under-keel wave motion allowance (heave, pitch, roll).
    Per PIANC MarCom WG 121:
      - Sheltered / port approach waters: 0.35 * Hs
      - Exposed coastal waters: 0.50 * Hs
    """
    factor = 0.50 if exposed_waters else 0.35
    wave_allowance = max(0.0, factor * significant_wave_height_m)
    return round(wave_allowance, 3)


def compute_dukc_budget(
    draft_m: float,
    speed_knots: float,
    block_coefficient: float = 0.65,
    ukc_net_margin_m: float = 1.0,
    water_depth_m: float = 10.0,
    turning_radius_m: float = 250.0,
    wave_height_m: float = 0.0,
    tide_offset_m: float = 0.0,
    beam_m: Optional[float] = None,
    gm_m: float = 1.5,
    exposed_waters: bool = False,
) -> Dict[str, Any]:
    """
    Synthesizes the complete Dynamic Under-Keel Clearance budget per PIANC WG 121.
    
    Total Dynamic Draft = Static Draft + Squat + Heel Sinkage + Wave Allowance
    Gross Clearance = (Chart Depth + Tide Offset) - Total Dynamic Draft
    Net Margin = Gross Clearance - Minimum Required UKC
    """
    effective_depth = water_depth_m + tide_offset_m
    h_over_t = (effective_depth / draft_m) if draft_m > 0 else 0.0

    dynamic_squat = calculate_pianc_squat(speed_knots, block_coefficient, h_over_t)
    heel_info = calculate_turning_heel(speed_knots, turning_radius_m, draft_m, beam_m, gm_m)
    wave_allowance = calculate_wave_response(wave_height_m, exposed_waters)

    total_dynamic_draft = draft_m + dynamic_squat + heel_info["heel_sinkage_m"] + wave_allowance
    gross_clearance = effective_depth - total_dynamic_draft
    clearance_margin = gross_clearance - ukc_net_margin_m

    return {
        "effective_depth_m": round(effective_depth, 2),
        "tide_offset_m": round(tide_offset_m, 2),
        "static_draft_m": round(draft_m, 2),
        "dynamic_squat_m": round(dynamic_squat, 2),
        "heel_sinkage_m": heel_info["heel_sinkage_m"],
        "heel_angle_deg": heel_info["heel_angle_deg"],
        "wave_allowance_m": wave_allowance,
        "total_dynamic_draft_m": round(total_dynamic_draft, 2),
        "gross_clearance_m": round(gross_clearance, 2),
        "required_safety_depth_m": round(total_dynamic_draft + ukc_net_margin_m, 2),
        "clearance_margin_m": round(clearance_margin, 2),
        "is_safe": clearance_margin >= 0.0,
    }


def calculate_tidal_window(
    min_chart_depth_m: float,
    required_safe_depth_m: float,
    tide_amplitude_m: float = 0.8,
    tide_period_hours: float = 12.42,
) -> Dict[str, Any]:
    """
    Determines tidal window feasibility for a shallow passage using M2 harmonic tidal model.
    Tide curve: h_tide(t) = A * cos(2 * pi * t / T), where t=0 is High Water (HW).
    """
    depth_deficit = required_safe_depth_m - min_chart_depth_m

    if depth_deficit <= 0.0:
        return {
            "is_feasible_anytime": True,
            "window_duration_hours": tide_period_hours,
            "window_before_hw_hours": tide_period_hours / 2.0,
            "window_after_hw_hours": tide_period_hours / 2.0,
            "required_tide_elevation_m": 0.0,
            "tide_amplitude_m": tide_amplitude_m,
            "notes": "Safe at all tidal phases (charted depths sufficient).",
        }

    if depth_deficit > tide_amplitude_m:
        return {
            "is_feasible_anytime": False,
            "window_duration_hours": 0.0,
            "window_before_hw_hours": 0.0,
            "window_after_hw_hours": 0.0,
            "required_tide_elevation_m": round(depth_deficit, 2),
            "tide_amplitude_m": tide_amplitude_m,
            "notes": (
                f"Deficit of {depth_deficit:.2f}m exceeds maximum high-water tide elevation "
                f"({tide_amplitude_m:.2f}m). Passage prohibited without dredging or lightering."
            ),
        }

    # Deficit is reachable during High Water (HW)
    # cos(2 * pi * t / T) = deficit / A
    cos_val = max(-1.0, min(1.0, depth_deficit / tide_amplitude_m))
    theta_rad = math.acos(cos_val)
    half_window_hours = (theta_rad / math.pi) * (tide_period_hours / 2.0)
    total_window_hours = 2.0 * half_window_hours

    return {
        "is_feasible_anytime": False,
        "window_duration_hours": round(total_window_hours, 2),
        "window_before_hw_hours": round(half_window_hours, 2),
        "window_after_hw_hours": round(half_window_hours, 2),
        "required_tide_elevation_m": round(depth_deficit, 2),
        "tide_amplitude_m": tide_amplitude_m,
        "notes": (
            f"Passage permitted within {total_window_hours:.1f} hour tidal window "
            f"(+/-{half_window_hours:.1f}h around High Water) requiring >= +{depth_deficit:.2f}m surge/tide."
        ),
    }
