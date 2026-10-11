"""
DUKC Physical Model & Hydrodynamic Validation Suite
===================================================
Rigorous validation of hydrodynamics, Dynamic Under-Keel Clearance (DUKC),
and passage planning safety against PIANC MarCom WG 121, IMO Res. A.893(21),
and IMO Res. MSC.137(76) maneuvering standards.
"""

import math
import pytest
from backend.core.dukc import (
    calculate_pianc_squat,
    calculate_turning_heel,
    calculate_wave_response,
    compute_dukc_budget,
    compute_search_budget,
    calculate_tidal_window,
)
from backend.core.safety import verify_ecdis_route_safety


# ==============================================================================
# 1. PIANC / Barrass Hydrodynamic Squat Validation
# ==============================================================================

def test_barrass_squat_deep_water():
    """In deep open water (h/T >= 1.5), K factor is 100.0."""
    # Scenario: Container vessel Cb=0.65, V=12 knots, deep water h/T=2.0
    # Delta_h = (0.65 * 144) / 100 = 0.936 m
    squat = calculate_pianc_squat(speed_knots=12.0, block_coefficient=0.65, h_over_t=2.0)
    assert pytest.approx(squat, abs=1e-3) == 0.936

    # Scenario: Bulk carrier Cb=0.85, V=8 knots, deep water h/T=1.8
    # Delta_h = (0.85 * 64) / 100 = 0.544 m
    squat_bulk = calculate_pianc_squat(speed_knots=8.0, block_coefficient=0.85, h_over_t=1.8)
    assert pytest.approx(squat_bulk, abs=1e-3) == 0.544


def test_barrass_squat_shallow_confined_water():
    """In shallow confined channels (h/T <= 1.2), bottom suction doubles squat (K=50.0)."""
    # Scenario: Container vessel Cb=0.65, V=12 knots, confined water h/T=1.1
    # Delta_h = (0.65 * 144) / 50 = 1.872 m
    squat_confined = calculate_pianc_squat(speed_knots=12.0, block_coefficient=0.65, h_over_t=1.1)
    assert pytest.approx(squat_confined, abs=1e-3) == 1.872

    # Exactly 2x deep-water sinkage
    squat_deep = calculate_pianc_squat(speed_knots=12.0, block_coefficient=0.65, h_over_t=1.6)
    assert pytest.approx(squat_confined, rel=1e-3) == 2.0 * squat_deep


def test_barrass_squat_transition_regime():
    """Between h/T=1.2 and 1.5, K linearly interpolates between 50 and 100."""
    # At h/T = 1.35, K = 50 + 50 * (0.15 / 0.3) = 75.0
    # Delta_h = (0.75 * 100) / 75.0 = 1.0 m
    squat_mid = calculate_pianc_squat(speed_knots=10.0, block_coefficient=0.75, h_over_t=1.35)
    assert pytest.approx(squat_mid, abs=1e-3) == 1.0


def test_barrass_squat_zero_speed():
    """At zero speed, hydrodynamic sinkage is identically zero."""
    assert calculate_pianc_squat(speed_knots=0.0, block_coefficient=0.65, h_over_t=1.1) == 0.0


def test_barrass_squat_monotonicity():
    """Squat increases monotonically with speed and decreases with depth ratio h/T."""
    speeds = [4.0, 6.0, 8.0, 10.0, 12.0, 14.0]
    squats = [calculate_pianc_squat(s, 0.65, 1.3) for s in speeds]
    for i in range(len(squats) - 1):
        assert squats[i + 1] > squats[i]

    depth_ratios = [1.1, 1.25, 1.4, 1.6, 2.0]
    squats_h = [calculate_pianc_squat(10.0, 0.65, ht) for ht in depth_ratios]
    for i in range(len(squats_h) - 1):
        assert squats_h[i + 1] <= squats_h[i]


# ==============================================================================
# 2. Centripetal Turning Heel Sinkage Validation
# ==============================================================================

def test_turning_heel_physical_invariants():
    """Verify physical invariants of steady turning heel: radius, GM, speed."""
    base = calculate_turning_heel(speed_knots=10.0, turning_radius_m=300.0, draft_m=6.0, beam_m=18.0, gm_m=1.5)
    assert base["heel_angle_deg"] > 0.0
    assert base["heel_sinkage_m"] > 0.0

    # 1. Larger turning radius strictly reduces heel angle and sinkage
    wider = calculate_turning_heel(speed_knots=10.0, turning_radius_m=600.0, draft_m=6.0, beam_m=18.0, gm_m=1.5)
    assert wider["heel_angle_deg"] < base["heel_angle_deg"]
    assert wider["heel_sinkage_m"] < base["heel_sinkage_m"]

    # 2. Greater metacentric height GM (stiffer ship) strictly reduces heel
    stiffer = calculate_turning_heel(speed_knots=10.0, turning_radius_m=300.0, draft_m=6.0, beam_m=18.0, gm_m=2.5)
    assert stiffer["heel_angle_deg"] < base["heel_angle_deg"]
    assert stiffer["heel_sinkage_m"] < base["heel_sinkage_m"]

    # 3. Higher speed strictly increases heel
    faster = calculate_turning_heel(speed_knots=14.0, turning_radius_m=300.0, draft_m=6.0, beam_m=18.0, gm_m=1.5)
    assert faster["heel_angle_deg"] > base["heel_angle_deg"]
    assert faster["heel_sinkage_m"] > base["heel_sinkage_m"]

    # 4. Zero speed or zero turn produces 0.0 heel
    straight = calculate_turning_heel(speed_knots=0.0, turning_radius_m=300.0, draft_m=6.0)
    assert straight["heel_sinkage_m"] == 0.0
    assert straight["heel_angle_deg"] == 0.0


def test_turning_heel_extreme_clamp():
    """Extreme centripetal forces are clamped at 20 degrees maximum steady heel."""
    extreme = calculate_turning_heel(speed_knots=40.0, turning_radius_m=50.0, draft_m=10.0, beam_m=25.0, gm_m=0.5)
    assert extreme["heel_angle_deg"] <= 20.0


# ==============================================================================
# 3. Wave Action Motion Allowance Validation
# ==============================================================================

def test_wave_response_allowance():
    """PIANC wave allowance: 0.35 * Hs in sheltered water, 0.50 * Hs in exposed coastal water."""
    hs = 2.0
    sheltered = calculate_wave_response(significant_wave_height_m=hs, exposed_waters=False)
    exposed = calculate_wave_response(significant_wave_height_m=hs, exposed_waters=True)

    assert pytest.approx(sheltered, abs=1e-3) == 0.70
    assert pytest.approx(exposed, abs=1e-3) == 1.00
    assert exposed > sheltered

    # Calm sea gives 0.0 allowance
    assert calculate_wave_response(0.0) == 0.0


# ==============================================================================
# 4. Harmonic Tidal Window Feasibility Model Validation
# ==============================================================================

def test_tidal_window_sufficient_depth():
    """When charted depth already meets required safety depth, window is 100% open."""
    res = calculate_tidal_window(min_chart_depth_m=12.0, required_safe_depth_m=10.0, tide_amplitude_m=0.8)
    assert res["is_feasible_anytime"] is True
    assert res["required_tide_elevation_m"] == 0.0


def test_tidal_window_impossible_deficit():
    """When deficit exceeds maximum high water tide amplitude, window is closed."""
    res = calculate_tidal_window(min_chart_depth_m=8.0, required_safe_depth_m=10.0, tide_amplitude_m=1.0)
    assert res["is_feasible_anytime"] is False
    assert res["window_duration_hours"] == 0.0
    assert res["required_tide_elevation_m"] == 2.0


def test_tidal_window_partial_feasibility():
    """When deficit is within tidal amplitude, window is symmetric around HW."""
    amp = 1.0
    period = 12.0
    # Deficit = 0.5 m -> cos(theta) = 0.5 -> theta = pi/3 (60 deg) -> window = (2/3) * (12/2) = 4.0 hours
    res = calculate_tidal_window(
        min_chart_depth_m=9.5,
        required_safe_depth_m=10.0,
        tide_amplitude_m=amp,
        tide_period_hours=period,
    )
    assert res["is_feasible_anytime"] is False
    assert pytest.approx(res["window_duration_hours"], abs=0.1) == 4.0
    assert pytest.approx(res["window_before_hw_hours"], abs=0.05) == 2.0
    assert pytest.approx(res["window_after_hw_hours"], abs=0.05) == 2.0


# ==============================================================================
# 5. Search Budget Invariants & Parity
# ==============================================================================

def test_search_budget_tide_monotonicity():
    """Positive tide strictly reduces required chart depth; negative tide strictly increases it."""
    b_neutral = compute_search_budget(draft_m=3.0, speed_knots=10.0, ukc_net_margin_m=1.0, tide_offset_m=0.0)
    b_high_tide = compute_search_budget(draft_m=3.0, speed_knots=10.0, ukc_net_margin_m=1.0, tide_offset_m=0.5)
    b_low_tide = compute_search_budget(draft_m=3.0, speed_knots=10.0, ukc_net_margin_m=1.0, tide_offset_m=-0.5)

    assert b_high_tide["min_chart_depth_m"] < b_neutral["min_chart_depth_m"]
    assert b_low_tide["min_chart_depth_m"] > b_neutral["min_chart_depth_m"]
    assert pytest.approx(b_neutral["min_chart_depth_m"] - b_high_tide["min_chart_depth_m"], abs=1e-3) == 0.5
    assert pytest.approx(b_low_tide["min_chart_depth_m"] - b_neutral["min_chart_depth_m"], abs=1e-3) == 0.5


def test_strict_dukc_encompasses_all_components():
    """Strict DUKC search budget strictly accounts for waves and turning heel."""
    b_strict = compute_search_budget(
        draft_m=4.0, speed_knots=12.0, ukc_net_margin_m=1.0,
        wave_height_m=1.5, turning_radius_m=200.0, strict_dukc=True
    )
    b_legacy = compute_search_budget(
        draft_m=4.0, speed_knots=12.0, ukc_net_margin_m=1.0,
        wave_height_m=1.5, turning_radius_m=200.0, strict_dukc=False
    )

    assert b_strict["min_chart_depth_m"] > b_legacy["min_chart_depth_m"]
    assert b_strict["wave_allowance_m"] > 0.0
    assert b_strict["heel_sinkage_m"] > 0.0
    assert b_legacy["wave_allowance_m"] == 0.0
    assert b_legacy["heel_sinkage_m"] == 0.0


# ==============================================================================
# 6. Turning Radius Restriction Alarm (ALM-05) Verification
# ==============================================================================

def test_safety_check_turn_radius_restriction_alarm():
    """When waypoint effective turn radius is constrained by short leg lengths, ALM-05 is emitted."""
    waypoints = [
        {"lat": 44.1, "lon": 28.6, "rot_deg_min": 10.0, "turn_angle_deg": 0.0, "turn_radius_m": 250.0},
        {"lat": 44.12, "lon": 28.62, "rot_deg_min": 25.0, "turn_angle_deg": 45.0, "turn_radius_m": 80.0}, # clamped: 80 < 250 * 0.85
        {"lat": 44.13, "lon": 28.65, "rot_deg_min": 10.0, "turn_angle_deg": 0.0, "turn_radius_m": 250.0},
    ]
    profile = [{"distance_from_start_m": 0.0, "depth": 10.0, "clearance": 3.0}]
    fairways = {"type": "FeatureCollection", "features": []}

    res = verify_ecdis_route_safety(
        waypoints=waypoints,
        profile=profile,
        fairways_coll=fairways,
        draft=3.0,
        dynamic_squat=0.5,
        ukc=1.0,
        speed_knots=10.0,
        requested_radius_m=250.0,
    )

    alarm_codes = [a["code"] for a in res["alarms"]]
    assert "ALM-05-RADIUS-RESTRICTION" in alarm_codes
    alm = next(a for a in res["alarms"] if a["code"] == "ALM-05-RADIUS-RESTRICTION")
    assert alm["waypoint_index"] == 2
    assert "80m" in alm["detail"]
    assert "250m" in alm["detail"]
