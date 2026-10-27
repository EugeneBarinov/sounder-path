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

### Ранее (из истории git, контекст)
- Многоточечная прокладка, XTD-коридор, аудит IEC 61174, DUKC/приливы/бутылочные
  горлышки, симулятор мостика, C++ ядро, фарватеры S-57/ENC, экспорт GPX/RTZ.

## 🔶 Запланировано (по приоритету)

### P0 — корректность безопасности
- [ ] `safety.py:verify_ecdis_route_safety` — сейчас игнорирует `fairways_coll`:
      добавить проверку близости к RESARE (аларм захода в запретку).
- [ ] `export.py` — `safetyContour = draft+UKC` → полный `dynamic_draft+UKC`;
      XTD брать из запроса, а не хардкод `0.10 NM` (сейчас коридор на карте ≠ RTZ).
- [ ] Фронт `app.js:1208` — `stbd_xtd_m: portXtdM` (copy-paste): чинить на `stbdXtdM`.
- [ ] Python-роутер: портировать heading-штраф из C++ (`router.cpp:165-180`) или
      задокументировать расхождение fallback.
- [ ] `bottleneck.py` — `K=50` фикс. → честный пересчёт по h/T; проверять
      `recommended`, а не `safe_speed`, против минимума управляемости 3.5 уз.

### P1 — воспроизводимость демо
- [ ] Синтетика vs heatmap: экстенты `create_synthetic` (33.12–40.0/43.12–46.5)
      vs `heatmap_bounds` (…–41.0/…–47.3) + хардкод координат в `app.js:500` —
      выровнять, иначе оверлей врёт.
- [ ] `requirements.txt` — добавить `affine`, `pybind11` (сейчас чистая установка ломается).
- [ ] `fairways.py` — silent-fallback `weights=1` без лога → явный warn, иначе
      запретки молча игнорируются.
- [ ] `download_data.py` — sample/чексумма тифа, понятный `exit 1` для CI.

### P2 — режим планирования (state-machine фронта)
- [ ] Ввести `idle | planning | verified`: вход «Новый план», заморозка черновика,
      кнопка «Проверить» → `safety/bottleneck/dukc`, только после `PASSED/ACK` —
      экспорт и симулятор. Сейчас всё реактивно и без версий плана.
- [ ] `app.js` — разбить монолит 1808 строк, SRI/offline для CDN, abort для
      `/api/depth` на mousemove.
- [ ] Дубли `calculate_bearing_deg` (×3), `flat vs haversine` дистанции — централизовать.
- [ ] Коридор XTD — mitre/endcap (сейчас самопересечение на острых поворотах).

### P3 — опс
- [ ] Smoke-тест `POST /api/route` на синтетике (до/после), лимит итераций A*,
      CORS для внешних ЭКНИС, Dockerfile/CI.
- [ ] `setup.py` — `-O3`, пины `pybind11`.
- [ ] Секреты: отозвать `ghp_...` из чата и `sk-proj-...` в `/workspace/docker-compose.yml`.

## 📌 Правила работы (напомнилка)
- Работаем локально в `/workspace/sounder-path/sounder-path`, коммиты локально.
- `git push` / PR — только по явной команде. `main` защищён → через PR.
- Этот файл обновлять каждым заходом: дата, файлы, проверка, статус пуша.
