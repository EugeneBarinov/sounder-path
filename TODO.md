# SeaPath — TODO: что изменилось и что запланировано

> Живой файл. Обновляется каждым заходом. Формат: дата → изменение → проверка.
> Локальная работа по умолчанию; пуш в GitHub — только по команде.

## ✅ Сделано

### 2026-10-11 — Единый бюджет поиска DUKC (ветка `feat/unified-dukc-budget`, коммит `dbbb818`)
**Проблема:** порог глубины для A* считался в трёх местах по-разному
(`main.py` → `draft+squat+UKC`, роутеры пересчитывали squat внутри себя,
полный бюджет `squat+крен+волна` жил только в отчёте). Поиск пропускал мель,
а пост-проверка давала `CRITICAL`.

**Что изменено (7 файлов):**
- `backend/core/dukc.py` — новая каноническая `compute_search_budget(...)`:
  `strict=True` → `min_chart = draft + squat(Баррасс) + heel + wave + max(0.05, UKC−tide)`;
  `strict=False` → legacy только squat (для A/B и отката). Подробные русские
  комментарии расчётов + числовой пример (яхта 1.2 м/10 уз: strict 2.46 м vs legacy 2.45 м).
- `backend/main.py` — `RouteRequest` + `beam_m / exposed_waters / strict_dukc`;
  `calculate_route` считает бюджет ОДИН раз и отдаёт роутерам готовые
  `min_chart_depth_m + dynamic_draft_m`; диагностика отдаёт
  `dynamic_draft / heel / wave / min_chart_depth / strict_dukc`;
  `dukc_budget` тоже с `beam/gm/exposed`. Комменты: бюджет, склейка профиля
  (`clearance + tide`), вызов роутера, отчётный DUKC vs поисковый.
- `backend/core/router.py` — `find_path(..., min_chart_depth_m, dynamic_draft_m)`:
  готовые числа → пропуск внутреннего пересчёта; иначе legacy. Докстринг пошагово.
- `backend/native/router.hpp / router.cpp` — те же 2 параметра (дефолт `NaN` →
  legacy), плюс bounds-check старта/финиша (OOB → `success=false` → API 400, не 500).
- `backend/native/bindings.cpp` — мост `None→NaN`, `try/catch` на границе языков.
- `scripts/benchmark.py` — единый бюджет + `fairway_weights` в ОБА движка
  (раньше C++ мерялся без запреток) + fallback на синтетику без тифа +
  обработка `NO PASSAGE`.

**Проверка:** `py_compile` — `PY_OK`; `compute_search_budget` smoke — сошлось
(strict 2.46 / legacy 2.45 / tide+1 → `effective_ukc` 0.05 / волна 2 м → 4.83 м).
Полный `POST /api/route` не прогнан — в оболочке нет `numpy/fastapi/rasterio`
и нет `pip`. Нужен env с `requirements.txt`.
**Статус пуша:** прямой пуш в `main` отклонён (`GH006`, ветка защищена);
запушена ветка `feat/unified-dukc-budget`, PR создаётся вручную.

### 2026-10-11 — P0 Безопасность: CRS валидация, ZOC, Rate Limiting (ветка `feat/safety-resare-export-fix`, коммит `5b195dd`)
**Что изменено (5 файлов):**
- `backend/core/fairways.py` — P0.4 CRS валидация: `_detect_fc_crs()` определяет EPSG из GeoJSON CRS,
  `_transform_geometry_to_wgs84()` трансформирует не-WGS84 геометрии через pyproj (опционально,
  с graceful degradation), `load_fairways_geojson()` логирует предупреждение при несоответствии CRS.
  Также добавлен `rasterize_zoc_mask()` — растеризация классов ZOC (A1..U) из свойств `zoc_class`/`CATZOC`.
- `backend/core/grid.py` — P0.1 ZOC: `zoc_mask` в `BathymetricGrid.__init__`, `attach_zoc()`,
  `get_zoc_class()`, `get_zoc_penalty()` (штрафы: A1/A2=0, B=0.1, C=0.3, D=0.6, U/unknown=0.8).
  `downsample()` сохраняет zoc_mask через max-pooling (худший класс побеждает).
- `backend/core/router.py` — P0.1 ZOC: `get_zoc_penalty(nr, nc)` добавляется к стоимости шага A*,
  маршрут уходит из зон с низкой достоверностью гидрографии.
- `backend/core/safety.py` — P0.1+P0 RESARE: `_point_in_polygon()` ray-casting точка-во-многоугольнике,
  шаг 5 `verify_ecdis_route_safety` проверяет waypoint-inside-RESARE через `fairways_coll`
  (ALM-10 CRITICAL). Шаг сканирования коридора добавляет ZOC_LOW_CONFIDENCE hazard (классы 0/5/6)
  и ALM-11 WARNING. Пошаговые комментарии на русском.
- `backend/core/export.py` — P0: `safetyContour` fallback теперь включает оценку squat
  (`draft + est_squat + ukc` через Barrass) вместо чистого `draft + ukc`.
- `backend/main.py` — P0.1: импорт `rasterize_zoc_mask`, растеризация и `attach_zoc()` для
  full + routing сеток. P0.5: `rate_limit_middleware` — in-memory ограничение 60 запросов/мин
  на IP для `/api/route`, ответ 429 с `Retry-After`.
- `TODO.md` — этот файл.

**Проверка:** `py_compile` — `PY_OK` для всех 5 файлов. Pure-Python smoke тесты:
CRS детекция (WGS84=4326, EPSG:3857=3857, none=None), ZOC mapping (A1=1, D=5, U=6, unknown=0),
точка-во-многоугольнике (inside/outside/edge).

### Ранее (из истории git, контекст)
- Многоточечная прокладка, XTD-коридор, аудит IEC 61174, DUKC/приливы/бутылочные
  горлышки, симулятор мостика, C++ ядро, фарватеры S-57/ENC, экспорт GPX/RTZ.

## 🔶 Запланировано (по приоритету)

### P0 — корректность безопасности (оставшееся)
- [ ] Фронт `app.js:2614` — `stbd_xtd_m: portXtdM` (copy-paste): один слайдер XTD,
      оба борта получают одинаковое значение. Нужно либо отдельный слайдер стренд-борта,
      либо явное Dokument.
- [ ] Python-роутер: heading-штраф уже портирован в `router.py` (строки 247-259),
      соответствует C++ логике — задача закрыта.
- [ ] `bottleneck.py` — `K=50` фикс. → честный пересчёт по h/T; проверять
      `recommended` против минимума управляемости ~3.5 уз.
- [ ] P0.2 Плотность судоходства (AIS density): слой `ais_density` в `grid.py`,
      штраф в `router.py`, аларм в `safety.py`. Требует исходных данных AIS.
- [ ] P0.3 Приливы в реальном времени: модуль `backend/core/tides.py`,
      вычисление `tide_offset_m` по координатам и времени.
- [ ] P0.2/AIS и P0.3/приливы отложены до появления исходных данных.

### P1 — воспроизводимость демо
- [ ] Синтетика vs heatmap: экстенты не совпадают — оверлей врёт.
- [ ] `requirements.txt` — добавить `affine`, `pybind11` (чистая установка ломается).
- [ ] `download_data.py` — чексумма тифа, понятный `exit 1` для CI.

### P2 — режим планирования (state-machine фронта)
- [ ] `idle | planning | verified`: черновик → проверка → утверждение → экспорт.
- [ ] `app.js` — разбить монолит, SRI/offline для CDN.
- [ ] Дубли `calculate_bearing_deg` (×3), централизовать геодезию.
- [ ] Коридор XTD — mitre/endcap (самопересечение на острых поворотах).

### P3 — опс
- [ ] Smoke-тест `POST /api/route`, лимит итераций A*, CORS, Dockerfile/CI.
- [ ] `setup.py` — `-O3`, пины `pybind11`.
- [ ] Секреты: отозвать `ghp_...` и `sk-proj-...`.

## 📌 Правила работы (напомнилка)
- Работаем локально в `/workspace/sounder-path/sounder-path`, коммиты локально.
- `git push` / PR — только по явной команде. `main` защищён → через PR.
- Этот файл обновлять каждым заходом: дата, файлы, проверка, статус пуша.
