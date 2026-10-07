#pragma once

#include <vector>
#include <cmath>
#include <algorithm>
#include <cstdint>

class NativeGrid {
public:
    int rows;
    int cols;
    double lat_min;
    double lat_max;
    double lon_min;
    double lon_max;
    std::vector<float> depths;

    NativeGrid(int r, int c, double lat0, double lat1, double lon0, double lon1, const float* data)
        : rows(r), cols(c), lat_min(lat0), lat_max(lat1), lon_min(lon0), lon_max(lon1), depths(data, data + (r * c)) {}

    inline float depth(int r, int c) const {
        if (r < 0 || r >= rows || c < 0 || c >= cols) return 0.0f;
        return depths[r * cols + c];
    }

    inline bool line_of_sight(int r0, int c0, int r1, int c1, float min_depth) const {
        int dr = std::abs(r1 - r0);
        int dc = std::abs(c1 - c0);
        int sr = (r0 < r1) ? 1 : -1;
        int sc = (c0 < c1) ? 1 : -1;
        int err = dr - dc;
        int r = r0, c = c0;

        while (true) {
            if (r < 0 || r >= rows || c < 0 || c >= cols) return false;
            if (depths[r * cols + c] <= min_depth) return false;
            if (r == r1 && c == c1) return true;

            int e2 = 2 * err;
            if (e2 > -dc) {
                err -= dc;
                r += sr;
            }
            if (e2 < dr) {
                err += dr;
                c += sc;
            }
        }
    }

    inline std::vector<float> sample_depth_along_segment(int r0, int c0, int r1, int c1) const {
        std::vector<float> readings;
        int dr = std::abs(r1 - r0);
        int dc = std::abs(c1 - c0);
        int sr = (r0 < r1) ? 1 : -1;
        int sc = (c0 < c1) ? 1 : -1;
        int err = dr - dc;
        int r = r0, c = c0;

        while (true) {
            if (r >= 0 && r < rows && c >= 0 && c < cols) {
                readings.push_back(depths[r * cols + c]);
            } else {
                readings.push_back(0.0f);
            }
            if (r == r1 && c == c1) break;

            int e2 = 2 * err;
            if (e2 > -dc) {
                err -= dc;
                r += sr;
            }
            if (e2 < dr) {
                err += dr;
                c += sc;
            }
        }
        return readings;
    }

    inline void get_cell_coords(int r, int c, double& lat, double& lon) const {
        double lat_step = (lat_max - lat_min) / rows;
        double lon_step = (lon_max - lon_min) / cols;
        lat = lat_max - (r + 0.5) * lat_step;
        lon = lon_min + (c + 0.5) * lon_step;
    }
};
