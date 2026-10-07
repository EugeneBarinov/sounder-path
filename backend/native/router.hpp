#pragma once

#include "grid.hpp"
#include <vector>
#include <queue>
#include <limits>
#include <cmath>
#include <memory>
#include <tuple>

struct RouteProfilePoint {
    double distance_m;
    double depth;
    double clearance;
};

struct NavWaypoint {
    int index;
    double lat;
    double lon;
    double leg_bearing_deg;     // Initial compass bearing to NEXT waypoint (0..360°)
    double leg_distance_nm;     // Distance to next waypoint in Nautical Miles
    double turn_angle_deg;      // Heading alteration angle at this waypoint (-180..+180°)
    double turn_radius_m;       // Turning radius arc applied at this waypoint
    double depth_at_waypoint;   // Seabed depth at waypoint
    double clearance_m;         // Under-keel clearance at waypoint
};

struct RouteDiagnostics {
    double distance_m;
    double distance_nm;
    double min_clearance_m;
    double dynamic_draft_m;
    int waypoints;
    double eta_hours;
    bool has_eta;
};

struct NativeRouteResult {
    bool success;
    std::vector<std::pair<double, double>> route_coordinates; // Detailed smooth track (lon, lat)
    std::vector<NavWaypoint> waypoints;                       // Structured ECDIS Waypoints list
    std::vector<RouteProfilePoint> profile;
    RouteDiagnostics diagnostics;
};

class NativeRouter {
private:
    std::shared_ptr<NativeGrid> grid;
    mutable std::vector<float> g_cost;
    mutable std::vector<int32_t> came_from;
    mutable std::vector<uint32_t> run_id;
    mutable uint32_t current_run;

    static constexpr float SQRT2 = 1.41421356237309504880f;
    static constexpr double EARTH_RADIUS_M = 6371000.0;
    static constexpr double DEG_TO_RAD = 3.14159265358979323846 / 180.0;
    static constexpr double RAD_TO_DEG = 180.0 / 3.14159265358979323846;

    static inline double haversine_distance_m(double lat1, double lon1, double lat2, double lon2) {
        double phi1 = lat1 * DEG_TO_RAD;
        double phi2 = lat2 * DEG_TO_RAD;
        double delta_phi = (lat2 - lat1) * DEG_TO_RAD;
        double delta_lambda = (lon2 - lon1) * DEG_TO_RAD;

        double s_dphi = std::sin(delta_phi / 2.0);
        double s_dlam = std::sin(delta_lambda / 2.0);
        double a = s_dphi * s_dphi + std::cos(phi1) * std::cos(phi2) * s_dlam * s_dlam;
        double c = 2.0 * std::atan2(std::sqrt(a), std::sqrt(1.0 - a));
        return EARTH_RADIUS_M * c;
    }

    static inline double calculate_bearing_deg(double lat1, double lon1, double lat2, double lon2) {
        double phi1 = lat1 * DEG_TO_RAD;
        double phi2 = lat2 * DEG_TO_RAD;
        double delta_lambda = (lon2 - lon1) * DEG_TO_RAD;

        double y = std::sin(delta_lambda) * std::cos(phi2);
        double x = std::cos(phi1) * std::sin(phi2) - std::sin(phi1) * std::cos(phi2) * std::cos(delta_lambda);
        double theta = std::atan2(y, x) * RAD_TO_DEG;
        return std::fmod(theta + 360.0, 360.0);
    }

    std::vector<std::pair<int, int>> string_pull(
        const std::vector<std::pair<int, int>>& path,
        float min_depth,
        int horizon = 30
    ) const;

public:
    NativeRouter(std::shared_ptr<NativeGrid> g);

    static inline double calculate_squat(double speed_knots, double block_coefficient = 0.6) {
        return (block_coefficient * (speed_knots * speed_knots)) / 100.0;
    }

    NativeRouteResult find_path(
        int start_r, int start_c,
        int goal_r, int goal_c,
        double draft, double speed_knots, double ukc,
        double turning_radius_m = 150.0
    ) const;
};
