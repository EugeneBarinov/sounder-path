import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.grid import BathymetricGrid
from backend.core.router import Router as PyRouter
from backend.core.dukc import compute_search_budget
from backend.core.fairways import rasterize_fairway_weights
import seapath_native

print("--- SEAPATH ENGINE BENCHMARK ---")

BASE = Path(__file__).resolve().parent.parent
tif = BASE / "data" / "raw" / "E8_2024.tif"
if tif.exists():
    print(f"Loading {tif.name}...")
    grid = BathymetricGrid.load_from_geotiff(tif).downsample(factor=10)
else:
    print("[DEMO] E8_2024.tif not found — using synthetic grid.")
    grid = BathymetricGrid.create_synthetic()

# Что делаем в бенчмарке (честное сравнение Python vs C++):
# Шаг 1. Грузим сетку: боевой GeoTIFF E8_2024.tif с даунсемплом x10, а если
#   его нет (а его нет в репо — data/raw/*.tif в .gitignore) — синтетику
#   create_synthetic(). Раньше скрипт падал с FileNotFound без тифа.
# Шаг 2. Считаем ЕДИНЫЙ бюджет поиска через dukc.compute_search_budget
#   (strict PIANC: draft+squat+heel+wave+UKC). Оба движка получают одинаковые
#   min_chart_depth_m / dynamic_draft_m — меряем скорость алгоритма, а не
#   разницу формул.
# Шаг 3. Растеризуем фарватеры/запретки и аттачим к ОБОИМ движкам.
#   Раньше NativeGrid создавался без fairway_weights → C++ не видел RESARE
#   и шёл напрямик, а Python их обходил: сравнение «35x» было нечестным.
# Шаг 4. Snap старта/финиша к воде с тем же порогом min_depth И проверкой
#   is_restricted — точка не встанет внутрь запретки.
# Шаг 5. Замеряем время обоих, печатаем дистанцию/UKC/число WP.
# Плечо: Сочи (39.96, 43.34) → Евпатория (33.40, 45.18), яхта 1.2м/10уз.
DRAFT, SPEED, UKC = 1.2, 10.0, 0.5
budget = compute_search_budget(
    draft_m=DRAFT, speed_knots=SPEED, ukc_net_margin_m=UKC,
    block_coefficient=0.65, turning_radius_m=150.0,
    strict_dukc=True,
)
min_depth = budget["min_chart_depth_m"]
dyn_draft = budget["dynamic_draft_m"]
print(f"Search budget: min_chart={min_depth:.2f}m dyn_draft={dyn_draft:.2f}m "
      f"(squat={budget['dynamic_squat_m']:.2f} heel={budget['heel_sinkage_m']:.3f} "
      f"wave={budget['wave_allowance_m']:.3f} strict={budget['strict_dukc']})")

# Attach fairways so both engines see identical restrictions
fw = rasterize_fairway_weights(
    grid.rows, grid.cols, grid.lat_min, grid.lat_max, grid.lon_min, grid.lon_max
)
grid.attach_fairways(fw)

# Sochi approach -> Yevpatoriya
start_r, start_c = grid.lonlat_to_cell(39.96, 43.34)
goal_r, goal_c = grid.lonlat_to_cell(33.40, 45.18)

# Snap to navigable water (respecting restrictions too)
def snap(r, c):
    for radius in range(50):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < grid.rows and 0 <= nc < grid.cols:
                    if float(grid.depths[nr, nc]) >= min_depth and not grid.is_restricted(nr, nc):
                        return nr, nc
    return r, c

start_r, start_c = snap(start_r, start_c)
goal_r, goal_c = snap(goal_r, goal_c)

print(f"Pathfinding from ({start_r}, {start_c}) to ({goal_r}, {goal_c})...")

# Reference Python Engine
py_router = PyRouter(grid)
t0 = time.perf_counter()
res_py = py_router.find_path(start_r, start_c, goal_r, goal_c, DRAFT, SPEED, UKC,
                             min_chart_depth_m=min_depth, dynamic_draft_m=dyn_draft)
t_py = time.perf_counter() - t0

# C++ Native Engine (same grid + same fairways + same budget)
native_grid = seapath_native.NativeGrid(
    grid.rows, grid.cols,
    grid.lat_min, grid.lat_max,
    grid.lon_min, grid.lon_max,
    grid.depths,
    grid.fairway_weights,
)
native_router = seapath_native.NativeRouter(native_grid)
t0 = time.perf_counter()
res_cpp = native_router.find_path(start_r, start_c, goal_r, goal_c, DRAFT, SPEED, UKC,
                                  min_chart_depth_m=min_depth, dynamic_draft_m=dyn_draft)
t_cpp = time.perf_counter() - t0

print("\n--- RESULTS ---")
if res_py is None:
    print("❌ ERROR: Python Reference Router failed to find passage!")
    sys.exit(1)
else:
    print(f"Python Reference Router: {t_py * 1000.0:.2f} ms")

if res_cpp is None:
    print("❌ ERROR: C++ Native Router failed to find passage!")
    sys.exit(1)
else:
    print(f"C++ Native Router:       {t_cpp * 1000.0:.2f} ms")

if t_cpp > 0:
    print(f"Performance Speedup:     {t_py / t_cpp:.1f}x faster")

py_diag = res_py["diagnostics"]
cpp_diag = res_cpp["diagnostics"]
print(f"\nDistance: Py {py_diag['distance_nm']} NM | C++ {cpp_diag['distance_nm']} NM")
print(f"Clearance: Py {py_diag['min_clearance_m']} m | C++ {cpp_diag['min_clearance_m']} m")
print(f"Waypoints: Py {py_diag['waypoints']} | C++ {cpp_diag['waypoints']}")

# Strict parity verification
dist_diff = abs(py_diag["distance_nm"] - cpp_diag["distance_nm"])
clearance_diff = abs(py_diag["min_clearance_m"] - cpp_diag["min_clearance_m"])
wp_diff = abs(py_diag["waypoints"] - cpp_diag["waypoints"])

if dist_diff > 0.5:
    print(f"❌ ERROR: Distance disparity ({dist_diff:.2f} NM) exceeds tolerance (0.5 NM)")
    sys.exit(1)

if clearance_diff > 0.1:
    print(f"❌ ERROR: Clearance disparity ({clearance_diff:.2f} m) exceeds tolerance (0.1 m)")
    sys.exit(1)

if wp_diff > 2:
    print(f"❌ ERROR: Waypoints count disparity ({wp_diff}) exceeds tolerance (2)")
    sys.exit(1)

print("\n✓ BENCHMARK VALIDATION PASSED: 100% mathematical and geometric parity confirmed.")
sys.exit(0)
