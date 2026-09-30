# Terrain and DEM

DEM sources, the DEM cache key, availability, and how empty or uncovered DEMs are reported. Related: [projections-raster.md](projections-raster.md), [mesh-pipeline.md](mesh-pipeline.md).

### 2026-09-30 — The local H5 DEM is read tile by tile and block-averaged to about 2x the request
- **Decision:** `geo2stl/dem.py::fetch_h5_dem(max_px=…)` reads each 5° tile in row bands and
  sums k x k blocks into the output (mean), so the longer side is about `max_px`;
  `fetch_layer_array` asks for 2 x `dim`. Without `max_px` it still returns the native crop.
- **Why:** the Amazon region (40° x 34°) built a 48,000 x 54,000 int16 mosaic and a float64 crop
  (~20 GB) before shrinking to 600 px, and the server died. Now 8 s and 128 MB peak.
- **Rejected:** keeping every k-th pixel (strided hyperslab read, 2.7 s) — it aliases: at k = 8
  it differs from the block mean by 13 m on average and up to 223 m.
- **Supersedes / superseded by:** —

### 2026-08-30 — DEM cache defaults live in exactly one module
- **Decision:** `map2stl/app/server/core/dem_cache.py::DEM_SETTING_DEFAULTS` is the only place DEM cache-key defaults are written. Clients send their DEM settings verbatim and the server fills gaps.
- **Why:** the session client had a fourth copy that disagreed on `maintain_dimensions`, so every settings-only city export missed a DEM that was on disk.
- **Rejected:** a local default list on any caller.
- **Supersedes / superseded by:** completes [one DEM cache key](#2026-08-26--the-dem-cache-key-has-exactly-one-definition).
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-27 — Rasterize at the pre-projection size, project once
- **Decision:** `/api/terrain/dem` reports `source_dimensions` (grid size before projection). Any layer that must line up with the DEM rasterizes at that size and lets the server project exactly once. The projected size is an output, never an input.
- **Why:** passing the already-projected width to the city raster applied the cosine factor twice (600 → 463 → 357 at Breckenridge): narrower and sheared.
- **Also:** the composite accumulator refuses a channel whose shape differs instead of reading past it (`+= weight * undefined` had turned the rest of the array into NaN).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry.

### 2026-08-27 — A source is available only if it has data, and an empty DEM is never cached
- **Decision:**
  - `/api/terrain/sources` computes availability from the tile count, never asserts it; the browser's select-reset rule then picks a working source.
  - An empty DEM is a failure, not a result, and is not written to the disk cache.
  - The tile-folder picker runs server-side (`tkinter.filedialog`); headless servers return 501 and the typed-path field is the fallback.
- **Why:** the missing-tile path returns zeros under HTTP 200, so a hard-coded `available: True` turned a broken config into a silent flat map; once cached, fixing the folder or adding a key appeared to change nothing. A browser cannot report an absolute path (`webkitdirectory` gives relative names), and `ocean_root` stores an absolute one.
- **Rejected:** a static source reorder; an in-page folder picker.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry.

### 2026-08-26 — The 11.9 s DEM load was two one-line bugs, not the cache design
- **Decision:** profile before redesigning. Fixes:
  - `geo2stl/dem.py` resolves the SRTM HDF5 path with the same project-relative fallback as `map2stl/app/server/config.py` (geo2stl may not import `app`, so the two must be kept in step by hand).
  - The raw OpenTopography GeoTIFF cache key (`map2stl/geo2stl/opentopo.py::fetch_opentopo_dem`) no longer includes `dim`, which never reaches the API.
- **Why:** `h5_srtm_available: true` was advertised while every request fell back to the network: 11.9 s → 0.27 s. Every resolution change re-downloaded a byte-identical file. Old `opentopo/*.tif` entries are orphaned (cache was 6.9 GB; a sweep is worth scheduling).
- **Rejected:** a resolution-ladder cache with resampling — dropped once the real causes were measured.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-26 entry.

### 2026-08-26 — The DEM cache key has exactly one definition
- **Decision:** `map2stl/app/server/core/dem_cache.py::dem_cache_key` (with `normalize_dem_settings`) is called by both the terrain router (writer) and `map2stl/app/server/core/export_params.py::resolve_dem` (reader).
  - Excluded from the key: `projection` and `clip_valid_region` — applied per request after the read, so the raw grid is cached once per bbox and toggling projection is free.
  - `DEM_CACHE_SCHEMA_VERSION` is bumped only when the meaning of a cached entry changes.
  - Client: `loadDEM()` snapshots the settings it requested into `appState.lastDemRequest`; `_demSettings()` sends that snapshot at export.
- **Why:** the hand-built reader key had drifted twice. Re-reading the form at export was a third way to miss (`highRes` overrides `dim`; controls can change between load and export). Verified: "DEM resolved from cache for export (key 0cbc4cdc, 599x600)".
- **Supersedes / superseded by:** supersedes the key-drift part of [client-orchestrated pipeline](architecture.md#2026-08-26--the-saved-location-render-pipeline-is-client-orchestrated-superseded); refined by F-DEMID (2026-09-27): `/api/terrain/dem` returns a `dem_id` (`map2stl/app/server/core/dem_store.py`) that every export sends, with the settings key as fallback.
- **Source:** decisions.md 2026-08-26 entry.

### 2026-07-18 — An empty DEM is flagged, not silent, and the server can report its own state
- **Decision:**
  - A failed local fetch still returns a usable array but tags the response `dem_empty` + `dem_warning` (`map2stl/app/server/routers/terrain.py::_dem_empty_warning`); the client shows a toast linking to the Keys modal.
  - `map2stl/app/server/core/export.py::generate_mesh_preview` distinguishes "no DEM cached" from "DEM is flat / no elevation data".
  - `GET /api/diagnostics` (`map2stl/app/server/routers/diagnostics.py`) plus a header button shows keys, sources, cache size and a coverage probe.
  - Saving the OpenTopography key (`map2stl/app/server/routers/auth.py::save_opentopo_key`) applies without a restart; the cache dir honours `MAP2STL_CACHE`.
  - The orphaned, throwing `generateModelFromTab` was deleted.
- **Why:** a region outside local coverage (Europa) loaded an all-zeros DEM and export failed with a generic 400.
- **Supersedes / superseded by:** —
- **Source:** [FIX-dem-coverage-diagnostics](../plans/done/FIX-dem-coverage-diagnostics.md).
