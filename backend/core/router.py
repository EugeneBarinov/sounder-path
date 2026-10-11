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


def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate initial geodetic bearing (true course) in degrees [0, 360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    brg = math.degrees(math.atan2(y, x))
    return (brg + 360.0) % 360.0


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
    def calculate_squat(speed_knots: float, block_coefficient: float = 0.65, h_over_t: float = 0.0) -> float:
        """
        Barrass / PIANC hydrodynamic squat formulation.
        Computes dynamic ship sinkage in meters based on hull form, operational speed,
        and depth-to-draft confinement ratio (h/T).
        Delta_h = (Cb * V^2) / K, where K=100 (open water) down to K=50 (confined shallow water h/T <= 1.2).
        """
        k = 100.0
        if h_over_t > 0.0:
            if h_over_t <= 1.2:
                k = 50.0
            elif h_over_t < 1.5:
                k = 50.0 + 50.0 * ((h_over_t - 1.2) / 0.3)
        return (block_coefficient * (speed_knots ** 2)) / k

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
            if float(self.grid.depths[r, c]) <= min_depth or self.grid.is_restricted(r, c):
                return False
            if r == r1 and c == c1:
                return True

            int_e2 = 2 * err
            if int_e2 > -dc and int_e2 < dr:
                # Supercover check: prevent diagonal cutting through obstacle corners (shoal or restricted)
                if (
                    self.grid.get_depth(r + sr, c) <= min_depth
                    or self.grid.is_restricted(r + sr, c)
                    or self.grid.get_depth(r, c + sc) <= min_depth
                    or self.grid.is_restricted(r, c + sc)
                ):
                    return False
            if int_e2 > -dc:
                err -= dc
                r += sr
            if int_e2 < dr:
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
        turning_radius_m: float = 150.0,
        fairway_preference: float = 1.0,
        block_coefficient: float = 0.65,
        min_chart_depth_m: Optional[float] = None,
        dynamic_draft_m: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Прокладка безопасного прохода взвешенным A* по сетке батиметрии.

        ЧТО ДЕЛАЕМ ПО ШАГАМ:
        1. Бюджет глубины: если gateway передал готовые min_chart_depth_m /
           dynamic_draft_m (канон dukc.compute_search_budget) — берём их
           как есть и squat внутри НЕ пересчитываем. Иначе legacy-ветка:
           h/T=(draft+ukc)/draft → squat по Баррассу → min_depth.
           Зачем: один источник правды, Python и C++ ядра дают одинаковый
           порог; иначе дрейф формул и «прошёл поиск — упал safety_check».
        2. A*: очередь (f=g+эвристика), стоимость шага =
           base(1.0/√2) * (1 + 8/max(0.05, depth-min_depth) + штраф_глубоководья)
           * вес_фарватера + 0 для Python (heading-штраф только в C++).
           Ячейка проходима ⇔ depth > min_depth И вес < 500 (не RESARE).
           Диагонали через угол запретки режутся отдельно (см. ниже).
        3. Спрямление (string pulling): последовательные узлы сырого пути
           соединяем лучом Брезенхема horizon=30, если весь луч глубже
           min_depth и вне запреток — промежуточные узлы выбрасываем.
        4. Профиль и таблица WP: по спрямлённому пути семплируем глубины
           вдоль каждого сегмента, clearance = depth - dynamic_draft - ukc
           (без прилива! прилив добавляет main.py при склейке плеч).

        Возвращает dict {route, profile, waypoints, diagnostics} или None,
        если прохода с требуемым запасом нет.
        """
        # -- Шаг 1. Порог глубины: канон снаружи или legacy внутри -----------
        # Каноническая ветка: gateway уже посчитал полный PIANC-бюджет
        # (squat+крен+волна+UKC с приливом). Роутер — чистый исполнитель:
        # не гадает, а применяет готовые числа. Это и есть унификация.
        if min_chart_depth_m is not None and dynamic_draft_m is not None:
            min_depth = float(min_chart_depth_m)
            dynamic_draft = float(dynamic_draft_m)
        else:
            # Legacy-ветка: только squat, без крена/волны. Оставлена для
            # прямых вызовов без gateway (тесты, benchmark без бюджета).
            h_over_t = (draft + ukc) / draft if draft > 0.0 else 0.0
            dynamic_draft = draft + self.calculate_squat(speed_knots, block_coefficient, h_over_t)
            min_depth = dynamic_draft + ukc

        rows, cols = self.grid.rows, self.grid.cols
        if not (0 <= start_r < rows and 0 <= start_c < cols and 0 <= goal_r < rows and 0 <= goal_c < cols):
            return None

        if (
            float(self.grid.depths[start_r, start_c]) <= min_depth
            or self.grid.is_restricted(start_r, start_c)
            or float(self.grid.depths[goal_r, goal_c]) <= min_depth
            or self.grid.is_restricted(goal_r, goal_c)
        ):
            return None

        # Priority queue entries: (f_score, g_score, row, col, dir_idx)
        # dir_idx = 8 represents the start state without incoming heading
        open_heap: List[Tuple[float, float, int, int, int]] = []
        start_state = (start_r, start_c, 8)
        heapq.heappush(open_heap, (0.0, 0.0, start_r, start_c, 8))

        g_cost: Dict[Tuple[int, int, int], float] = {start_state: 0.0}
        came_from: Dict[Tuple[int, int, int], Optional[Tuple[int, int, int]]] = {start_state: None}

        while open_heap:
            _, g_curr, r, c, dir_curr = heapq.heappop(open_heap)
            curr_state = (r, c, dir_curr)

            if g_curr > g_cost.get(curr_state, math.inf):
                continue

            if r == goal_r and c == goal_c:
                return self._build_result(
                    came_from, curr_state, dynamic_draft, ukc, min_depth, speed_knots, turning_radius_m
                )

            has_incoming_dir = (dir_curr < 8)
            in_dr, in_dc = (0, 0)
            if has_incoming_dir:
                in_dr, in_dc = _DIRECTIONS[dir_curr][0], _DIRECTIONS[dir_curr][1]

            for i, (dr, dc, base_cost) in enumerate(_DIRECTIONS):
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue

                if dr != 0 and dc != 0:
                    s1_r, s1_c = r + dr, c
                    s2_r, s2_c = r, c + dc
                    if (
                        self.grid.get_depth(s1_r, s1_c) <= min_depth
                        or self.grid.is_restricted(s1_r, s1_c)
                        or self.grid.get_depth(s2_r, s2_c) <= min_depth
                        or self.grid.is_restricted(s2_r, s2_c)
                    ):
                        continue

                depth = float(self.grid.depths[nr, nc])
                if depth <= min_depth:
                    continue

                fw_factor = self.grid.get_fairway_weight(nr, nc)
                if fw_factor >= 500.0:
                    continue  # Restricted / danger area
                if fairway_preference < 1.0 and fw_factor < 1.0:
                    fw_factor = 1.0 - fairway_preference * (1.0 - fw_factor)

                clearance = max(0.05, depth - min_depth)
                # Asymptotic safety penalty when approaching minimum safe clearance
                shallow_penalty = 8.0 / clearance
                # Coastal fairway preference: discourages uncontrolled deep-trench routing
                deep_penalty = ((depth - 150.0) / 100.0) * 2.0 if depth > 150.0 else 0.0

                # Heading Alteration Penalty (Ship Kinematics in 3D State Lattice)
                heading_penalty = 0.0
                if has_incoming_dir:
                    dot = float(in_dr * dr + in_dc * dc)
                    mag1 = _DIRECTIONS[dir_curr][2]
                    mag2 = base_cost
                    cos_theta = dot / (mag1 * mag2)

                    if cos_theta < 0.99:
                        if cos_theta >= 0.0:
                            heading_penalty = 0.35 * (1.0 - cos_theta)
                        else:
                            heading_penalty = 1.8 + 1.2 * (-cos_theta)

                transition_cost = (base_cost * (1.0 + shallow_penalty + deep_penalty) * fw_factor) + heading_penalty
                g_new = g_curr + transition_cost
                next_state = (nr, nc, i)

                if g_new < g_cost.get(next_state, math.inf):
                    g_cost[next_state] = g_new
                    came_from[next_state] = curr_state
                    f_score = g_new + 0.30 * _heuristic(nr, nc, goal_r, goal_c)
                    heapq.heappush(open_heap, (f_score, g_new, nr, nc, i))

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
        came_from: Dict[Tuple[int, int, int], Optional[Tuple[int, int, int]]],
        goal_state: Tuple[int, int, int],
        dynamic_draft: float,
        ukc: float,
        min_depth: float,
        speed_knots: float,
        turning_radius_m: float,
    ) -> Dict[str, Any]:
        """Reconstruct waypoints, compute geodetic distances, and build depth soundings profile."""
        raw_path: List[Tuple[int, int]] = []
        curr: Optional[Tuple[int, int, int]] = goal_state
        while curr is not None:
            raw_path.append((curr[0], curr[1]))
            curr = came_from.get(curr)
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
                    fraction = i / max(1, n_samples - 1)
                    sample_dist = total_distance_m + seg_len_m * fraction
                    sample_lat = lat0 + (lat1 - lat0) * fraction
                    sample_lon = lon0 + (lon1 - lon0) * fraction
                    clearance = d - dynamic_draft - ukc
                    if clearance < min_clearance:
                        min_clearance = clearance

                    profile.append({
                        "distance_from_start_m": round(sample_dist, 1),
                        "depth": round(d, 2),
                        "clearance": round(clearance, 2),
                        "lat": round(sample_lat, 6),
                        "lon": round(sample_lon, 6),
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
                    "lat": round(lat0, 6),
                    "lon": round(lon0, 6),
                })

        distance_nm = total_distance_m / 1852.0
        eta_hours = (distance_nm / speed_knots) if speed_knots > 0.0 else None

        # Build structured passage plan waypoints
        waypoints: List[Dict[str, Any]] = []
        n_wp = len(path)
        for i in range(n_wp):
            r, c = path[i]
            lat, lon = self.grid.get_cell_coords(r, c)
            depth = float(self.grid.depths[r, c])
            clearance = depth - dynamic_draft - ukc

            leg_bearing = 0.0
            leg_dist_nm = 0.0
            if i < n_wp - 1:
                r_next, c_next = path[i + 1]
                lat_next, lon_next = self.grid.get_cell_coords(r_next, c_next)
                leg_bearing = calculate_bearing_deg(lat, lon, lat_next, lon_next)
                leg_dist_nm = haversine_distance_m(lat, lon, lat_next, lon_next) / 1852.0

            turn_angle = 0.0
            turn_radius = 0.0
            rot_deg_min = 0.0
            wop_distance_m = 0.0
            if 0 < i < n_wp - 1:
                r_prev, c_prev = path[i - 1]
                lat_prev, lon_prev = self.grid.get_cell_coords(r_prev, c_prev)
                brg_in = calculate_bearing_deg(lat_prev, lon_prev, lat, lon)
                brg_out = leg_bearing
                diff = (brg_out - brg_in + 180.0) % 360.0 - 180.0
                turn_angle = abs(diff)
                if turn_angle >= 0.5 and turning_radius_m > 10.0 and speed_knots > 0.0:
                    rad_half = math.radians(turn_angle / 2.0)
                    tan_half = math.tan(rad_half)
                    leg_prev_m = haversine_distance_m(lat_prev, lon_prev, lat, lon)
                    leg_next_m = leg_dist_nm * 1852.0
                    tangent_dist = turning_radius_m * tan_half
                    max_t_dist = min(leg_prev_m * 0.40, leg_next_m * 0.40)
                    safe_t_dist = min(tangent_dist, max_t_dist)
                    eff_r = safe_t_dist / tan_half if tan_half > 0.001 else turning_radius_m
                    turn_radius = eff_r
                    rot = (5556.0 / math.pi) * (speed_knots / eff_r)
                    rot_deg_min = round(rot if diff >= 0.0 else -rot, 1)
                    v_ms = speed_knots * 0.514444
                    wop_distance_m = round(safe_t_dist + v_ms * 12.0, 1)

            waypoints.append({
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "leg_bearing_deg": round(leg_bearing, 1),
                "leg_distance_nm": round(leg_dist_nm, 2),
                "turn_angle_deg": round(turn_angle, 1),
                "turn_radius_m": round(turn_radius, 1),
                "rot_deg_min": rot_deg_min,
                "wop_distance_m": wop_distance_m,
                "port_xtd_nm": 0.10,
                "stbd_xtd_nm": 0.10,
                "depth_m": round(depth, 1),
                "clearance_m": round(clearance, 1),
            })

        return {
            "route": coordinates,
            "profile": profile,
            "waypoints": waypoints,
            "diagnostics": {
                "distance_m": round(total_distance_m, 0),
                "distance_nm": round(distance_nm, 2),
                "eta_hours": round(eta_hours, 2) if eta_hours is not None else None,
                "min_clearance_m": round(min_clearance, 2),
                "dynamic_draft_m": round(dynamic_draft, 2),
                "waypoints": len(path),
            },
        }
