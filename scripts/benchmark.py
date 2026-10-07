import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.grid import BathymetricGrid
from backend.core.router import Router as PyRouter
import seapath_native

print("--- SEAPATH ENGINE BENCHMARK ---")
print("Loading E8_2024.tif (900x948 cells)...")
grid = BathymetricGrid.load_from_geotiff("data/raw/E8_2024.tif").downsample(factor=10)

# Coordinates: Sevastopol to Yevpatoriya
min_depth = 1.2 + (0.6 * 100.0 / 100.0) + 0.5 # 2.3m

# Test 1: Short coastal (Sevastopol -> Yevpatoriya)
# Test 2: Long coastal passage (Sochi -> Yevpatoriya)
start_r, start_c = grid.lonlat_to_cell(39.96, 43.34) # Sochi approach
goal_r, goal_c = grid.lonlat_to_cell(33.40, 45.18)   # Yevpatoriya

# Snap to navigable water
def snap(r, c):
    for radius in range(50):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < grid.rows and 0 <= nc < grid.cols:
                    if grid.depths[nr, nc] >= min_depth:
                        return nr, nc
    return r, c

start_r, start_c = snap(start_r, start_c)
goal_r, goal_c = snap(goal_r, goal_c)

print(f"Pathfinding from ({start_r}, {start_c}) to ({goal_r}, {goal_c})...")

# Reference Python Engine
py_router = PyRouter(grid)
t0 = time.perf_counter()
res_py = py_router.find_path(start_r, start_c, goal_r, goal_c, 1.2, 10.0, 0.5)
t_py = time.perf_counter() - t0

# C++ Native Engine
native_grid = seapath_native.NativeGrid(
    grid.rows, grid.cols,
    grid.lat_min, grid.lat_max,
    grid.lon_min, grid.lon_max,
    grid.depths
)
native_router = seapath_native.NativeRouter(native_grid)
t0 = time.perf_counter()
res_cpp = native_router.find_path(start_r, start_c, goal_r, goal_c, 1.2, 10.0, 0.5)
t_cpp = time.perf_counter() - t0

print("\n--- RESULTS ---")
print(f"Python Reference Router: {t_py * 1000.0:.2f} ms")
print(f"C++ Native Router:       {t_cpp * 1000.0:.2f} ms")
print(f"Performance Speedup:     {t_py / t_cpp:.1f}x faster")

py_diag = res_py["diagnostics"]
cpp_diag = res_cpp["diagnostics"]
print(f"\nDistance: Py {py_diag['distance_nm']} NM | C++ {cpp_diag['distance_nm']} NM")
print(f"Clearance: Py {py_diag['min_clearance_m']} m | C++ {cpp_diag['min_clearance_m']} m")
print(f"Waypoints: Py {py_diag['waypoints']} | C++ {cpp_diag['waypoints']}")
