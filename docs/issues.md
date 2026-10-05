# Known Issues — map2stl

_Last updated: 2026-10-05_

- Live bugs and technical debt only. Planned work is in the roadmap,
  [plans/README.md](plans/README.md); ideas are in [proposals.md](proposals.md).
- Fixed issues move to [history/issues-resolved.md](history/issues-resolved.md), with their
  full write-ups (the fixed §2–§2e, §4, §0, §0d and all older resolved entries are there).
- Section ids `0c` and `3` are cited from code (`city2stl/height/providers/google_3d.py`,
  `city2stl/height/providers/gba.py`): keep them.

## Active bugs — from the full code audit (2026-10-05)

Source and full list: [history/audits/AUDIT-2026-10-05.md](history/audits/AUDIT-2026-10-05.md).
Eight of its silent wrong-answer bugs were fixed the same day
([issues-resolved](history/issues-resolved.md)). Still open:

- **`numpy2stl` `stl2numpy/heightmap.py::_auto_resolution` swaps rows and cols**, so the grid comes out stretched.
  The fix belongs in the numpy2stl repo.

## Active bugs — from the pipeline audit

Source: [history/audits/pipeline-audit-2026-08-26.md](history/audits/pipeline-audit-2026-08-26.md)
(ids = its numbering). Re-checked against the code 2026-09-28: 12 findings fixed, 2 obsolete,
the rest below.

- **PA-9 Region create resets parameters** (update fixed): `create_region` uses the
  `RegionParameters()` defaults (dim 200 / height 10 / base 2). The DB defaults are
  600 / 25 / 5 (`app/server/schemas.py::RegionParameters` vs `app/server/core/db.py`).
- **PA-10 Settings are not validated or versioned**: `save_region_settings_route` stores the
  raw dict; there is no settings model (the unused `RegionSettings` was deleted 2026-09-30) and no
  `schema_version`. See F-FE1 step 3.
- **PA-14 Silent zero-fill** (partly fixed; ESA/EE and all-tiles-failed now raise):
  - `geo2stl/dem.py::fetch_dem` (local source) returns zeros on error.
  - `geo2stl/hydrology.py` river rasteriser returns zeros on error.
  - A single failed tile stays black, logged at DEBUG only (`geo2stl/imagery.py::fetch_tile`).
- **PA-16 Non-atomic cache writes** (partly fixed: `geo2stl/cache.py` array / OSM / composite
  writes are atomic). Still written in place, so a truncated file stays cached:
  - `geo2stl/opentopo.py::fetch_opentopo_dem`
  - the HydroRIVERS shapefile extract in `geo2stl/hydrology.py`
  - `geo2stl/sat2stl.py` (joblib)
  - `geo2stl/imagery.py::fetch_tile`
- **PA-3 Binary STL writer**: `numpy2stl/src/numpy2stl/io/writers.py::writeSTL` opens the final
  path directly (no temp + rename) and joins the whole mesh in memory. The app's mesh path uses
  trimesh; `writeSTL` is still reached from `geo2stl/dem.py`, `geo2stl/write.py` and numpy2stl
  `numpy2stl/src/numpy2stl/core/solid.py`.
- **PA-12 Two caches escape `CACHE_ROOT`** — fixed 2026-09-30: `geo2stl/opentopo.py::CACHE_PATH`,
  `geo2stl/sat2stl.py::CACHE_DIR` and `app/server/config.py::EE_CACHE_DIR` sit under
  `geo2stl.cache.CACHE_ROOT` (so `$MAP2STL_CACHE` moves them); `EE_CACHE_DIR` had pointed at
  `Code/cache/ee`, which sat2stl no longer wrote. The unused `OPENTOPO_CACHE_PATH` was removed.
- **PA-M Medium, still open**:
  - `app/server/core/db.py`: `_CREATE_REGIONS` lacks continent / source / city / tags.
  - `app/server/core/db.py::get_db` connections are never closed.
  - Region routes: no validators, `_ensure_db()` on every request, and blocking sqlite in
    `async def` routes.
  - Export temp files leak on failure or restart (`export._write_mesh`; nothing after `yield`
    in the lifespan).
  - Half-pixel convention mismatch: the projection grid uses nodes, `burn_polygons` uses edges.
  - `blend_layers` checks shape only; `verify_layer_alignment` has no caller.
  - `water`, `esa_lc` and `hydrology` cache keys have no version token.
  - No export concurrency limit (`export_tasks.start_export_task`, one thread per request).
  - `read_text` / `write_text` / `writeOBJ` without an explicit encoding.
  - `city2stl/fetch.py` still calls osmnx `features_from_bbox`.
  - A contour-engraving failure is still downgraded to a warning by a blanket `except`
    (`export._apply_contour_lines`).
- **PA-S Structural, still open**:
  - Two city-raster routes (`app/server/routers/composite.py` and `app/server/routers/cities.py`).
  - Session:
    - `terrain_session.py` is 3.1k lines.
    - `enrich_buildings_with_heights` has no callers.
    - `_kill_stale_server` kills any owner of port 9090.
  - Dead code:
    - `geo2stl/write.py::savefile` (row-flipped, notebook-only).
    - `create_dem_model` / `process_region` are notebook-only.
  - Presets:
    - `satImgResolution` is not re-applied.
    - `hydrology.scale_m` is not collected.
    - `wat.dim ?? wat.sat_scale` lets `dim` override the legacy `sat_scale`.
  - The `rotation` setting has no consumer.
- **PA-F Frontend**: two client stacks (`main.js` + `vue/`); three.js r128 loads from a
  CDN (`app/client/templates/index.html`). See F-FE1.

## Active data limitations

### 0e. Google 3D Tiles has no buildings over Cartagena (surveyed 2026-08-28)
- Outside Google's photorealistic cities, the endpoint serves a global base mesh with no
  buildings. The fetch "works" (99 % filled, fine geometric error) but is flat.
- Relief inside a 1 km window tells the two cases apart: 35–105 m where buildings exist
  (Miami, Rio, Buenos Aires, Santiago, Medellín), 1–4 m where they do not (Cartagena, Lima,
  Panama City, Barranquilla).
- `_looks_built` now returns an empty result with a warning instead of a flat raster.
- **Still open:** in data-poor cities the best source is missing. Cartagena falls back to Open
  Buildings 2.5D (~91 %, capped at 99.5 m) and Overture (~9 %). Nothing measured so far
  reaches the Bocagrande towers that Open Buildings clips.
- Full measurements: [history/issues-resolved.md](history/issues-resolved.md) (§0d) and §0c
  below.

## Active technical debt

### 0a. Skyline height pipeline — known weaknesses (audit 2026-08-28)
Found while fixing the elevation-datum bug below. All are reported, none are fixed:

- **The tag-disagreement filter is circular.** `city2stl/skyline/_core/height.py` drops any prediction more
  than `min(2 x height_tag, 50 m)` from the OSM tag, so accuracy measured against tagged
  buildings is partly self-fulfilling, and the "over-prediction is rare" argument used to
  justify the upward-only rescue is derived from filtered data.
- **The outlier-seed rule bites at exactly 3 seeds.** A 1.5 x MAD threshold over three
  values nearly always downweights one of them, whether or not it deserves it.
- **`n_views` / `views` count MAD-rejected views.** The reported view list includes
  estimates that were dropped from the mean, with no marker saying so.
- **`enhance_buildings_with_raster` never revises an `osm_levels` height**, even where the
  raster clearly disagrees.
- **`_footprint_roof_y_from_mask` needs half the columns** (`cols // 2`) to be building,
  which misses narrow spires and setback towers.
- **Depth is treated as horizontal distance.** `depth_height_from_segment` uses the
  metric depth as `forward_m`; it is really slant range. Small near the horizon, growing
  with the elevation angle.

### 0b. `city2stl/skyline/runs/` is ~6 GB on disk
Gitignored (not in history) but a OneDrive-sync burden. As of 2026-07-26 it is
~6 GB, dominated by regenerable caches: `image_cache` (2.6 GB),
`satellite_image_cache` (1.9 GB), `region_reports` (1.4 GB PDFs). Run
**`make clean-runs`** to drop the cache dirs + stale `*.log` (~4.6 GB); it keeps
`region_reports/` and `height_traces/`. See [history/audits/AUDIT-2026-06-07.md](history/audits/AUDIT-2026-06-07.md).

### 1. `<script>` vs module boundary
`app.js` is still a plain script and modules still coordinate through `window.*`. Inline
handlers are gone (except the dev-only debug overlay). The fix is F-FE1 steps 1, 2 and 6
([plans/active/F-FE1-vue-consolidation.md](plans/active/F-FE1-vue-consolidation.md)).

## Measurements and fixes cited from code

### 0c. Satellite height sources beat the panorama pipeline (measured 2026-08-28)
Scored per footprint against the registered Miami STL plate, same
p90-of-covered-pixels rule, with the pipeline's tag filter disabled:

| Source | n | MAE | bias | corr |
|---|---|---|---|---|
| `google3d` (Google 3D Tiles) | 63 | **14.02 m** | +7.28 | **+0.922** |
| `open_buildings` (Overture) | 61 | 20.95 m | +8.40 | +0.866 |
| Skyline panorama pipeline | — | 59.85 m | — | +0.117 |
| `ndsm` | 10 | 55.88 m | −55.88 | +0.563 |

Google 3D Tiles is the most accurate height source in the project, and the gap
widens on exactly the buildings that matter most. On the 23 footprints above
100 m -- the class the panorama pipeline reads as a third of true height -- it
scores MAE 9.29 m at a bias of +1.73 m, and it recovers a tallest building of
254 m against the plate's 256 m. It has no height ceiling and no resolution
floor, which is what separates it from both footprint sources.

It passes the same independence test as Overture: split by whether OSM tags a
height, MAE is 15.93 m on the 36 tagged footprints and 11.48 m on the 27
untagged ones. Better where OSM says nothing.

The Overture result is not an echo of the OSM tags it partly ingests: split by
whether OSM tags a height, it scores MAE 24.03 on the 35 tagged footprints and
MAE 16.81 on the 26 untagged ones, and it matches zero OSM tags to within
0.5 m. Accuracy is *better* where OSM says nothing.

For data-poor cities the two satellite sources are complementary rather than
alternative. Over Cartagena, Google Open Buildings 2.5D answers 298 of 329
registered footprints (91%) but its heights are capped at 99.5 m; Overture
answers 31 (9%) and reaches 190 m uncapped. The cap, not the coverage, is what
leaves Cartagena's Bocagrande towers unrepresented — and those towers are
exactly the buildings the panorama pipeline gets most wrong.

### 3. Raster height enhancement made buildings shorter than the 10 m fallback — fixed 2026-08-30
- Kept here because code cites it: a provider's ranking was tuned on an argument, not a
  measurement.
- Cause: the merge ranked providers by a flat confidence constant and ignored resolution.
  A 30 m nDSM beat 5 m Open Buildings and pushed buildings to the 3 m clamp.
- Fix: rank by confidence × `resolution_priority(res)`; per-building enhancement refuses
  sources coarser than 10 m (`BUILDING_RESOLUTION_LIMIT_M`).
- Full write-up with the seven-city tables:
  [history/issues-resolved.md](history/issues-resolved.md) (§3).

## Audits to re-verify

- Re-verify the colour-contrast and UX audit items against the running UI:
  [history/audits/accessibility-audit.md](history/audits/accessibility-audit.md),
  [history/audits/ux-audit.md](history/audits/ux-audit.md). They were archived as-is on
  2026-09-28 and some items may still be open (see proposal A11Y-1).

## Carried over from the memory bank (2026-09-28)

Open items that lived only in `Code/claude/memory-bank/activeContext.md`. Dates are when each was
last measured.

- **Height model default path is off by one** (found 2026-09-28):
  `city2stl/height/predict.py` sets `_MODELS_DIR` from `Path(__file__).resolve().parents[3]`,
  which is `Code/models/`, not `map2stl/models/`. The default `height_unet.pt` is also absent
  from both, so the U-Net default checkpoint never loads.
- **Cache sweep reaches ~1 % of the cache** (measured 2026-09-04; deferred by decision):
  - `prune_all_caches` (`app/server/core/cache.py`) walks only the `NAMESPACE_TTL` keys, once
    at startup, and is non-recursive.
  - Never swept: hydrorivers (4.8 GB), ndsm (1.1 GB), roof_tiles (392 MB), ee, google3d, the
    loose root-level Overpass `.json` files.
  - The opentopo tif cache (6.9 GB) has orphaned entries since `dim` left its key.
  - Fix: sweep the tree that exists. Decide per directory whether a refetch is cheap before
    deleting anything.
- **Height/roof loose ends** (2026-09-06, not re-checked since):
  - GHSL WMS and the Copernicus EU building-height WCS fail on every bbox (Copernicus: HTTP 400).
  - `shadow_conf` saturates at 1.0 (`city2stl/roof_features.py`).
  - `_attach_building_terrain` (`city2stl/skyline/region_data.py`) is called only from a demo
    script, never in production.
  - The Miami roof call-rate job segfaults (native import-order clash), so the third call-rate
    row in [reference/roof-shape-model.md](reference/roof-shape-model.md) is empty.
- **Registration water data** (2026-09-14): a partial water fetch degrades registration
  silently, and a cached empty layer is never retried (the Lisbon case in
  [decisions/registration-refinement.md](decisions/registration-refinement.md)). Nine plates
  have an empty water mask image.
- **Frontend**: `LAYER_CANVAS_IDS` in `stacked-layers.js` has no `CityOverlay` entry, although
  `_layerOrder` lists it (2026-08-30).
