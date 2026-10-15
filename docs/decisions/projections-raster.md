# Projections and raster conventions

Raster orientation, map-projection order and area measurement. Related: [architecture.md](architecture.md) (row 0 = north), [terrain-dem.md](terrain-dem.md) (project once).

### 2026-10-14 — Projection sampling is separable float32; masks stay at order 1; class grids go out as uint8
- **Decision:**
  - Every projection samples through `geo2stl/projections.py::_sample_grid`: each axis is
    interpolated in turn in float32, because the row coordinate depends only on the output row
    for every projection here. Results match `ndimage.map_coordinates` (order 0 exact, order 1
    within float32 rounding, identical NaN masks; `tests/test_projections_remap.py`).
  - `project_water_arrays` keeps the water mask at order 1 (F-SPEED #14 not done).
  - ESA land cover and the 0/1 water mask are sent as base64 uint8, with a
    `<name>_dtype: "uint8"` field (`app/server/core/validation.py::b64_encode_u8`; 255 = NaN).
    Every decoder (`ui-helpers.js::_decodeGrid`, `app/session/terrain_session.py::_decode_b64_grid`)
    reads the dtype field and defaults to float32. The satellite overlay goes out as float32
    base64 (`sat_values_b64`), since its values are continuous.
  - ESA and water are fetched once per box at the shared source size
    (`routers/terrain.py::_layer_source_dim`, `hydrology.py::water_surface_mask` `fetch_dim`), so a
    projection switch reads the cached Earth Engine image. The client reuses a cached water-mask
    response's ESA grid for the land-cover layer (`core/cache.js::waterMaskCache.findEsa`).
- **Why:** F-SPEED. `map_coordinates` in float64 cost 0.2–0.45 s per request, cache hits
  included; the separable sampler is about 3× faster (mercator 190 → 65 ms at dim 2000). The
  float32 transport of class grids was 4× larger than needed.
- **Rejected:**
  - `cv2.remap` (the plan's first choice): it quantises sub-pixel positions to 1/32 px, which moved
    a steep synthetic DEM by up to 0.54 m.
  - Water mask at order 0 (#14): it flipped about 1 % of pixels (about 5 % of the water pixels)
    to save 15–30 ms.
  - uint8 for the satellite overlay: its values are not small integers.
- **Supersedes / superseded by:** —
- **Source:** [F-SPEED plan](../plans/active/F-SPEED-region-open-and-fetch.md).

### 2026-10-13 — Longest side after projection
- **Decision:** `dim` is the longest side of the DEM **after** map projection, for every projection.
  One rule in `geo2stl/projections.py`: `projected_shape` is the exact output grid,
  `source_dim` / `source_shape` the plate-carrée grid to sample so the projection only shrinks, and
  `project_coordinates(target_dim=dim)` (through `project_grid`, `project_water_arrays`,
  `project_rgb_image`) forces the output onto `projected_shape`. The DEM route
  (`app/server/routers/terrain.py::get_terrain_dem`), every aligned layer route (water mask, ESA,
  satellite, hydrology, trails, borders), `app/server/routers/cities.py::get_city_raster`,
  `app/server/routers/composite.py::compute_composite_dem` and the export fallback
  `app/server/core/export_params.py::resolve_dem` sample at `source_dim` and project with
  `target_dim`, `none` included. Caches: `app/server/core/dem_cache.py::DEM_CACHE_SCHEMA_VERSION`
  4 with the sampling size `sd` in the key; layer caches key on the sampling size;
  `COMPOSITE_CACHE_VERSION` 7; hydrology print cache v2.
- **Why:** user decision 2026-10-13. SanFranciso (dim 600, cosine, h5_local) came out 474 × 428:
  the raw grid was 600 on the longitude side and cosine then narrowed it by ~0.79, so a 1 mm/px
  print was 474 mm, not 600. After: 600 × 541 (sampled at 759 px). With `target_dim`, layers
  sampled at any size land on the identical grid (`tests/test_projection_target_dim.py`).
- **Rejected:** upsampling the projected grid back to `dim` — interpolates every cell twice;
  stretching only the narrowed axis back to `dim` — the composite once did this (Apply to DEM widened
  the model by 1/cos(lat)).
- **Supersedes / superseded by:** refines [terrain-dem.md](terrain-dem.md) "Rasterize at the
  pre-projection size, project once" (the pre-projection size is now `source_shape`, larger than `dim`).
- **Source:** `tests/test_projection_target_dim.py`.

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
- **Status 2026-10-05:** deleted with the one-off tools (AUDIT-2026-10-05 §8).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-06 entry.

### 2026-08-06 — Rasters are row 0 = south everywhere, and the satellite layer was flipped [superseded]
- **Decision (then):** all align-tool rasters (OSM masks, STL heightmap, STL mask) are row 0 = south; ESRI imagery arrives row 0 = north, so both satellite fetch sites (export and `/api/refetch`) flip on read. Displays need `origin='lower'`.
- **Why:** only the export site flipped, so every satellite layer was mirrored north-south against the OSM layer it overlays.
- **Supersedes / superseded by:** superseded by [2026-09-26 — Raster row 0 is north by default](architecture.md#2026-09-26--raster-row-0-is-north-by-default). The align tool and registration still work row 0 = south, now by passing `row0="south"` explicitly.
- **Source:** decisions.md 2026-08-06 entry.
