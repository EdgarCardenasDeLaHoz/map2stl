# Projections and raster conventions

Raster orientation, map-projection order and area measurement. Related: [architecture.md](architecture.md) (row 0 = north), [terrain-dem.md](terrain-dem.md) (project once).

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
