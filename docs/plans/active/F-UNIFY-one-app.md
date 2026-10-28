# F-UNIFY: one app, one architecture, panorama heights in reconstruction

_Approved 2026-10-24 (user; mockups approved 2026-10-26). Status: Phase 0 done 2026-10-28, Phase 1
in progress (steps 1-6 done). Proposal: [F-UNIFY](../../proposals.md). Mockups: `Code/claude/mockups/2026-10-24-unify/`._

## Goal

Every tool runs inside the map2stl app on one backend pattern (router → core → library, one job
runner, one cache/HTTP layer) and one frontend stack (Vue + Pinia). The skyline/panorama pipeline
and the registration tools feed building heights into the city model automatically.

## User decisions (2026-10-24)

- Tools that become app pages: **skyline runs, photo label / camera solve, manual plate align,
  benchmark & eval** (all four).
- Panorama heights: **automatic when a report covers the region, with one switch** to turn them
  off. They never override tags, survey, LiDAR, landmarks or user overrides (`panorama_heights.KEEP_SOURCES`).
- Cleanup depth: **full restructure** (shared core, dedupe, file splits, legacy JS → Vue,
  skyline package restructure).
- Frontend: **Vue only** for new pages.

## Phase 0: one clean master (done 2026-10-28)

- TM's photo-label / F-PHOTOVIEW / onedrive_check work committed and pushed as b22ca85, a89a9f3,
  d239a29 (master d239a29, pre-push 2998 passed). The OneDrive working tree was reset to it.
- Conflict copies: 76 `*-DESKTOP-*` copies outside `cache/` and `runs/` archived (moved, not
  deleted) to `data/archive/conflict-copies-2026-10-24/` (git-ignored, `MANIFEST.txt`). The
  TMJM22C canopy / survey_fill / city_model copies were Oct 17-18 drafts that 071ab34 and later
  commits superseded, so nothing was ported. Copies inside `cache/` and `runs/` and
  `data-DESKTOP-*.db` stay until the user decides.
- Branches: the 14 dead local branches and the `map2stl-merge` worktree are gone. The `res` and
  `skybench` worktrees stay: `skybench/cache` holds ~2 GB of survey / 3D Tiles rasters that are
  not in the main cache.

## Phase 1: shared backend core (no behaviour change)

1. **One job runner**: `app/server/core/jobs.py`.
   - Job id, state, progress, message, result, error, TTL sweep, cancel flag.
   - One `/api/jobs/{id}` poll endpoint, plus SSE if cheap.
   - Port `ExportTask`, `CityFetchTask` and `PlateTask` onto it, keeping the old endpoints
     as thin aliases until the client moves.
2. **One HTTP/Overpass client.**
   - `geo2stl/osm.py` already has mirror health, DNS pin and backoff. Route every Overpass
     caller through it:
     - `registration/osm_water.py`, `osm_model.py`, `street_place.py`;
     - `osm_raster.py`, `skyline/benchmark.py`, `19_refresh_survey_truth.py`;
     - `geo2stl/landmarks.py`, `trails.py`;
     - `tools/align_tool/locate.py`, `tools/photo_label`.
3. **Geo primitives in one place** (`geo2stl/geo.py`) **Done 2026-10-28**: constants, `lonlat_to_local_m`; BBox and quadkey kept as they are (decision `architecture.md`).
   - `M_PER_DEG_*` is re-declared or inlined in about 55 places; replace them all.
   - One local metric projection (`skyline/_core/projection.lonlat_to_local_m` moves down).
   - One lon/lat→pixel helper and one tile/quadkey helper.
   - One `BBox` type, shared by the skyline `RegionBBox`, the app validation and align_tool tuples.
4. **One OSM water/coast builder.** **Closed 2026-10-28, no change**: the three do different jobs (decision `architecture.md`). Original note:
   - `skyline/osm_water.py`;
   - `registration/osm_water.py`;
   - `geo2stl/water_layers.py`.

   Keep the `geo2stl` one and make the other two use it.
5. **One rasteriser.** **Closed 2026-10-28, no change**: everything already burns through `burn_polygons`; the wrappers differ only in policy (decision `architecture.md`).
   - `city2stl/rasterize.rasterize_city_data` is the home.
   - Fold in:
     - `osm_raster._rasterize_buildings`;
     - `routers/composite._rasterize_city`;
     - align_tool's `plate_vectors.polygons_to_heightmap`.
6. **Atomic writes and locks.** **Done 2026-10-28.** One `atomic_write_json` / `file_lock` in `geo2stl/cache.py`;
   `skyline/benchmark` uses it.
7. **Dead code out.** **Done 2026-10-28** (decision `architecture.md`, "Dead code removed in F-UNIFY step 7"). Kept: `roads.py`, `maintain_dimensions`, F-SKY5 (on), the F-SKY12 / 11.1 flags, the pre-sidecar OSM probe, F-SKY17 (live default).
   - The `shadow_height` provider (deprecated 2026-08-28) and `city2stl/roads.py` (legacy).
   - The legacy request aliases (`mm_per_px`, `fit_height_mm`, `base_mm`, `clip_nans`,
     `maintain_dimensions`), after checking `data.db` presets and the client for users.
   - Legacy cache-migration paths, if no cache entry still needs them (read-only check;
     `cache/` itself is untouched).
   - `skyline/scripts/demos/`: move to `docs/history` or delete; they are research probes.
   - Off and failed skyline features: F-SKY5, 12, 11.1, 14 and 17, `sat_joint`,
     triangulation, `lowrise_prior`. Delete their code paths and env switches, and record
     each in decisions. The lever is a shorter `SKYLINE_*` env list.

## Phase 2: skyline package as a service

- **Config.** One `SkylineConfig` dataclass, built from site JSON + env + request, replaces
  the scattered `os.environ` reads. Env vars stay as overrides.
- **Library entry.** `city2stl.skyline.run_region(bbox | region, config, progress_cb) ->
  RegionResult`, with heights rows, report paths and cost counters. `region_pdf.py` and
  `08_region_skyline_pdf.py` become thin wrappers over it.
- **Reports.**
  - One report renderer base, shared by the skyline HTML, the benchmark report, the
    height-diagnostic report, the landing page and the numpy2stl registration report.
  - `report_index` reads a JSON sidecar instead of re-parsing `index.html`.
- **Splitting large files.**
  - Split by responsibility, keeping the import paths behind the existing façades:
    - `_pano/detect.py` (1.9k lines);
    - `html_report.py` (1.9k);
    - `benchmark.py` (1.7k);
    - `elevated.py` (1.6k);
    - `_pano_plots.py` (1.3k);
    - `_core/height.py` (1.2k);
    - `_pages.py` (1.2k).
  - The same applies to `city_model.py` (2.0k), `routers/terrain.py` (1.7k) and
    `app/session/terrain_session.py` (2.6k).
- **Commons photo pipeline.** Scripts 13–17 become one library module. `17_photo_pipeline`'s
  own 500 m rival rule moves onto `skyline_match.gate_margin`.

## Phase 3: registration family

- **Camera solve.**
  - One `camera_solver` API, used by the skyline photo path and by photo_label.
  - photo_label's `terrain_solve.solve_pose` / `register.solve_pose` merge into
    `camera_solver` / `skyline_match`.
- **Plate registration.**
  - One pipeline: `numpy2stl.registration`, plus `city2stl/registration` for the geo side.
  - align_tool's `locate*`, `refine_guess`, `auto_register` and `eval_registration` merge
    into it or are deleted where they duplicate it.
  - Tests for `city2stl/registration` (3.4k lines, untested today).

## Phase 4: app pages (Vue; mockups `png/01-05` approved by the user 2026-10-26)

Per the appendix, power tools stay out of the Beginner view. They live under a **Tools** menu in
the header, as pages in the same app shell, with the same header, region pill and job runner.

| Page | Main job | Type | Backend |
|---|---|---|---|
| Heights (Street View) | Measure building heights for the selected region from panoramas and see them on the map | Console | `POST /api/skyline/runs` (job), `GET /api/skyline/runs/{id}`, report files |
| Photo solve | Place a photo on the map and read building heights from it | Designer | `routers/photo.py` (ports photo_label server), Google 3D proxy server-side |
| Plate align | Line a printed or scanned plate up with the map | Designer | the existing `routers/registration.py`, plus manual drag, ECC refine and refetch from align_tool |
| Benchmark | Score a region's heights against LiDAR / 3D Tiles truth | Manager | `POST /api/benchmark/score` (job), cached truth only unless the user OKs fetches |

- **Paid fetches.** Every paid Street View / 3D Tiles fetch shows the count and the free-tier
  share before it starts (Street View cap: 10k per month; 3D Tiles: 1k roots), and runs only
  after an OK. Offline cache-only is the default.
- **GPU.** Runs queue behind `city2stl.resources`' GPU lock, and the job card shows "waiting
  for GPU".

## Phase 5: panorama heights in reconstruction

- `panorama_heights.find_reports(bbox)`: auto-discover the skyline reports whose region covers
  the bbox (the `heights.json` footprints intersect it).
- Apply them in:
  - `city_data.get_city_layers`, so they reach the preview, the city overlay and the export;
  - not only `city_model_task`, as today.
- Existing rules stay: tiers corroborated/single, IoU ≥ 0.5 and `KEEP_SOURCES`.
- UI:
  - Extrude → City section: a "Street View heights" switch (on) with "312 buildings from 2
    reports".
  - Building hover and the legend show the height source.
- Photo-solve readings and plate-align results write into the same height store, with
  `height_source = photo_*` and their own tier.

## Phase 6: legacy JS → Vue (full restructure)

- Migrate the 68 `modules/**` files (22k lines) page by page, behind the existing
  `window.appState` bridge:
  1. Explore;
  2. Edit layers, i.e. `stacked-layers`, `composite-dem`, `city-overlay`;
  3. Extrude (`model-viewer.js`, 1.9k lines).
- The bridge is removed at the end. `app.css` (3.4k lines) splits into component styles.
- Each page migrates only after a screenshot comparison against the current build (no capability lost).

## Order and gates

- 0 → 1 → 2/3, in parallel worktrees: they touch different packages.
- 4 needs the mockups approved and 1's job runner.
- 5 needs 2's `run_region` and the report sidecar.
- 6 is last, page by page.
- Each step:
  - lands as its own commit;
  - keeps the full suite green;
  - runs `graphify update .`;
  - records decisions in `docs/decisions/` at the same time.
