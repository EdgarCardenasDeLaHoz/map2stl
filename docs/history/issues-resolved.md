# Resolved issues — map2stl (history)

Moved out of [../issues.md](../issues.md) on 2026-09-28 so the live tracker stays short.
Kept for the reasoning: each entry says what broke, why, and how it was fixed.
Section numbers are the ones `issues.md` used; §3 and §0c stay summarised there
because code cites them.

- Newest first within each group; dates are when the fix landed.
- Paths are as they were at the time; modules have moved since (e.g.
  `city2stl/skyline/height/` → `city2stl/height/`, `app/server/core/height/` →
  `city2stl/height/`).

## Fixed 2026-10 (end-to-end run, Amazon + Philadelphia)

### 0g. Lake outlines fetched twice; continent-sized lakes query; city overlay froze the page — fixed 2026-10-02
- **Lakes fetched twice** (Granada, Philadelphia): two concurrent misses. Fixed with a lock per
  cache key in `city2stl/fetch.py::fetch_osm_lakes` (re-checks the cache inside the lock).
- **Amazon Extrude never finished:** the OSM lakes query asked Overpass for a continent.
  `geo2stl/water_layers.py::make_lakes_source` now skips boxes over `LAKES_MAX_AREA_KM2`
  (10 000 km²); open water there comes from the ESA layer. Amazon Extrude: >10 min → 13 s.
- **Dead mirror retried first:** `geo2stl/osm.py::mark_overpass_failure` puts a mirror that
  failed a real query last for `FAILURE_MEMORY_S` (10 min). Philadelphia Extrude 175 s → 9 s
  (with the lock).
- **Philadelphia Edit froze the page** (58 k cached buildings, 100 s of main-thread blocking in
  2 min): see `docs/decisions/frontend.md` (city overlay worker cache). Blocking 100 s → 8 s.
- Tests: `tests/test_osm_fetch_performance.py`.

### 0h. Edit terrain stayed empty for the Amazon until a layer changed — fixed 2026-10-03
- Every DEM is coloured in `workers/dem-render-worker.js`; its reply
  (`app/client/static/js/modules/dem/dem-main.js::_onDemWorkerMessage`) filled the canvas but
  never redrew the Edit stack, which copies it. Philadelphia looked right only because its
  city data arrived later and triggered a redraw; the Amazon (no city data) showed gridlines
  only. The handler now calls `emitStackUpdate`: terrain drawn 1.7 s after the DEM.

## Fixed 2026-08 to 2026-09 (were under "Active Bugs")

### 1. City raster endpoint 500 — NaN values in JSON response — fixed 2026-05-26
Listed as open until 2026-09-28, but it is the same defect as
"City raster NaN JSON crash" under Resolved technical debt below.


### 2. `/api/composite/hydrology-merge` raised on every call — fixed 2026-09-06
`app/server/routers/composite.py` did
`from app.server.core.hydrology import merge_rivers_with_dem`, but
`app/server/core/hydrology.py` does not exist — the function lives at
`geo2stl/hydrology.py:976`. The import is inside the handler, so the module
loaded fine and the route only failed when it was called. Found 2026-08-27 while
building the trails layer; fixed during F-COMPOSITE3 pass 1 by importing from
`geo2stl.hydrology`. Still uncovered by tests, which is why it went unnoticed
for a fortnight.

### 2b. City-raster cache key ignored the detail tier — fixed 2026-09-06
`/api/composite/city-raster` keyed its cached raster on (bbox, width, height)
only, so a coarse request and a full request over the same bbox collided: the
first one to run decided what the second one got back, and the coarse tier drops
small buildings. `detail` is now part of the key
(`_city_raster_arrays`, `app/server/routers/composite.py:245`). Found while
registering the OSM channels as composite layer sources.

### 2c. Extrude preview mesh built twice — fixed 2026-09-26
- Every `renderDEMCanvas` (recolour, rescale, a layer finishing) calls the auto-rebuild, as
  does entering the Extrude view; each POSTed the same `/api/export/preview` body, so the
  server built the same ~700k-face mesh again.
- `previewModelIn3D` now keys each request on its serialised body and skips one that is in
  flight or already shown (`_createPreviewGate`, `modules/export/model-viewer.js`;
  test `tests/js/previewGate.test.js`).

### 2d. `read_array_cache failed (...): [Errno 22] Invalid argument` — fixed 2026-09-26
- Cause: `cache/` lives in OneDrive, which dehydrated old entries into cloud-only
  placeholders (`RECALL_ON_DATA_ACCESS | OFFLINE`); opening one from a process OneDrive
  will not hydrate for raises Errno 22. Reproduced on 3 of 6 `cache/wsf3d` entries.
- `read_array_cache` detects placeholders, logs, deletes the pair and misses, so the
  re-fetch writes a local copy. Array and OSM cache writes are now temp-file + rename, and
  the `.npz` handle is closed after reading (it blocked replacement on Windows).
- Lasting fix is outside the code: point `MAP2STL_CACHE` outside OneDrive, or mark
  `cache/` "Always keep on this device".

### 2e. US cities got no building heights — fixed 2026-09-26
- `enhance_city_data` returned early for US bboxes ("osm_only_us"), a guard from before
  the resolution limit existed. Breckenridge, CO: 3,788 / 3,973 buildings at 10 m.
- `lidar_3dep` is not lidar (COP30 − SRTM, 30 m) and the limit rightly refuses it;
  OpenTopography only serves 3DEP bare-earth DTMs. Real 3DEP heights now come from the
  COPC point clouds on Planetary Computer, measured per footprint
  (`city2stl/skyline/height/providers/lidar_3dep_copc.py`), before the raster providers.
- Breckenridge: default share 95.3 % → 1.0 % (3,686 lidar, 63 raster), ~66 s. Against its
  185 OSM-tagged buildings: lidar MAE 1.98 m / corr +0.876, GBA + Open Buildings
  2.13 m / +0.666.
- Coarse providers are now skipped before download instead of after (~2 min saved there).


### 3. Raster height enhancement makes buildings shorter than the fallback it replaces — fixed 2026-08-30
`enhance_city_data` (`app/server/core/height/service.py:276`) runs automatically whenever a
city has default-height buildings, and on every European city measured it makes the model
worse. Measured against the eight vendor plates, reverting the enhanced heights to the
plain 10 m fallback wins in **7 of 7** cities that have enhanced buildings:

| city | enhanced share of built cells | applied | reverted to 10 m |
|---|---|---|---|
| bilbao_spain | 70.5 % | 6.22 m | **2.85 m** |
| valencia_spain | 56.5 % | 8.18 m | **4.62 m** |
| lisbon_portugal | 32.4 % | 4.49 m | **3.40 m** |
| paris_france | 27.6 % | 4.85 m | **3.75 m** |
| salzburg_austria | 27.2 % | 4.03 m | **3.24 m** |
| prague_czech_republic | 11.3 % | 3.92 m | 3.69 m |
| barcelona_spain | 8.1 % | 4.87 m | 4.90 m |

(median absolute height error against the plate; Barcelona and Prague are won by the city's
median OSM height rather than by 10 m, but never by the enhancement.)

**Root cause: the merge ranks providers by nominal quality and ignores resolution.**
`merge_height_rasters` documents that "higher-confidence pixels always win", and confidence
is a flat per-provider constant. `NDSMProvider` is registered at confidence 0.80 /
resolution 30 m and `OpenBuildingsProvider` at 0.60 / 5 m, so nDSM wins every pixel where
both have data — which is most of them, since nDSM covers about 84 % of the grid. A 30 m
normalised DSM averages each building with the streets and courtyards around it, so its
value at a building is not that building's height. Measured over the cached rasters:

| provider | median | p90 | share of pixels ≤ 3 m |
|---|---|---|---|
| ndsm | 2.00 m | 6.86 m | 58–72 % |
| open_buildings | 9.35–24.00 m | 15–33 m | 3–15 % |

Those sub-3 m values then hit the `max(3.0, ...)` clamp in
`enhance_buildings_with_raster` (`city2stl/heights.py:299`), so roughly half to two thirds
of enhanced buildings come out at exactly 3.0 m — replacing a 10 m box with a 3 m one. The
clamp is not the bug; it is what makes the bug visible in the output.

US bboxes were unaffected at the time: `enhance_city_data` skipped them
(`source_name: "osm_only_us"`). That skip was removed 2026-09-26; see 2e.

#### Fix (2026-08-30), in two parts

**1. The merge now ranks on confidence scaled by cell size.**
`resolution_priority(res) = min(1, (BUILDING_SCALE_M / res) ** 0.5)` with
`BUILDING_SCALE_M = 5.0` (`city2stl/skyline/height/__init__.py`). Square root rather than
the raw ratio, so a coarse source is demoted without being erased — it is still better than
no data on a pixel nothing else covers. Effective priorities: copernicus 0.495,
roofnet 0.65, open_buildings 0.60, lidar_3dep 0.335, ndsm 0.327, wsf3d 0.118, ghsl 0.089.
The emitted confidence raster stays *unscaled*, so `enhance_buildings_with_raster`'s
`min_confidence` gate keeps the meaning it was tuned against.

**2. Per-building enhancement now refuses sources coarser than 10 m.**
`BUILDING_RESOLUTION_LIMIT_M = 10.0` keeps the dedicated building-height products (Overture
at 5 m, Copernicus EU at 10 m) and drops the general-purpose elevation differences. A city
covered only by coarse sources gets no enhancement and keeps the 10 m fallback;
`payload["height_enhancement"]["providers_too_coarse"]` records what was dropped so this
reads as a decision rather than a silent no-op. Merging itself is *not* gated — a coarse
source is still the best available whole-city raster.

**Why part 1 alone was not enough.** Re-ranking can only help if there is something finer to
promote, and mostly there is not:

| city | ndsm cover / median | open_buildings cover / median |
|---|---|---|
| barcelona_spain | 100.0 % / 0.00 m | 51.8 % / 18.08 m |
| bilbao_spain | 100.0 % / 2.59 m | 33.3 % / 16.59 m |
| lisbon_portugal | 100.0 % / 0.00 m | 20.3 % / 15.00 m |
| paris_france | 100.0 % / 1.21 m | 60.9 % / 18.00 m |
| prague_czech_republic | 100.0 % / 1.44 m | 51.0 % / 12.00 m |
| salzburg_austria | 100.0 % / 0.91 m | 32.8 % / 9.35 m |
| valencia_spain | 100.0 % / 1.91 m | 34.0 % / 24.00 m |

**Result.** Median absolute error against the plates, whole raster:

| city | applied (pre-fix) | re-ranked only | re-ranked + gated | reverted to 10 m |
|---|---|---|---|---|
| barcelona_spain | 4.87 | 4.74 | **4.72** | 4.78 |
| bilbao_spain | 6.22 | 6.19 | 5.01 | **2.87** |
| lisbon_portugal | 4.49 | 4.47 | 3.42 | **3.40** |
| paris_france | 4.85 | 4.13 | **3.54** | 3.67 |
| prague_czech_republic | 3.92 | 3.86 | 3.66 | **3.57** |
| salzburg_austria | 4.03 | 3.60 | 3.21 | **3.20** |
| valencia_spain | 8.18 | 8.13 | 4.82 | **4.66** |

The gated fix beats current behaviour in all 7 cities. Reverting entirely still wins 5 of 7
on L1, but four of those margins are ≤ 0.16 m; only Bilbao is a real gap.

**Why the enhancement was kept rather than switched off.** Median absolute error is the wrong
referee for that question. Plate-to-generated correlation is about 0.43, and at that
correlation a constant beats an unbiased noisy estimate on L1 by construction — the constant
sits at the middle of the distribution and the estimate does not. Over the merged cells only:

| city | cells | r, gated | r, re-ranked only | best shrinkage k |
|---|---|---|---|---|
| barcelona_spain | 3615 | 0.44 | 0.31 | 0.90 |
| bilbao_spain | 30595 | 0.13 | 0.09 | 0.00 |
| lisbon_portugal | 13593 | 0.09 | 0.07 | 0.00 |
| paris_france | 13540 | 0.34 | 0.34 | 0.70 |
| prague_czech_republic | 5360 | 0.21 | 0.20 | 0.60 |
| salzburg_austria | 5304 | 0.24 | −0.02 | 0.35 |
| valencia_spain | 30626 | 0.02 | 0.03 | 0.10 |

Gating raises correlation everywhere it changes anything, which is what says the gated
estimate carries signal a constant happens to beat on L1. No global shrinkage was added:
best k spans 0.00–0.90 across seven cities, and [research/plate-critic.md](../research/plate-critic.md) already documents
that the plates are unreliable referees — fitting one constant on that spread would be
overfitting to a reference we have called untrustworthy in writing.

**The sidecar/registry resolution disagreement was not a data error.** `ndsm.py` and
`lidar_3dep.py` reported the *output grid spacing* (`lat_span * 111000 / dim`, about 5.86 m
for a 3 km box at 512 cells) while the registry reported the *source product* resolution
(30 m). Sampling a 30 m difference onto a 512-cell grid does not make it finer. Both
providers now report their product resolution as a module constant, and
`_cache.read_height_result` ignores the sidecar's `resolution_m` entirely — every sidecar
written before 2026-08-30 holds the old value and would have kept the old merge ordering
alive on cache hits. The registry's `lidar_3dep` entry was also corrected from 0.95 / 1 m
(wrong, taken from the name) to 0.82 / 30 m, which is what its own module always said.

**Every non-US city exported before 2026-08-30 carries the defect**, and the enhancement is
persisted into the OSM cache, so a re-export needs those cache entries invalidated first.


### 4. An exhausted Overpass mirror list can return an empty city instead of an error — fixed 2026-09-06
Fix: see "An Overpass outage was indistinguishable from an empty city" under Recently closed below.
Found 2026-08-30 in the `gen_city.py --all` batch. One outage between 17:14 and 17:15 took all
three mirrors down at once — `overpass-api.de` with its usual 406 on `/status`,
`overpass.kumi.systems` with a 502 and then a read timeout, `maps.mail.ru` with a read
timeout — and two cities in the batch hit it. They did not fail the same way:

| City | Response | Correct? |
|---|---|---|
| Palermo | HTTP 500, "No Overpass endpoint answered its /status probe" | yes |
| Naples | **HTTP 200 with zero buildings** | no |

Naples logged `Overpass ... returned no usable data for buildings; 0 mirror(s) left` and the
handler then returned success anyway. Only the batch script's own guard
(`raise RuntimeError("no buildings returned")`) noticed. Through the API an exhausted mirror
list is indistinguishable from a genuinely empty rural bbox, so a caller that trusts the
status code silently exports a city with no buildings in it.

Neither failure is a property of those cities: Bologna, the third Italian bbox, built normally
17 minutes later. This is the same class as the align track's partial water fetch degrading
registration without saying so. An exhausted mirror list must raise; only a successful query
that genuinely matched nothing may return empty.


## Technical debt resolved 2026-08 to 2026-09

### 0. Dead code in `city2stl/buildings.py` — RESOLVED 2026-09-01
Found while checking whether the module's `building:levels * 3.9` contradicted `heights.py`'s
`METRES_PER_LEVEL = 3.2`. It did not, because the code holding it was unreachable — but the
file read as live, so the contradiction was a trap for the next reader. Recorded here rather
than deleted silently, because a future reader may go looking for these names.

Six of the module's seven functions had no importer anywhere in the tree, and each would have
raised immediately against the installed dependencies:

| Function | Fails on | Installed |
|---|---|---|
| `building_to_gdf` (held the 3.9) | `ox.footprints_from_polygon` | osmnx 2.1.0, removed |
| `building_to_polygons` | `np.float` | numpy 1.26.4, removed |
| `building_heights` | `np.float` | numpy 1.26.4, removed |
| `draw_building_patches`, `building_to_patches`, `draw_patches` | `descartes` | not installed |

All six were removed, along with the module's `osmnx`, `pandas` and `matplotlib` imports.
`roads.py` carried a lazy-import comment blaming `descartes` "required by city2stl.buildings";
that comment was corrected in the same change.

`get_polygons` was kept — it is the only function with an importer, `city2stl/create.py:29`.
Note that create.py does not itself import in this environment either (`numpy2stl` is not
installed, create.py:33), and `mesh.py:12` records it as superseded by the current mesh path;
nothing outside `notebooks/` imports it. So `get_polygons` is live only in the sense that a
live-looking import statement names it. It was left alone rather than chased, since removing
it means deciding the fate of create.py and the notebook mesh path — a larger question than
this cleanup.

2026-09-27 (F-ARCH): `create.py` had since been deleted, leaving `get_polygons` with no importer;
`city2stl/buildings.py` was deleted. `notebooks/granada.ipynb` still calls `get_polygons` through
`notebooks/experimental.py`, which imports the missing `city2stl.create` and so already fails.

`heights.py`'s `METRES_PER_LEVEL = 3.2` is now the only levels-to-metres conversion in the
tree.


### 0d. Google 3D Tiles provider was returning an empty raster — fixed 2026-08-28
`height/providers/google_3d.py` reported `valid=0` for every bbox. Six
independent defects, each hiding the next; all fixed 2026-08-28:

1. Child URIs are root-absolute, and were concatenated onto the parent path,
   duplicating the `/v1/3dtiles` prefix. Every request 404'd into a
   `logger.debug`.
2. `box` bounding volumes were tested by converting only the oriented box's
   centre to lon/lat. Google's root box is centred on the Earth's centre, so
   the test rejected every city in the Americas.
3. `uri.endswith(".json")` is False for `...json?session=...`, so routing
   nodes were handed to the glTF loader.
4. `urljoin` drops the parent's query string, so the session token Google
   issues at the root never reached the geometry requests.
5. The DSM was built by ray-casting 262 144 rays through trimesh's numpy
   fallback. It raised with an empty message, and the resulting all-NaN
   raster was written to the cache, so later runs returned it in 0.0 s.
6. Reading `scene.geometry` discards the glTF scene graph that carries the
   ECEF placement, and trimesh's Y-up to Z-up rotation must not be applied to
   tiles that are already in ECEF. Miami's geometry arrived correctly shaped
   at 70 E, 63 N.

A second round on 2026-08-28 made it usable rather than merely non-empty:

7. The fetch held every tile mesh in memory at once, because
   `ThreadPoolExecutor.map` submits all tasks immediately and yields in
   order. A 4000-tile run died in trimesh's cache hashing. Tiles are now
   downloaded a chunk at a time and each is binned and dropped
   (`_accumulate_mesh`), so peak memory follows the chunk, not the budget.
8. Binning only mesh vertices assumed vertex spacing finer than the output
   cell. True at the finest levels, false at the coarse ones, whose triangles
   span many cells -- so a coarse fetch filled 19% of the grid. Points are now
   sampled across each face in proportion to area (`_surface_points`), taking
   the same fetch to 99%.
9. The target geometric error was fixed at 4 m, chosen from the tiles'
   resolution rather than from what a 5.9 m output cell can hold. Descending
   that far is expensive and *worse*: finer tiles are smaller, so a fixed
   budget covers less ground. Measured over Miami — 32 m target, 28 s, 99%
   filled, MAE 14.01; 8 m target, 240 s, 82% filled, MAE 14.72. The target is
   now derived from the output cell (`_target_error_m`).

The walk was also made level-order and the downloads pooled, which together
took a Miami fetch from 581 s to 23 s at better coverage.

One limit remains by design: the raster is WGS84 ellipsoidal altitude turned
into height by estimating ground from the mesh itself (`_ground_from_dsm`),
which assumes terrain gentler than the buildings are tall. That holds in the
coastal cities this work targets and would fail in hills, where a DEM plus a
geoid model is the honest answer. A caller may pass a DEM to override it.


## Resolved Bugs

### ~~`export_city_3mf` could never find its own DEM in the cache~~ ✅ (2026-08-30)
`TerrainSession.fetch_dem` sends `settings["dem"]` verbatim, and that dict carries no
`maintain_dimensions` key, so the server stored the array under its canonical default of
`False`. `export_city_3mf` then rebuilt the DEM block field by field with a fourth copy of
the defaults, and its copy said `maintain_dimensions: True`. The two sides hashed different
cache keys and every settings-only city export answered
`400 DEM not found in cache — load DEM first` on a DEM that was sitting on disk.

This is the third recurrence of the drift `core/dem_cache.py` was written to end; the
session client was simply a copy that module did not know about. Fixed by sending
`"dem": dict(s)` and letting the server fill any gap from `DEM_SETTING_DEFAULTS`.
`projection` and `clip_nans` went with the hand-built block: neither is part of the key and
the route never reads either from `req.dem`.


### ~~Building heights were measured from the wrong ground datum~~ ✅ (2026-08-28)
`camera_elev_m` is metres above sea level, but `BuildingRecord.terrain_elev_m` defaulted to
a 0.0 placeholder that the region pipeline never filled. The pinhole math
(`cam_elev + cam_height + forward*tan(angle) - ground_elev`) then added the whole camera
elevation to every height. At sea level, where this pipeline was developed, the error is a
couple of metres. In Denver it is +1600 m, which also pushed every candidate outside the
plausibility gates, so elevated regions silently produced *no* heights at all — confirmed by
temporarily restoring the old behaviour and watching 5 of 7 datum tests fail, one of them
because zero estimates survived at 250 m.

Fixed by adding `BuildingRecord.terrain_known` and `_core.height._ground_elev_m`: measured
terrain is used when it exists, otherwise the building is assumed to stand on the camera's
own ground plane, which cancels the camera elevation out. `region_data._fetch_elevations_opt`
now returns `None` rather than 0.0 on API failure so the two cases stay distinguishable.
Regression tests in `tests/test_skyline_height_datum.py`.

### ~~The geometric y-gate had the terrain term inverted~~ ✅ (2026-08-28)
The gate inverted the height equation for the ray angle but subtracted `ground_z` where the
algebra adds it, double-counting the ground offset and narrowing the plausibility window by
`2 x terrain_elev` on any sloped site. `_core/height.py`.

### ~~The depth cross-check ignored camera pitch and terrain~~ ✅ (2026-08-28)
`depth_height_from_segment` measured the ray angle from the optical axis rather than the
horizon, so every pitched view (seed capture tilts up for tall buildings) under-read, and the
F-SKY12 disagreement flag fired for reasons unrelated to either estimate being wrong. It also
had no way to express a camera/building ground offset. Now takes `pitch_rad` and
`ground_offset_m`; `_pano/detect.py` supplies the per-building offsets.

### ~~Depth calibration and sampling both read the sky~~ ✅ (2026-08-28)
F-SKY12 anchored its relative-to-metric fit at the silhouette-top pixel and equated the depth
there with `forward_m`. The silhouette top is the last building pixel before sky, so DA2
routinely reports the far background there — regressing background depth against near
footprint distances mis-scaled the entire map. The same pixel was then used for the distance
in the height itself. Anchors now sit on the horizon row (where a z-depth genuinely is the
horizontal ground distance), and `depth_height_from_segment` takes an optional
`depth_sample_xy` so the angle comes from the roof pixel while the distance comes from the
facade. `tests/test_skyline_height_datum.py::TestDepthCalibrationAnchors`.

### ~~`_fill_heights` could never reach its `default_m`~~ ✅ (2026-08-28)
`city2stl/heights.py` filled a missing `building:levels` with an invented 3.0 before
multiplying, so untagged buildings silently became 3 levels tall and the `default_m`
parameter was dead. Levels are now multiplied by a shared `METRES_PER_LEVEL = 3.2` (which
also removes a 4.0-vs-3.2 inconsistency with the skyline floor-period estimator) and
untagged buildings fall through to `default_m`.


### ~~Graticule froze during pan and zoom whenever the DEM layer was off~~ ✅ (2026-08-28)
`drawLayerGrid` bailed out on `demCanvas.width === 0 || demCanvas.height === 0`,
reading `#layerDemCanvas`. That buffer is only sized while the `Dem` layer is
active, so viewing Sat, Trails, or any other layer on its own made the guard
return every time. `_applyGridCSSDelta` kept running regardless, so the grid
became a stale bitmap being CSS-translated and scaled under the gesture: the
lines drifted out of position, their spacing no longer matched the zoom, and the
axis labels never updated. The guard now requires only a bounding box, and the
container extent comes from the stack rect — which is what every layer buffer is
sized to anyway, so the numbers are unchanged when the DEM *is* drawn.

### ~~Trails layer engraved piste areas as solid blobs~~ ✅ (2026-08-28)
`rasterize_trails` buffered Polygon and MultiPolygon features directly. OSM maps
many pistes and every ski-area boundary as a closed way, so buffering the
polygon painted its whole interior: the Breckenridge overlay came back as large
solid cyan masses instead of runs, and the same fill would have engraved the
mountain face in the exported STL. Areal features are now reduced to their
boundary before buffering, so they engrave as linework of the requested width.
Their interiors are returned separately as `ski_area_grid` / `hiking_area_grid`,
0/1 masks that the client tints at low alpha in the category colour. The masks
are display only and never reach the DEM.

### ~~Settings panel left a 200px gap when collapsed~~ ✅ (2026-08-28)
`.dem-right-panel.settings-collapsed` set `width: 0` but inherited
`min-width: 200px` from the expanded rule, and any panel that had been dragged,
restored from `localStorage`, or clamped by `_ensureDemViewportSpace` also
carried an inline width that outranks a stylesheet declaration. The contents
were hidden but the box stayed. The collapsed rule now zeroes `min-width`, and
`toggleSettingsPanel` stashes the inline width on collapse and restores it on
expand. The resize handle is hidden while collapsed.

### ~~Composite DEM rendered blank or sheared when a projection was active~~ ✅ (2026-08-27)
The city raster was being projected twice. `/api/composite/city-raster`
rasterizes at the requested size and *then* applies the projection, but
`composite-dem.js` was sending the DEM's **post**-projection dimensions as the
requested size. At Breckenridge's latitude the cosine factor is ~0.771, so a
600-wide grid projected to 463, and feeding 463 back projected it again to 357:
the raster came back narrower than the DEM and its contents were squeezed
relative to the terrain underneath.

The blankness was a second defect stacked on the first.
`_addWeightedFeature()` looped to the composite's length regardless of the
channel's length; reading past the end of the short city channel yielded
`undefined`, and `+= weight * undefined` turned the rest of the composite into
`NaN`, which renders as nothing rather than as an error.

Fixed in three places:
- `/api/terrain/dem` now reports `source_dimensions` — the pre-projection grid
  size — and `dem-main.js` stores it as `lastDemData.sourceDimensions`.
- `composite-dem.js` requests the city raster at that pre-projection size, so a
  single projection lands it exactly on the DEM grid, and nearest-neighbour
  resamples any residual mismatch rather than trusting the sizes to agree.
- `_addWeightedFeature()` refuses a length-mismatched channel and warns instead
  of silently poisoning the composite with `NaN`.

Any layer that must align with the DEM has to follow the same rule: rasterize at
`source_dimensions` and project once.

### ~~Disabling a DEM source did not stop it being selected~~ ✅ (2026-08-27)
Marking the unavailable `local` option `disabled` was assumed to push the select
onto the first working source. It does not: `disabled` blocks the *user* from
choosing an option, never a programmatic assignment or the browser's default. So
after `populateDemSources()` rebuilt the list, the select sat on index 0 —
`local`, disabled and empty — and every fetch came back flat, exactly the symptom
the disabling was meant to cure. Two paths set it there:

- `dem-main.js:populateDemSources()` cleared the options and only restored the
  previous value when it was still selectable, leaving the browser's index-0
  default in place otherwise. It now selects the first enabled option explicitly.
- `presets.js` restored `dem_source` from saved region settings with a bare
  `el.value = val`. Saved settings outlive the data behind them — Breckenridge
  had `"dem_source": "local"` stored — so loading a region re-pinned the dead
  source. It now checks the option is selectable, falls back to the first that
  is, and logs which substitution it made.

### ~~`h5_local` returned terrain from the wrong place in the tile~~ ✅ (2026-08-27)
`geo2stl/dem.py:fetch_h5_dem()` transposed the assembled mosaic before cropping
it. The HDF5 datasets are already stored row=lat (north at row 0), col=lon, and
the mosaic loop assembles them the same way, so the transpose swapped the pixel
axes and read `data[px, py]` instead of `data[py, px]`. Because a 5° tile is
still terrain everywhere, the result looked like a plausible landscape rather
than an error: Breckenridge came back as 1745–2029 m instead of its true
2860–4210 m, an 1100 m offset with the relief flattened to a quarter of its real
range. Every `h5_local` render and export since the function was written was
wrong in this way. Fixed by dropping the transpose; `h5_local` now returns
2861–4195 m for that bbox against SRTMGL1's 2860–4207 m (the spread is the
expected 90 m vs 30 m sampling difference).

### ~~An empty DEM was written to the disk cache~~ ✅ (2026-08-27)
`/api/terrain/dem` cached whatever it produced, including the array of zeros a
source returns when it has no coverage. Fixing the underlying cause — repointing
the tile folder, adding an API key — then appeared to do nothing, because the
flat map was served from cache on every subsequent request. The write is now
skipped when `_dem_empty_warning()` fires. `DEM_CACHE_SCHEMA_VERSION` was raised
to 3 at the same time, so entries written under the transposed `h5_local`
orientation are ignored rather than misread.

### ~~The `local` DEM source reported itself available with no tiles on disk~~ ✅ (2026-08-27)
`ocean_root` in `config.json` pointed at a folder that no longer exists, so
`geo2stl.tiles.get_tile_files()` returned nothing, `stitch_tiles_no_rasterio`
returned `None`, and the caller substituted an array of zeros. The request
succeeded with HTTP 200 and the only symptom was a perfectly flat map — and,
if exported, a flat slab. Both `/api/terrain/sources` and `/api/diagnostics`
hardcoded `"available": True` for this source, so the dropdown kept it
selectable and selected. Availability is now computed from the tile count in
`app/server/core/tile_store.py`; the option is disabled and labelled
"(unavailable)", which makes the browser select the first working source
instead. The Keys panel gained a "Local SRTM tiles" block for repointing the
folder — typed, or chosen with a Browse… button that raises a native dialog on
the server (`POST /api/auth/tile-store/browse`), since a browser cannot report an
absolute path.

### ~~Saving an OpenTopography key wrote to a file nothing reads~~ ✅ (2026-08-27)
`routers/auth.py` built its config path with one `.parent` too few, resolving to
`app/config.json` instead of `map2stl/config.json`. The key was applied to the
running process by `_apply_opentopo_key`, so the panel reported success and
downloads worked — until the next restart, when the key was gone again. Both the
key route and the new tile-store route now share `tile_store.CONFIG_PATH`.

### ~~renderCombinedView() assumed DEM and water mask always share a pixel count~~ ✅ (2026-07-19)
`water-mask.js`'s `renderCombinedView()` (DEM+water overlay view) indexes
both decoded arrays by the same flat index, requiring identical dimensions.
This was true only as an accidental side effect of `maintain_dimensions`
defaulting to `True` everywhere (F-PROJ-DIMS's prior default) — the DEM and
water-mask endpoints derive their output resolution through genuinely
different algorithms (DEM resizes to `dim` on its longest axis; water-mask
derives an Earth-Engine metres-per-pixel scale from `dim`), so once
`maintain_dimensions` defaulted to `False` they could produce very different
absolute pixel counts (e.g. DEM `387×474` vs. water `483×591` for the same
bbox) while still sharing the same aspect ratio. The existing "fix" (reload
water mask on mismatch) was a no-op — reloading re-derives the same
mismatched shape from the same algorithm. Found via manual exploration of
the app after the F-PROJ-DIMS default flip. Fixed by nearest-neighbour
resampling the water mask onto the DEM's exact grid before the per-pixel
loop (`_resampleNearest` helper). Regression test:
`tests/e2e/test_interactions.py::test_combined_view_resamples_mismatched_water_mask`.

## Resolved Technical Debt

### ~~Git line-ending churn (CRLF) — renormalization pending~~ ✅ (2026-07-26)
`.gitattributes` (`* text=auto eol=lf` + per-type rules; `.bat/.cmd/.ps1` keep
CRLF) was added 2026-06-07, and the index is now fully normalized: `git
ls-files --eol` reports **0 files stored as CRLF**, and a clean tree no longer
shows the ~95-file phantom diff. No `git add --renormalize` commit is needed.

### ~~stl_to_heightmap always returned an all-NaN heightmap~~ ✅ (2026-07-19)
`city2stl/skyline/height/stl_import.py:stl_to_heightmap` accumulated per-pixel
max-Z hits with `np.maximum.at(heightmap, index_ray, zvals)` on an array
pre-filled with `np.nan` — `np.maximum(nan, x)` is always `nan`, so every hit
pixel stayed `nan` regardless of ray-cast success (silent 0% valid output on
every call). Existing tests didn't catch it because two assertions were
gated behind `if mask.any():`, which is vacuously true when the mask is
empty. Found while wiring F-MESHIMPORT (STL/OBJ import as a web-app layer,
[proposals.md](../proposals.md) F-MESHIMPORT). Fixed by initializing the
accumulator to `-inf` instead of `nan`, then converting untouched (`-inf`)
cells back to `nan` after the max-accumulate. Hardened
`tests/test_height/test_stl_import.py`'s two previously-vacuous assertions
to require `mask.any()` explicitly.

### ~~City raster NaN JSON crash~~ ✅ (2026-05-26)
`/api/cities/raster` crashed with a 500 error when a non-identity projection (e.g. "cosine", "lambert") was applied, because `project_grid` fills out-of-projection areas with `numpy.nan` and `grid.flatten().tolist()` produces Python `float('nan')` which is not valid JSON. Fixed by wrapping the grid with `np.nan_to_num(grid, nan=0.0)` before `.flatten().tolist()` at both the cache-hit and fresh-fetch code paths in `app/server/routers/cities.py`.

### ~~F-SKY11.1 Phase A (pano-level coastline alignment)~~ ✅ (2026-05-17)
Pano heading-offset recovery from stitched 360° pano + water mask vs coastline keypoints. Demo script `scripts/12_pano_coastline_demo.py` demonstrates approach; Cartagena seed_5 recovers 310° vs manual 320°. Phase B (integration into region_pdf.py) pending.

### ~~skyline script sprawl~~ ✅
Scripts `00`–`07` (individual-step runners) removed; single orchestration
entry point is now `scripts/08_region_skyline_pdf.py`. `review.py` removed
(logic merged into `region_pdf.py`). `pipeline.py` and `region_pdf.py` have
expanded module-level docstrings. `STATUS.md` added to capture current
accuracy numbers and known weaknesses. Architecture (post F-CLEAN14 full split,
2026-06-23): pure-math layer in `_core/` behind the `pipeline.py` façade +
`region_pdf.py` (I/O + rendering), with `_pano/`, `_report_plots/`, `_region_render/`
subpackages behind façades — all original import paths preserved.

### ~~app.js DOMContentLoaded Closure~~ ✅
`renderDEMCanvas` and `window.loadDEM` were extracted to `dem-main.js`. Closure vars (`lastDemData`, `originalDemValues`) moved to `window.appState`.

### ~~Closure-Only State Vars Not on appState~~ ✅
All ~20 closure-only vars (`boundingBox`, `drawnItems`, `coordinatesData`, `map`, `globeScene`, `sidebarState`, `waterOpacity`, `layerBboxes`, `layerStatus`, etc.) migrated to `window.appState`. Legacy `window.get*/set*` aliases kept for backward compat. Modules can now subscribe to changes via `window.appState.on('key', fn)`.

### ~~Inline onclick/onchange handlers~~ ✅
All non-intentional inline handlers removed. Last remaining: dev-only debug overlay dismiss button (intentional).

## Feature Status

| ID | Feature | Status |
|----|---------|--------|
| P1 | Physical dimensions panel | ✅ Done |
| P2 | Print-bed fit optimizer | ✅ Done |
| P3 | Contour lines in STL | ✅ Done |
| P4 | Base label engraving | ✅ Done |
| P5 | STL mesh repair (trimesh) | ✅ Done |
| P6 | Elevation band export (multi-material STL) | ❌ Denied (see [proposals.md](../proposals.md) F-P6) |
| P7 | Cross-section OBJ export | ✅ Done |
| P8 | Flat water surface cap | ✅ Done |
| P9 | Region label editor | ✅ Done |
| P10 | Curve undo/redo | ✅ Done |
| P11 | Region thumbnails | ✅ Done |
| P12 | Map quick-preview tooltips | ✅ Done |

## Recently Closed

### ~~An Overpass outage was indistinguishable from an empty city~~ ✅ (2026-09-06)

`city2stl/fetch.py` walked its healthy mirrors and then returned whatever the last one gave it,
including an empty FeatureCollection carrying an `error` key. Through `POST /api/cities` an
outage and a genuinely empty rural bbox produced the same HTTP 200 with zero buildings. In the
2026-08-30 batch Naples hit this and Palermo, on the same outage in the same minute, correctly
returned 500; only `gen_city.py:97`'s own check caught the difference.

Fixed 2026-09-06 in three parts.

1. `fetch_osm_data` raises `OverpassUpstreamError` when the mirror list is exhausted with a
   requested layer still failing. The router already answers 500 on an exception and already
   refuses to cache a result carrying an error, so an outage now fails like Palermo did.
2. Every layer fetcher separates osmnx's `InsufficientResponseError` -- which means the query
   matched nothing -- from a request failure. Only the latter gets an `error` key, so an honest
   empty region costs one mirror instead of three and never raises. Same distinction
   `geo2stl/trails.py:374` already drew for trails.
3. `_fetch_buildings` runs a `building` and a `building:part` query and merged them by reading
   `len()` on both, which assumed osmnx returns an empty frame when nothing matched. It raises
   instead (`osmnx/features.py:508`), so **any bbox with buildings but no `building:part` lost
   every footprint** to the layer's catch-all and was reported, cached and exported as empty.
   The queries are now split through `_features_or_none`.

Part 3 is a wider defect than the outage that surfaced it and is not restricted to mirror
failures: it would have emptied any bbox lacking `building:part`, and the caller could not
tell. The OSM cache was audited for it on the same day and is clean -- all 39 payloads under
`cache/osm/` carry buildings, none zero -- so nothing needs invalidating, but a cached city
with zero buildings remains the signature to look for. Nine tests in `tests/test_osm_fetch.py`, plus a live check on the two cases that must not be
confused: Granada returned 9 996 buildings with no error key, and a mid-Pacific bbox returned 0
buildings with no error key and no raise. Both ran with only `maps.mail.ru` alive, so failover
was exercised as well.

### ~~WSF3D reads a missing tile as "no settlements"~~ ✅ (2026-09-05)

`city2stl/skyline/height/providers/wsf3d.py` fetches one-degree tiles from
`download.geoservice.dlr.de/WSF3D/files/tiles/<name>/` and used to return an empty raster on 404. That
directory lists only **453** tiles, so most of the populated world 404s — Frankfurt
(`e008_n51_e009_n50`), Rotterdam, Panama, Miami and Dubai included — and in each of those cities
WSF3D was the only coarse global height product available. No current export changes, because a
90 m product never outranks a 3 m one under the merge policy, but the fallback was silently
unavailable exactly where it is meant to apply.

Fixed 2026-09-05. `providers/wsf3d_global.py` reads the global mosaic
(`files/global/WSF3D_V02_BuildingHeight.tif`, 2.1 GB, LZW-tiled, 86.58 m/px, Int16 x 0.1)
by HTTP range, and `wsf3d.py:226` serves a bbox from it whenever every tile 404s, cached
under the same namespace. It uses `tifffile` plus `requests` because GDAL cannot open that
URL on Windows here (schannel fails a revocation check on the DLR chain), and degrades to
the old empty raster if the decoders are absent or the read fails. Frankfurt now returns
245 099 finite cells at 86.6 m, p90 33.1 m, max 226.7 m in 21 s cold and 1.4 s cached;
Cartagena still comes from its published tile; mid-Pacific is still empty. Requires
`tifffile` and `imagecodecs`, both now in `requirements.txt`.


## Library Integration Debt ✅ ALL RESOLVED

`app/server/core/` is now a thin wrapper over `geo2stl` and `numpy2stl` as intended.
All five B-LIB items from [proposals.md](../proposals.md) are complete.
See [reference/overview.md](../reference/overview.md) for the current import map.

| ID | Summary | Severity | Status |
|----|---------|----------|--------|
| B-LIB1 | `cities_3d._terrain_mesh` → delegates to `numpy2stl.array_to_mesh` | High | ✅ Done |
| B-LIB2 | `cities_3d._extrude_ring`/`_ear_clip` → delegates to `numpy2stl.polygon_to_prism` | High | ✅ Done |
| B-LIB3 | `dem.py` legacy `proj_map_geo_to_2D` removed | Medium | ✅ Done |
| B-LIB4 | `sat.py` uses `geo2stl.sat2stl.calculate_scale_for_dimensions` | Medium | ✅ Done |
| B-LIB5 | `terrain.py` no longer imports `make_dem_image` directly | Small | ✅ Done |

## Completed Refactoring Milestones

- EXP-1 ✅ — Export progress indicator (spinner + `/api/export/status` polling)
- CLEAN-1 ✅ — Inline styles replaced with CSS classes across Vue components and JS modules
- UX-M ✅ — Lazy canvas allocation via `getOrCreateCanvas` + `_canvasRegistry` in `stacked-layers.js`
- UX-1 ✅ — Region creation consolidated to single `floatingDrawBtn` entry point
- REG-1 ✅ — Region list pagination (20 items/page + live search)
- REG-2 ✅ — Region import/export as JSON
- R-EVENTS-A ✅ — Event bus consolidation (`DEM_LOADED`, `COLORMAP_CHANGE`, `REGION_SELECTED`)
- P-PLANB-DEM ✅ — Off-thread DEM pixel rendering via `dem-render-worker.js`
- dim refactor ✅ — All terrain/water/ESA endpoints take `dim` (px); server computes `sat_scale` from bbox; `resolution_m` returned in responses
- IMP4 ✅ — dem-loader.js owns all DEM canvas helpers
- IMP5 ✅ — window.appState unified across modules
- ARCH1 ✅ — state.js Proxy appState + events.js event bus
- ARCH3 ✅ — api.js centralizes all fetch calls
- ARCH4 ✅ — Vite bundler installed (npm install + build verified, package.json + vite.config.js)
- FA2 ✅ — No duplicate functions between app.js and modules
- PERF6B ✅ — city-worker.js Web Worker for city overlay rendering; generation counter for stale-reply discard; sync fallback preserved
- Reactive bbox ✅ — N/S/E/W inputs update Leaflet rectangle live on every keystroke (no reload until Enter/button)
- Backend split ✅ — server.py + schemas.py + config.py + core/ + routers/
- SQLite migration ✅ — data.db with WAL mode
- Backend DEAD-1 ✅ — removed JSON fallback (~150 lines) from regions.py
- Backend REFACTOR-1–5 ✅ — split fetch_osm_data, merge _fill_heights, split _rasterize_city, extract H5 tile helpers, satellite tile math
- Backend EXTRACT-1 ✅ — fetch_water_mask extracted from terrain router to core/dem.py
- Backend DEAD-2/4 ✅ — removed unused dim param and local import math from terrain.py
- Frontend CLEAN-1–5 ✅ — regions.js: inline onclick, haversineDiagKm bug, AUTO_SCALE constants, globe marker colors, selectCoordinate JSDoc
- Frontend DEM-CLEAN-1–3 ✅ — dem-main.js: extracted _applyDemResult, moved progress bar/cancel/sat-unavailable inline styles to CSS
- CLOSURE-MIGRATE ✅ — All ~20 closure-only vars migrated to window.appState; legacy get*/set* aliases kept; no closure vars remain in app.js
- ARCH5 ✅ — Vitest 4.x installed; 58 JS unit tests across 5 test files (interpolateCurve, mapElevationToColor, detectContinent, haversineDiagKm, nicePixelInterval, niceGeoInterval); helpers in tests/js/helpers/; config in vite.config.js test block

Full history: [history/archive/functionality-history.md](archive/functionality-history.md)

## Completed Python / Session Milestones

- Session PEP8 ✅ — 51 PEP 8 violations fixed in terrain_session.py
- Session REFACTOR ✅ — 7 helper methods + 5 settings properties; ~150 lines removed (~8.3%); matplotlib consolidation; fetch method consolidation
- HYDRO-OPT ✅ — HydroRIVERS geometry simplification pipeline (collinear point reduction + shapely simplify + simplified cache)
- HYDRO-REGION ✅ — Region bounding box coverage fix (SA north to +15°N, NA south to -10°S); eliminated Central America gap
- HYDRO-CACHE ✅ — Simplified shapefile validation probe before trusting cached files
- HYDRO-RASTER-CACHE ✅ — Server-side disk cache of rasterized `river_grid` keyed on (bbox, dim, source, depression_m, scale_m, min_order, order_exponent, projection, clip_nans); 30-day TTL. Cuts repeat hydrology fetches from ~200 s to <50 ms.
- HYDRO-DEFAULT ✅ — SDK default source flipped from `natural_earth` to `hydrorivers` to match UI.
- HYDRO-VECTORIZE ✅ — Killed the `combined.to_json()` round-trip and replaced the per-feature simplify+buffer Python loop with vectorized GeoPandas/shapely-2.0 ops. **Why (do not re-litigate):** the JSON serialization existed only as an artifact of an old API boundary; the rasterizer immediately rebuilt shapely shapes from coordinate dicts. Vectorized GeoSeries.simplify/buffer is C-level and ~75× faster than the Python loop. **How to apply:** `fetch_hydrorivers` now returns a `GeoDataFrame` directly (not GeoJSON dict); `rasterize_hydrorivers` consumes the GDF. Also added `width_factor` (multiplier on per-line buffer) and `all_touched=True` in the rasterize call so thin rivers stay visible. Amazon cold dropped from ~270 s → ~17 s; Vermont from ~5 s → ~0.3 s.
- HYDRO-DEDUPE ✅ — Server-side in-flight dedupe via a `dict[cache_key, asyncio.Future]` in `terrain.py` so concurrent identical requests share one pipeline; client-side dedupe in `hydrology-overlay.js` returns the in-flight promise on duplicate calls and disables the Load button while running. **Why:** the disk cache only helps requests arriving *after* the first one completes; without dedupe, N parallel identical requests = N full pipelines (we observed 5× contention on Amazon-bbox loads). **How to apply:** clients re-firing `loadHydrology` with the same params now no-op until the first finishes; concurrent server requests likewise. **Rationale (do not re-litigate):** HydroRIVERS is the better default at *every* zoom we care about, not just city zoom. Two reasons baked into [`geo2stl/hydrology.py`](../../geo2stl/hydrology.py): (1) the HydroRIVERS rasterizer applies `min_buf_deg = pixel_deg * 0.6` so thin features stay ≥1 px wide and survive downsampling; the Natural Earth path has no such buffering, so tributaries alias away at continent zoom. (2) HydroRIVERS carries Strahler order 1–9 and scales depression depth as `base * (order/9)**exponent`, so major rivers carve deep while small tributaries still register. Natural Earth has neither. Natural Earth remains valid only as a fallback when (a) the HydroRIVERS regional shapefile isn't downloaded yet, or (b) the bbox spans regions HydroRIVERS doesn't cover (e.g. Antarctica).
- SRV-LIFECYCLE ✅ — start() reuses healthy server; _ensure_bbox() validates 4 keys; server wait timeout 60 attempts
- Bbox validation ✅ — Frontend rejects satellite/water requests for areas > 20°×20°
- PERF-RAF ✅ — RAF-gated curve drag in curve-editor.js
- MAP-2 ✅ — Keyboard accessibility for bbox drag handles
- UX-2 ✅ — Text labels on floating map buttons
- UX-3 ✅ — Clarified sidebar 3-state toggle
- ARCH4 ✅ — Vite bundler installed
- ARCH5 ✅ — Vitest 4.x installed; 58 JS unit tests across 5 test files
