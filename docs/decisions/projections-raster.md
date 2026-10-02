# Projections and raster conventions

Raster orientation, map-projection order and area measurement. Related: [architecture.md](architecture.md) (row 0 = north), [terrain-dem.md](terrain-dem.md) (project once).

### 2026-10-02 — Gall is x = λ/√2 and sinusoidal fills its widest row; shapes are tested against textbook maths
- **Decision:** `geo2stl/projections.py` Gall stereographic uses x = λ/√2 (`_X_SCALE`, passed to
  `_project_cylindrical_y`), and sinusoidal samples lon = centre + x·half_width·widest_cos/cos(lat), so
  x ∈ [−1, 1] spans the box's widest row. `tests/test_projection_shapes.py` checks each projection's
  small-box aspect against the textbook derivatives, independent of `expected_aspect_ratio`.
- **Why:** a live sweep of every projection (user asked "are different projections working?") gave
  Granada (2 km, 37° N) Gall 423 × 320 px where the maths says ≈ 300, and sinusoidal 254 × 320 where it
  says ≈ 320. Gall used Miller's x = λ (√2 too wide); sinusoidal lacked the widest_cos factor, so only
  the middle cos(lat) of the width held data and the rest was trimmed as empty. The existing tests
  compared outputs with `expected_aspect_ratio`, which shared the Gall formula, so they passed. After
  the fix: Gall 299 × 320, sinusoidal 319 × 320; the new test fails on the old code (Gall 1.322 vs 0.934,
  sinusoidal 0.794 vs 1.000).
- **Rejected:** —
- **Supersedes / superseded by:** —

### 2026-09-30 — Routes accept only clip_valid_region; stored clip_nans is renamed on load
- **Decision:**
  - The deprecated `clip_nans` alias is gone from every route and request model (terrain, cities, composite, height, schemas) and from export parsing; they read only `clip_valid_region`. `/api/settings` defaults and the SDK use `clip_valid_region`.
  - The one place the old key is still read: `map2stl/app/server/routers/regions.py::_rename_legacy_clip_nans`, as saved region settings load (an explicit `clip_valid_region` wins). The browser also renames it in saved presets (`app/client/static/js/modules/ui/settings-compat.js::normalizeSettingsKeys`).
  - The `geo2stl.projections` functions keep their `clip_nans` parameter name (library-internal).
- **Why:** two spellings meant every reader had to check both, and the composite export path read only `clip_nans`, so an unchecked box was ignored once the client sent `clip_valid_region`.
- **Rejected:** mapping in `ExportContext` — it covers export bodies but not the settings the SDK and browser load.
- **Supersedes / superseded by:** —

### 2026-08-28 — Web Mercator areas are not square metres
- **Decision:** building-area filters reproject through `map2stl/city2stl/fetch.py::_to_metric` (local UTM via `estimate_utm_crs()`, falling back to 3857 with a warning). `map2stl/app/server/config.py::COARSE_MIN_BUILDING_AREA_M2` lowered 2000 → 1200 (user).
- **Why:** Mercator inflates area by sec² of latitude (1.68× at Breckenridge, 4× at 60°). The 2000 m² floor was really ~1190 m² at Breckenridge and different in every city. 1200 keeps the observed behaviour, now the same at every latitude.
- **Rejected:** raising the client's `CITY_MAX_DIAG_KM` 10 → 15 so a 12 km city skips the coarse tier — changes which tier runs instead of fixing the tier, and saves no network time (Overpass runs either way).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 entry.

### 2026-08-06 — The satellite-flip fix script is a one-shot, not a tool
- **Decision:** `map2stl/tools/align_tool/_fix_sat_flip.py` flips already-exported sat.png files in place and rebuilds align_data.js. It must never run after a full re-export and should be deleted once that lands.
- **Why:** it is destructive by repetition — a second run undoes the fix and reintroduces the bug.
- **Status 2026-09-28:** the file still exists.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-06 entry.

### 2026-08-06 — Rasters are row 0 = south everywhere, and the satellite layer was flipped [superseded]
- **Decision (then):** all align-tool rasters (OSM masks, STL heightmap, STL mask) are row 0 = south; ESRI imagery arrives row 0 = north, so both satellite fetch sites (export and `/api/refetch`) flip on read. Displays need `origin='lower'`.
- **Why:** only the export site flipped, so every satellite layer was mirrored north-south against the OSM layer it overlays.
- **Supersedes / superseded by:** superseded by [2026-09-26 — Raster row 0 is north by default](architecture.md#2026-09-26--raster-row-0-is-north-by-default). The align tool and registration still work row 0 = south, now by passing `row0="south"` explicitly.
- **Source:** decisions.md 2026-08-06 entry.
