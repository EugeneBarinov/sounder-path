"""
Maritime Passage Planning Router
================================
Implements bathymetric pathfinding using constrained A* search, dynamic squat
hydrodynamics (Barrass formulation), Under Keel Clearance (UKC) verification,
and horizon-bounded line-of-sight path pruning (Theta*-style string pulling).
"""

import math
import heapq
from typing import List, Tuple, Dict, Any, Optional
from backend.core.grid import BathymetricGrid

_SQRT2 = math.sqrt(2.0)

# 8-connected grid offsets: (delta_row, delta_col, euclidean_distance_weight)
_DIRECTIONS = [
    (-1,  0, 1.0),
    ( 1,  0, 1.0),
    ( 0,  1, 1.0),
    ( 0, -1, 1.0),
    (-1,  1, _SQRT2),
    (-1, -1, _SQRT2),
    ( 1,  1, _SQRT2),
    ( 1, -1, _SQRT2),
]


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two geographic coordinates in meters."""
    r = 6371000.0  # Earth mean radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def _heuristic(r1: int, c1: int, r2: int, c2: int) -> float:
    """Euclidean distance heuristic for uniform grid cells."""
    return math.hypot(r1 - r2, c1 - c2)


class Router:
    """
    Bathymetric passage planning engine.
    Ensures safe navigation through dynamic under-keel clearance monitoring.
    """

    def __init__(self, grid: BathymetricGrid):
        self.grid = grid

    @staticmethod
    def calculate_squat(speed_knots: float, block_coefficient: float = 0.6) -> float:
        """
        Barrass open-water squat formulation.
        Computes dynamic ship sinkage in meters based on hull form and operational speed.
        Delta_h = (Cb * V^2) / 100
        """
        return (block_coefficient * (speed_knots ** 2)) / 100.0

    def _line_of_sight(
        self,
        r0: int,
        c0: int,
        r1: int,
        c1: int,
        min_depth: float,
    ) -> bool:
        """
        Bresenham raycasting between two grid nodes.
        Returns True if every intermediate cell satisfies the required minimum depth.
        """
        dr = abs(r1 - r0)
        dc = abs(c1 - c0)
        sr = 1 if r0 < r1 else -1
        sc = 1 if c0 < c1 else -1
        err = dr - dc
        r, c = r0, c0

        while True:
            if not (0 <= r < self.grid.rows and 0 <= c < self.grid.cols):
                return False
            if float(self.grid.depths[r, c]) <= min_depth:
                return False
            if r == r1 and c == c1:
                return True

            e2 = 2 * err
            if e2 > -dc:
                err -= dc
                r += sr
            if e2 < dr:
                err += dr
                c += sc

    def find_path(
        self,
        start_r: int,
        start_c: int,
        goal_r: int,
        goal_c: int,
        draft: float,
        speed_knots: float,
        ukc: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Compute optimal safe passage plan using constrained A* search.
        Evaluates dynamic draft, under-keel clearance, and coastal bathymetry corridors.
        """
        dynamic_draft = draft + self.calculate_squat(speed_knots)
        min_depth = dynamic_draft + ukc

        rows, cols = self.grid.rows, self.grid.cols

        # Priority queue entries: (f_score, g_score, row, col)
        open_heap: List[Tuple[float, float, int, int]] = []
        heapq.heappush(open_heap, (0.0, 0.0, start_r, start_c))

        g_cost: Dict[Tuple[int, int], float] = {(start_r, start_c): 0.0}
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        closed_set = set()

        while open_heap:
            _, g_curr, r, c = heapq.heappop(open_heap)

            if (r, c) in closed_set:
                continue
            closed_set.add((r, c))

            if r == goal_r and c == goal_c:
                return self._build_result(came_from, r, c, dynamic_draft, ukc, min_depth)

            for dr, dc, base_cost in _DIRECTIONS:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                if (nr, nc) in closed_set:
                    continue

                depth = float(self.grid.depths[nr, nc])
                if depth <= min_depth:
                    continue

                clearance = max(0.05, depth - min_depth)
                # Asymptotic safety penalty when approaching minimum safe clearance
                shallow_penalty = 8.0 / clearance
                # Coastal fairway preference: discourages uncontrolled deep-trench routing
                deep_penalty = ((depth - 150.0) / 100.0) * 2.0 if depth > 150.0 else 0.0

                transition_cost = base_cost * (1.0 + shallow_penalty + deep_penalty)
                g_new = g_curr + transition_cost

                if g_new < g_cost.get((nr, nc), math.inf):
                    g_cost[(nr, nc)] = g_new
                    came_from[(nr, nc)] = (r, c)
                    f_score = g_new + _heuristic(nr, nc, goal_r, goal_c)
                    heapq.heappush(open_heap, (f_score, g_new, nr, nc))

        return None

    def _string_pull(
        self,
        path: List[Tuple[int, int]],
        min_depth: float,
        horizon_cells: int = 30,
    ) -> List[Tuple[int, int]]:
        """
        Raycast-based waypoint reduction with bounded horizon lookahead.
        Preserves macro-scale fairway topology while eliminating discrete grid artifacts.
        """
        if len(path) <= 2:
            return path

        result = [path[0]]
        anchor = 0

        while anchor < len(path) - 1:
            furthest = anchor + 1
            max_scan = min(len(path), anchor + horizon_cells)

            for i in range(anchor + 2, max_scan):
                if self._line_of_sight(path[anchor][0], path[anchor][1], path[i][0], path[i][1], min_depth):
                    furthest = i
                else:
                    break

            result.append(path[furthest])
            anchor = furthest

        return result

    def _build_result(
        self,
        came_from: Dict[Tuple[int, int], Tuple[int, int]],
        goal_r: int,
        goal_c: int,
        dynamic_draft: float,
        ukc: float,
        min_depth: float,
    ) -> Dict[str, Any]:
        """Reconstruct waypoints, compute geodetic distances, and build depth soundings profile."""
        raw_path: List[Tuple[int, int]] = []
        curr = (goal_r, goal_c)
        while curr in came_from:
            raw_path.append(curr)
            curr = came_from[curr]
        raw_path.append(curr)
        raw_path.reverse()

        path = self._string_pull(raw_path, min_depth)

        coordinates: List[List[float]] = []
        profile: List[Dict[str, Any]] = []
        min_clearance = math.inf
        total_distance_m = 0.0

        for seg_idx in range(len(path)):
            r0, c0 = path[seg_idx]
            lat0, lon0 = self.grid.get_cell_coords(r0, c0)
            coordinates.append([lon0, lat0])

            if seg_idx < len(path) - 1:
                r1, c1 = path[seg_idx + 1]
                lat1, lon1 = self.grid.get_cell_coords(r1, c1)
                seg_len_m = haversine_distance_m(lat0, lon0, lat1, lon1)

                depths_along = self.grid.sample_depth_along_segment(r0, c0, r1, c1)
                n_samples = len(depths_along)

                for i, d in enumerate(depths_along):
                    sample_dist = total_distance_m + seg_len_m * (i / max(1, n_samples - 1))
                    clearance = d - dynamic_draft - ukc
                    if clearance < min_clearance:
                        min_clearance = clearance

                    profile.append({
                        "distance_from_start_m": round(sample_dist, 1),
                        "depth": round(d, 2),
                        "clearance": round(clearance, 2),
                    })

                total_distance_m += seg_len_m
            else:
                terminal_depth = float(self.grid.depths[r0, c0])
                clearance = terminal_depth - dynamic_draft - ukc
                if clearance < min_clearance:
                    min_clearance = clearance

                profile.append({
                    "distance_from_start_m": round(total_distance_m, 1),
                    "depth": round(terminal_depth, 2),
                    "clearance": round(clearance, 2),
                })

        distance_nm = total_distance_m / 1852.0

        return {
            "route": coordinates,
            "profile": profile,
            "diagnostics": {
                "distance_m": round(total_distance_m, 0),
                "distance_nm": round(distance_nm, 2),
                "min_clearance_m": round(min_clearance, 2),
                "dynamic_draft_m": round(dynamic_draft, 2),
                "waypoints": len(path),
            },
        }
