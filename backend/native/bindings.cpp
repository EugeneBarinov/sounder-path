#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include "grid.hpp"
#include "router.hpp"

namespace py = pybind11;

PYBIND11_MODULE(seapath_native, m) {
    m.doc() = "SeaPath C++ native routing acceleration module";

    py::class_<NativeGrid, std::shared_ptr<NativeGrid>>(m, "NativeGrid")
        .def(py::init([](int r, int c, double lat0, double lat1, double lon0, double lon1,
                         py::array_t<float, py::array::c_style | py::array::forcecast> array,
                         py::object py_fw) {
            py::buffer_info info = array.request();
            if (info.ndim != 2 || info.shape[0] != r || info.shape[1] != c) {
                throw std::runtime_error("Array dimensions must match specified rows and cols");
            }
            const float* fw_ptr = nullptr;
            py::array_t<float, py::array::c_style | py::array::forcecast> fw_arr;
            if (!py_fw.is_none()) {
                fw_arr = py_fw.cast<py::array_t<float, py::array::c_style | py::array::forcecast>>();
                py::buffer_info fw_info = fw_arr.request();
                if (fw_info.ndim == 2 && fw_info.shape[0] == r && fw_info.shape[1] == c) {
                    fw_ptr = static_cast<const float*>(fw_info.ptr);
                }
            }
            return std::make_shared<NativeGrid>(r, c, lat0, lat1, lon0, lon1, static_cast<const float*>(info.ptr), fw_ptr);
        }),
        py::arg("rows"), py::arg("cols"),
        py::arg("lat_min"), py::arg("lat_max"),
        py::arg("lon_min"), py::arg("lon_max"),
        py::arg("depths"),
        py::arg("fairway_weights") = py::none())
        .def_readonly("rows", &NativeGrid::rows)
        .def_readonly("cols", &NativeGrid::cols);

    py::class_<NativeRouter>(m, "NativeRouter")
        .def(py::init<std::shared_ptr<NativeGrid>>())
        .def_static("calculate_squat", &NativeRouter::calculate_squat, py::arg("speed_knots"), py::arg("block_coefficient") = 0.6)
        .def("find_path", [](const NativeRouter& self, int start_r, int start_c, int goal_r, int goal_c,
                             double draft, double speed_knots, double ukc, double turning_radius_m,
                             double fairway_preference) -> py::object {
            NativeRouteResult res = self.find_path(start_r, start_c, goal_r, goal_c, draft, speed_knots, ukc, turning_radius_m, fairway_preference);
            if (!res.success) {
                return py::none();
            }

            py::dict py_res;

            // 1. Detailed smoothed geometric coordinates
            py::list py_route;
            for (const auto& pt : res.route_coordinates) {
                py::list coord;
                coord.append(pt.first);
                coord.append(pt.second);
                py_route.append(coord);
            }
            py_res["route"] = py_route;

            // 2. Structured ECDIS Waypoints Table (Passage Plan)
            py::list py_wps;
            for (const auto& wp : res.waypoints) {
                py::dict w_dict;
                w_dict["index"] = wp.index;
                w_dict["lat"] = wp.lat;
                w_dict["lon"] = wp.lon;
                w_dict["leg_bearing_deg"] = wp.leg_bearing_deg;
                w_dict["leg_distance_nm"] = wp.leg_distance_nm;
                w_dict["turn_angle_deg"] = wp.turn_angle_deg;
                w_dict["turn_radius_m"] = wp.turn_radius_m;
                w_dict["depth_m"] = wp.depth_at_waypoint;
                w_dict["clearance_m"] = wp.clearance_m;
                py_wps.append(w_dict);
            }
            py_res["waypoints"] = py_wps;

            // 3. Continuous Depth Soundings Profile
            py::list py_profile;
            for (const auto& pt : res.profile) {
                py::dict p_dict;
                p_dict["distance_from_start_m"] = pt.distance_m;
                p_dict["depth"] = pt.depth;
                p_dict["clearance"] = pt.clearance;
                py_profile.append(p_dict);
            }
            py_res["profile"] = py_profile;

            // 4. Passage Diagnostics
            py::dict py_diag;
            py_diag["distance_m"] = res.diagnostics.distance_m;
            py_diag["distance_nm"] = res.diagnostics.distance_nm;
            py_diag["min_clearance_m"] = res.diagnostics.min_clearance_m;
            py_diag["dynamic_draft_m"] = res.diagnostics.dynamic_draft_m;
            py_diag["waypoints"] = res.diagnostics.waypoints;
            if (res.diagnostics.has_eta) {
                py_diag["eta_hours"] = res.diagnostics.eta_hours;
            } else {
                py_diag["eta_hours"] = py::none();
            }
            py_res["diagnostics"] = py_diag;

            return py_res;
        },
        py::arg("start_r"), py::arg("start_c"),
        py::arg("goal_r"), py::arg("goal_c"),
        py::arg("draft"), py::arg("speed_knots"), py::arg("ukc"),
        py::arg("turning_radius_m") = 150.0,
        py::arg("fairway_preference") = 1.0);
}
