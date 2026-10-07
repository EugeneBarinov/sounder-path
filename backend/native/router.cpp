#include "router.hpp"
#include <algorithm>
#include <cmath>

struct PQNode {
    float f;
    float g;
    int32_t r;
    int32_t c;

    bool operator>(const PQNode& other) const {
        return f > other.f;
    }
};

NativeRouter::NativeRouter(std::shared_ptr<NativeGrid> g)
    : grid(g), current_run(1) {
    size_t total = static_cast<size_t>(grid->rows) * static_cast<size_t>(grid->cols);
    g_cost.resize(total, std::numeric_limits<float>::infinity());
    came_from.resize(total, -1);
    run_id.resize(total, 0);
}

std::vector<std::pair<int, int>> NativeRouter::string_pull(
    const std::vector<std::pair<int, int>>& path,
    float min_depth,
    int horizon
) const {
    if (path.size() <= 2) {
        return path;
    }

    std::vector<std::pair<int, int>> result;
    result.push_back(path[0]);
    size_t anchor = 0;

    while (anchor < path.size() - 1) {
        size_t furthest = anchor + 1;
        size_t max_scan = std::min(path.size(), anchor + static_cast<size_t>(horizon));

        for (size_t i = anchor + 2; i < max_scan; ++i) {
            if (grid->line_of_sight(path[anchor].first, path[anchor].second,
                                    path[i].first, path[i].second,
                                    min_depth)) {
                furthest = i;
            } else {
                break;
            }
        }
        result.push_back(path[furthest]);
        anchor = furthest;
    }
    return result;
}

NativeRouteResult NativeRouter::find_path(
    int start_r, int start_c,
    int goal_r, int goal_c,
    double draft, double speed_knots, double ukc,
    double turning_radius_m,
    double fairway_preference,
    double block_coefficient
) const {
    NativeRouteResult result;
    result.success = false;

    double dynamic_squat = calculate_squat(speed_knots, block_coefficient);
    double dynamic_draft = draft + dynamic_squat;
    float min_depth = static_cast<float>(dynamic_draft + ukc);

    int rows = grid->rows;
    int cols = grid->cols;

    // Increment run_id to avoid O(N) memset on every search
    ++current_run;
    if (current_run == 0) {
        std::fill(run_id.begin(), run_id.end(), 0);
        current_run = 1;
    }

    std::priority_queue<PQNode, std::vector<PQNode>, std::greater<PQNode>> open_heap;

    size_t start_idx = static_cast<size_t>(start_r) * cols + start_c;
    g_cost[start_idx] = 0.0f;
    came_from[start_idx] = -1;
    run_id[start_idx] = current_run;

    open_heap.push({0.0f, 0.0f, start_r, start_c});

    static const struct { int dr; int dc; float cost; } DIRS[8] = {
        {-1,  0, 1.0f},
        { 1,  0, 1.0f},
        { 0,  1, 1.0f},
        { 0, -1, 1.0f},
        {-1,  1, SQRT2},
        {-1, -1, SQRT2},
        { 1,  1, SQRT2},
        { 1, -1, SQRT2}
    };

    bool reached_goal = false;
    size_t goal_idx = static_cast<size_t>(goal_r) * cols + goal_c;

    while (!open_heap.empty()) {
        PQNode curr = open_heap.top();
        open_heap.pop();

        size_t curr_idx = static_cast<size_t>(curr.r) * cols + curr.c;

        if (curr.g > g_cost[curr_idx]) {
            continue;
        }

        if (curr.r == goal_r && curr.c == goal_c) {
            reached_goal = true;
            break;
        }

        // Kinematic vector of incoming motion
        int32_t prev_idx = came_from[curr_idx];
        int in_dr = 0, in_dc = 0;
        bool has_prev = (prev_idx != -1);
        if (has_prev) {
            in_dr = curr.r - (prev_idx / cols);
            in_dc = curr.c - (prev_idx % cols);
        }

        for (int i = 0; i < 8; ++i) {
            int nr = curr.r + DIRS[i].dr;
            int nc = curr.c + DIRS[i].dc;

            if (nr < 0 || nr >= rows || nc < 0 || nc >= cols) {
                continue;
            }

            // Diagonal corner check: prevent cutting diagonally past obstacle corners
            if (DIRS[i].dr != 0 && DIRS[i].dc != 0) {
                if (grid->is_restricted(curr.r + DIRS[i].dr, curr.c) || grid->is_restricted(curr.r, curr.c + DIRS[i].dc)) {
                    continue;
                }
            }

            size_t n_idx = static_cast<size_t>(nr) * cols + nc;
            float depth = grid->depths[n_idx];

            if (depth <= min_depth) {
                continue;
            }

            float fairway_w = grid->fairway_weight(nr, nc);
            if (fairway_w >= 500.0f) {
                continue; // Navigational danger / restricted area (RESARE)
            }
            if (fairway_preference < 1.0 && fairway_w < 1.0f) {
                fairway_w = 1.0f - static_cast<float>(fairway_preference) * (1.0f - fairway_w);
            }

            float clearance = std::max(0.05f, depth - min_depth);
            float shallow_penalty = 8.0f / clearance;
            float deep_penalty = (depth > 150.0f) ? ((depth - 150.0f) / 100.0f) * 2.0f : 0.0f;

            // Heading Alteration Penalty (Ship Kinematics)
            float heading_penalty = 0.0f;
            if (has_prev) {
                int out_dr = DIRS[i].dr;
                int out_dc = DIRS[i].dc;
                float dot = static_cast<float>(in_dr * out_dr + in_dc * out_dc);
                float mag1 = std::hypot(static_cast<float>(in_dr), static_cast<float>(in_dc));
                float mag2 = DIRS[i].cost;
                float cos_theta = dot / (mag1 * mag2);

                if (cos_theta < 0.99f) {
                    if (cos_theta >= 0.0f) {
                        heading_penalty = 0.35f * (1.0f - cos_theta); // Gentle curve
                    } else {
                        heading_penalty = 1.8f + 1.2f * (-cos_theta); // Sharp turn penalty
                    }
                }
            }

            float step_cost = (DIRS[i].cost * (1.0f + shallow_penalty + deep_penalty) * fairway_w) + heading_penalty;
            float g_new = curr.g + step_cost;

            if (run_id[n_idx] != current_run || g_new < g_cost[n_idx]) {
                run_id[n_idx] = current_run;
                g_cost[n_idx] = g_new;
                came_from[n_idx] = static_cast<int32_t>(curr_idx);

                float h = std::hypot(static_cast<float>(nr - goal_r), static_cast<float>(nc - goal_c));
                open_heap.push({g_new + h, g_new, nr, nc});
            }
        }
    }

    if (!reached_goal) {
        return result;
    }

    // 1. Reconstruct raw discrete path
    std::vector<std::pair<int, int>> raw_path;
    int32_t curr_pos = static_cast<int32_t>(goal_idx);

    while (curr_pos != -1) {
        int r = curr_pos / cols;
        int c = curr_pos % cols;
        raw_path.push_back({r, c});
        curr_pos = came_from[curr_pos];
    }
    std::reverse(raw_path.begin(), raw_path.end());

    // 2. Horizon-bounded string pulling
    std::vector<std::pair<int, int>> macro_wps = string_pull(raw_path, min_depth, 30);
    size_t num_wps = macro_wps.size();

    // 3. Build structured navigational waypoints table (Passage Plan)
    std::vector<std::pair<double, double>> wp_coords; // (lat, lon)
    for (size_t i = 0; i < num_wps; ++i) {
        double lat, lon;
        grid->get_cell_coords(macro_wps[i].first, macro_wps[i].second, lat, lon);
        wp_coords.push_back({lat, lon});
    }

    for (size_t i = 0; i < num_wps; ++i) {
        NavWaypoint nav_wp;
        nav_wp.index = static_cast<int>(i + 1);
        nav_wp.lat = wp_coords[i].first;
        nav_wp.lon = wp_coords[i].second;

        float wp_d = grid->depth(macro_wps[i].first, macro_wps[i].second);
        nav_wp.depth_at_waypoint = std::round(wp_d * 10.0) / 10.0;
        nav_wp.clearance_m = std::round((wp_d - dynamic_draft - ukc) * 100.0) / 100.0;
        nav_wp.turn_radius_m = turning_radius_m;

        if (i < num_wps - 1) {
            nav_wp.leg_bearing_deg = std::round(calculate_bearing_deg(
                wp_coords[i].first, wp_coords[i].second,
                wp_coords[i + 1].first, wp_coords[i + 1].second
            ) * 10.0) / 10.0;

            double leg_dist_m = haversine_distance_m(
                wp_coords[i].first, wp_coords[i].second,
                wp_coords[i + 1].first, wp_coords[i + 1].second
            );
            nav_wp.leg_distance_nm = std::round((leg_dist_m / 1852.0) * 100.0) / 100.0;
        } else {
            nav_wp.leg_bearing_deg = 0.0;
            nav_wp.leg_distance_nm = 0.0;
        }

        if (i > 0 && i < num_wps - 1) {
            double prev_bearing = calculate_bearing_deg(
                wp_coords[i - 1].first, wp_coords[i - 1].second,
                wp_coords[i].first, wp_coords[i].second
            );
            double next_bearing = nav_wp.leg_bearing_deg;
            double diff = next_bearing - prev_bearing;
            while (diff > 180.0) diff -= 360.0;
            while (diff < -180.0) diff += 360.0;
            nav_wp.turn_angle_deg = std::round(diff * 10.0) / 10.0;

            double abs_turn = std::abs(diff);
            if (abs_turn >= 0.5 && turning_radius_m > 10.0 && speed_knots > 0.0) {
                // Rate of Turn (ROT in deg/min): omega = V / R -> ROT = (5556 / pi) * (V_kts / R_m)
                double rot = (5556.0 / 3.14159265358979323846) * (speed_knots / turning_radius_m);
                nav_wp.rot_deg_min = std::round((diff >= 0.0 ? rot : -rot) * 10.0) / 10.0;

                // Wheel Over Point (WOP in meters): R * tan(|theta| / 2) + V_ms * t_delay (12s lag)
                double rad_half = (abs_turn * 3.14159265358979323846) / 360.0;
                double v_ms = speed_knots * 0.514444;
                double wop_m = turning_radius_m * std::tan(rad_half) + v_ms * 12.0;
                nav_wp.wop_distance_m = std::round(wop_m * 10.0) / 10.0;
            } else {
                nav_wp.rot_deg_min = 0.0;
                nav_wp.wop_distance_m = 0.0;
            }
        } else {
            nav_wp.turn_angle_deg = 0.0;
            nav_wp.rot_deg_min = 0.0;
            nav_wp.wop_distance_m = 0.0;
        }

        result.waypoints.push_back(nav_wp);
    }

    // 4. Generate smooth kinematically-filleted track with circular turning arcs
    result.success = true;
    double total_distance_m = 0.0;
    double min_clearance = std::numeric_limits<double>::infinity();

    for (size_t i = 0; i < num_wps; ++i) {
        double lat_i = wp_coords[i].first;
        double lon_i = wp_coords[i].second;

        // Check if a circular turning arc should fillet this vertex
        if (i > 0 && i < num_wps - 1 && std::abs(result.waypoints[i].turn_angle_deg) >= 4.0 && turning_radius_m > 10.0) {
            double lat_prev = wp_coords[i - 1].first;
            double lon_prev = wp_coords[i - 1].second;
            double lat_next = wp_coords[i + 1].first;
            double lon_next = wp_coords[i + 1].second;

            // Local Cartesian tangent projection around waypoint i (in meters)
            double cos_lat = std::cos(lat_i * DEG_TO_RAD);
            double m_per_deg_lat = 111139.0;
            double m_per_deg_lon = 111139.0 * cos_lat;

            double v1_x = (lon_prev - lon_i) * m_per_deg_lon;
            double v1_y = (lat_prev - lat_i) * m_per_deg_lat;
            double v2_x = (lon_next - lon_i) * m_per_deg_lon;
            double v2_y = (lat_next - lat_i) * m_per_deg_lat;

            double len1 = std::hypot(v1_x, v1_y);
            double len2 = std::hypot(v2_x, v2_y);

            double u1_x = v1_x / len1, u1_y = v1_y / len1;
            double u2_x = v2_x / len2, u2_y = v2_y / len2;

            double dot = u1_x * u2_x + u1_y * u2_y;
            dot = std::max(-1.0, std::min(1.0, dot));
            double gamma = std::acos(dot); // Interior angle
            double beta = 3.14159265358979323846 - gamma; // Turn deflection angle

            double tan_half = std::tan(beta / 2.0);
            if (tan_half > 0.001) {
                double tangent_dist = turning_radius_m * tan_half;
                double max_t_dist = std::min(len1 * 0.40, len2 * 0.40);
                double safe_t_dist = std::min(tangent_dist, max_t_dist);
                double effective_r = safe_t_dist / tan_half;

                // Entry and exit tangent points
                double t1_x = u1_x * safe_t_dist, t1_y = u1_y * safe_t_dist;
                double t2_x = u2_x * safe_t_dist, t2_y = u2_y * safe_t_dist;

                // Arc center
                double bisect_x = u1_x + u2_x, bisect_y = u1_y + u2_y;
                double bisect_len = std::hypot(bisect_x, bisect_y);
                if (bisect_len > 0.001) {
                    double bx = bisect_x / bisect_len;
                    double by = bisect_y / bisect_len;
                    double center_dist = effective_r / std::sin(gamma / 2.0);
                    double cx = bx * center_dist;
                    double cy = by * center_dist;

                    // Interpolate 6 points along the turning arc
                    std::vector<std::pair<double, double>> arc_geo_points;
                    bool arc_safe = true;

                    for (int step = 0; step <= 6; ++step) {
                        double t = static_cast<double>(step) / 6.0;
                        // Linear blend from T1 to T2 projected from Center C
                        double chord_x = t1_x + (t2_x - t1_x) * t;
                        double chord_y = t1_y + (t2_y - t1_y) * t;
                        double rad_x = chord_x - cx;
                        double rad_y = chord_y - cy;
                        double rad_len = std::hypot(rad_x, rad_y);

                        double px = cx + (rad_x / rad_len) * effective_r;
                        double py = cy + (rad_y / rad_len) * effective_r;

                        double pt_lat = lat_i + py / m_per_deg_lat;
                        double pt_lon = lon_i + px / m_per_deg_lon;

                        // Check bathymetric and navigational safety of arc point
                        int cell_r = static_cast<int>((grid->lat_max - pt_lat) / ((grid->lat_max - grid->lat_min) / rows));
                        int cell_c = static_cast<int>((pt_lon - grid->lon_min) / ((grid->lon_max - grid->lon_min) / cols));
                        if (cell_r >= 0 && cell_r < rows && cell_c >= 0 && cell_c < cols) {
                            if (grid->depths[cell_r * cols + cell_c] <= min_depth || grid->is_restricted(cell_r, cell_c)) {
                                arc_safe = false;
                                break;
                            }
                        }
                        arc_geo_points.push_back({pt_lon, pt_lat});
                    }

                    if (arc_safe) {
                        for (const auto& ap : arc_geo_points) {
                            result.route_coordinates.push_back(ap);
                        }
                        continue;
                    }
                }
            }
        }

        // Default: add apex waypoint
        result.route_coordinates.push_back({lon_i, lat_i});
    }

    // 5. Build continuous depth profile along final smooth route
    for (size_t seg = 0; seg < result.route_coordinates.size(); ++seg) {
        double lon0 = result.route_coordinates[seg].first;
        double lat0 = result.route_coordinates[seg].second;

        if (seg < result.route_coordinates.size() - 1) {
            double lon1 = result.route_coordinates[seg + 1].first;
            double lat1 = result.route_coordinates[seg + 1].second;
            double seg_len_m = haversine_distance_m(lat0, lon0, lat1, lon1);

            int r0 = static_cast<int>((grid->lat_max - lat0) / ((grid->lat_max - grid->lat_min) / rows));
            int c0 = static_cast<int>((lon0 - grid->lon_min) / ((grid->lon_max - grid->lon_min) / cols));
            int r1 = static_cast<int>((grid->lat_max - lat1) / ((grid->lat_max - grid->lat_min) / rows));
            int c1 = static_cast<int>((lon1 - grid->lon_min) / ((grid->lon_max - grid->lon_min) / cols));

            r0 = std::max(0, std::min(rows - 1, r0));
            c0 = std::max(0, std::min(cols - 1, c0));
            r1 = std::max(0, std::min(rows - 1, r1));
            c1 = std::max(0, std::min(cols - 1, c1));

            std::vector<float> depths_along = grid->sample_depth_along_segment(r0, c0, r1, c1);
            size_t n_samples = depths_along.size();

            for (size_t i = 0; i < n_samples; ++i) {
                double progress = (n_samples > 1) ? static_cast<double>(i) / (n_samples - 1) : 0.0;
                double sample_dist = total_distance_m + seg_len_m * progress;
                double d = depths_along[i];
                double clearance = d - dynamic_draft - ukc;
                if (clearance < min_clearance) {
                    min_clearance = clearance;
                }

                result.profile.push_back({
                    std::round(sample_dist * 10.0) / 10.0,
                    std::round(d * 100.0) / 100.0,
                    std::round(clearance * 100.0) / 100.0
                });
            }
            total_distance_m += seg_len_m;
        } else {
            int r_last = static_cast<int>((grid->lat_max - lat0) / ((grid->lat_max - grid->lat_min) / rows));
            int c_last = static_cast<int>((lon0 - grid->lon_min) / ((grid->lon_max - grid->lon_min) / cols));
            r_last = std::max(0, std::min(rows - 1, r_last));
            c_last = std::max(0, std::min(cols - 1, c_last));

            double terminal_depth = grid->depth(r_last, c_last);
            double clearance = terminal_depth - dynamic_draft - ukc;
            if (clearance < min_clearance) {
                min_clearance = clearance;
            }

            result.profile.push_back({
                std::round(total_distance_m * 10.0) / 10.0,
                std::round(terminal_depth * 100.0) / 100.0,
                std::round(clearance * 100.0) / 100.0
            });
        }
    }

    double distance_nm = total_distance_m / 1852.0;

    result.diagnostics.distance_m = std::round(total_distance_m);
    result.diagnostics.distance_nm = std::round(distance_nm * 100.0) / 100.0;
    result.diagnostics.min_clearance_m = std::round(min_clearance * 100.0) / 100.0;
    result.diagnostics.dynamic_draft_m = std::round(dynamic_draft * 100.0) / 100.0;
    result.diagnostics.waypoints = static_cast<int>(result.waypoints.size());

    if (speed_knots > 0.0) {
        result.diagnostics.eta_hours = std::round((distance_nm / speed_knots) * 100.0) / 100.0;
        result.diagnostics.has_eta = true;
    } else {
        result.diagnostics.eta_hours = 0.0;
        result.diagnostics.has_eta = false;
    }

    return result;
}
