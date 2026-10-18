# Roofs and landmarks

Roof shape and pitch (classification, rise, geometry) and landmark detail (building parts,
per-building mesh/nDSM overrides, the Landmarks panel). Plan:
[F-LANDMARK](../plans/active/F-LANDMARK-roofs-and-building-parts.md). Related:
[building-heights.md](building-heights.md), [survey-lidar.md](survey-lidar.md). Research notebook:
[../research/roof-shape-research.md](../research/roof-shape-research.md); classifier reference:
[../reference/roof-shape-model.md](../reference/roof-shape-model.md).

### 2026-10-17 — Classified roofs are cached on disk per footprint, keyed by checkpoint hash + geometry + tags
- **Decision:** `city_data.py::_classify_buildings` looks each untagged building up in
  `<CACHE_ROOT>/roof_shapes.sqlite` (`city2stl/roof_cache.py`) and classifies only the misses; the
  key is sha1 of `roof_cache.VERSION`, the checkpoint's content hash (`model_id`), the GeoJSON
  geometry and the tags the model reads (`building`, `building:levels`, `height`). Only classified
  rows are stored. `classify_roof_shapes` batches the model's predictions (512) and reports
  `progress(done, total)`, shown as "Classifying roofs N of M" by the fetch and export tasks.
- **Why:** the in-memory memo (last 3 boxes) was lost on every restart and missed on any moved box;
  Granada took ~10 min cold. Per-footprint keys survive both. Measured on Granada: cold 124 s,
  warm 3.4 s, identical shapes. Profile of 600 buildings: crop features 3.3 s, tile decode
  1.6 s, per-row predict 1.3 s, so batching saves about a fifth and the cache does the rest.
- **Rejected:** keying by bbox (a moved box misses everything); caching skips (a missing tile may
  arrive later); speeding up `roof_features` appearance features (would change what the
  checkpoint was trained on); classifying in the background with flat roofs meanwhile (still
  possible later, see `issues.md`).
- **Supersedes / superseded by:** —
- **Source:** [F-DETAIL D2](../plans/active/F-DETAIL-survey-detail.md)

### 2026-10-17 — Reference landmark: the Alhambra raster is z-scaled isometrically (3.68 m/mm), not terrain-fitted (4.67)
- **Decision:** landmark kind `reference` (`city2stl.landmarks::reference_landmarks`) swaps the
  Alhambra footprints of `exp4_merge/rule_table.csv` (22 swapped labels in 18 units, 11 flagged,
  never deleted) for a stored height-above-ground raster built from the Thingiverse STL (thing
  5141408, mamutazos, CC BY-SA) by `tools/landmarks/build_reference_raster.py`. The STL z is scaled
  by the 4x4's isotropic **3.683 m/mm**. The builder renders both scales, scores each against the
  REDIAM lidar nDSM (median |delta| over the swapped footprints, 1 cell eroded, tree-free) and keeps
  the lower: **isotropic 2.367 m (18 of 19 labels improved), terrain-fit 4.671 m/mm 3.927 m (14
  of 19)**. Off by default; off or outside the raster's bbox the model is unchanged.
- **Why:** the terrain-fit scale made the raster too tall; the 4x4's own scale already matches the
  lidar. Heights come from the study's ground-subtracted grid (`thing_resid_mm`, scale
  independent): my own `estimate_dtm` re-run gave 3.25 m because it put the Alcazaba ground 0.8 mm
  low. Checked on the stored raster alone (no merged-model terrain base): 21 of 22 swapped labels
  beat OSM (only Torre del Criado, 275, is level: 2.98 vs 2.92 m), median 2.45 m, against method B's
  22 of 22 and 2.37 m on the merged model. Tops follow the local terrain (`local_ground`), not
  the footprint's highest ground, since the Alhambra sits on a slope.
- **Rejected:** terrain-fit scale (above); re-deriving the ground with `estimate_dtm` (3.25 m);
  shipping the 3MF or STL (the raster is 0.07 MB, tracked, no gitignore line).
- **Supersedes / superseded by:** —
- **Source:** [F-DETAIL D4](../plans/active/F-DETAIL-survey-detail.md)

### 2026-10-17 — Untagged roofs are classified on read, after the survey fill; `roof_shapes: "off"` is today's output
- **Decision:** `city_data.with_roof_shapes` runs `roof_classifier.classify_roof_shapes` on the
  `buildings` layer in `get_city_layers`, after `with_survey_heights`, when `roof_shapes` is `"auto"`
  (default; a bool maps true to auto, false to off). It uses the trained checkpoint
  (`use_model=True`, no satellite image: the model fetches its own disk-cached zoom-18 tiles), so
  a bbox with no checkpoint or no tiles skips quietly with `reason` in the stats. An OSM
  `roof:shape` is never overwritten; classified features carry `roof_source`
  (`roof_model_gbm`) and `roof_confidence`. Stats (`classified`, `unchanged`, `skipped`,
  `by_shape`, `reason`) sit beside `survey_fill` in the payload and in `report.json`.
  Applied on read, last results kept in memory per bbox and footprint set: no cache write, no
  `CITY_PIPELINE_VERSION` bump. `"off"` returns the payload untouched (classifier never imported),
  proved byte-identical by `tests/test_roof_shapes_server.py`. `merge_layer_data` keeps the
  server's classified roof fields unless the client feature has its own `roof:shape`. The quick
  view (`city-quickview.js::buildingPrisms`) draws gabled (ridge on the long axis) and hipped /
  pyramidal (apex at the centroid, convex outlines) roofs, `roof:height` else 30 % of the height
  capped at 50 %, as `city_model._roof`.
- **Why:** F-DETAIL D2; the user chose "auto, off switch in Settings" (2026-10-17). The checkpoint
  is the only classifier measured above always-flat (see the 2026-10 entries below); the signal
  cascade needs a city-wide image whose resolution cannot show a roof.
- **Rejected:** classifying inside the OSM cache (stale after every classifier change; would need
  a version bump) -- on read is cheap once tiles are cached. Passing the survey nDSM to the
  cascade -- the cascade was measured at its majority baseline.
- **Supersedes / superseded by:** —
- **Source:** [F-DETAIL D2](../plans/active/F-DETAIL-survey-detail.md)

### 2026-10-14 — Wall and fortification polygons print as their outline ring, in ground-following pieces
- **Decision:** `walls` and `fortifications` polygons (`city2stl/city_model.py::RING_LAYERS`)
  become their outline ring, buffered by the layer's line width, in
  `city2stl/city_model.py::feature_polygons`; `towers` polygons stay filled. Both layers'
  footprints, rings and line walls alike, are then cut into `WALL_PIECE_MM` (2 mm) grid pieces
  (`city2stl/city_model.py::split_on_ground`) in `build_layer` and `layer_preflight`, each
  extruded on the ground under it; pieces keep the whole wall's height and slenderness cap.
  Both constants are in `_polygon_constants`, so polygon and solid caches change with them.
  `drop_building_copies` is unchanged: a fortification polygon that copies a building is
  still dropped before the ring is made.
- **Why:** Granada's Alhambra precinct is one fortification polygon. Filled, it printed as one
  slab 63 m above the lowest ground under it (51 m of relief), burying 84 of the 85 buildings
  inside and 24 of 25 towers. A ring alone still stood 52 m tall, because an extruded footprint's
  top is the highest ground under the whole footprint plus its height. With ring plus pieces the
  worst piece stands 18 m above its own ground; 83 of 85 buildings and 23 of 25 towers stand
  clear. User choice: "Outline ring only".
- **Rejected:** keep the polygon filled and lower it (still a slab over the buildings); ring
  without pieces (52 m wall on a slope); pieces for every extruded layer (a building's flat roof
  must stay one flat top).
- **Supersedes / superseded by:** —
- **Source:** Granada cache `cache/osm/d30b834cb6bcc839289b3c717601da32`; tests
  `tests/test_city_model.py::TestWallRings`.

### 2026-10-14 — Named, historic and tower buildings are not merged into their neighbours
- **Decision:** `city2stl/heights.py::_reduce_buildings` skips any building for which
  `city2stl/heights.py::keeps_own_footprint` is true (`name`, `historic` or `tower:type` set, or
  `man_made=tower`, or `building=tower`): it keeps its own geometry, `osm_id`, tags and height.
  Unnamed touching buildings of the same rounded height still merge. `CITY_PIPELINE_VERSION`
  went 4 to 5 (`city2stl/cache_policy.py`), with 4 in `BUILDINGS_ONLY_STALE_VERSIONS`, so a
  cached city refetches only its buildings layer.
- **Why:** in Granada 26 touching 10 m halls and towers of the Nasrid palaces (Mexuar, Torre de
  Comares, Baños Reales, ...) were dissolved into one polygon carrying "Palacio de Carlos V"'s
  id and tags, so landmark overrides and heights could not reach them. User choice: "Keep named
  separate".
- **Rejected:** merging only buildings of identical tags (still loses the osm_id of all but
  one); no merging at all (unnamed rows of terraced houses are the reason the merge exists).
- **Supersedes / superseded by:** —
- **Source:** tests `tests/test_enhance_heights.py::TestReduceBuildingsKeepsLandmarks`.

### 2026-10-14 — A "height" landmark override rescales the OSM building to a known top height
- **Decision:** override kind `"height"` (`{"kind": "height", "height_m", "source"?}`,
  `city2stl/landmarks.py::OVERRIDE_KINDS`) keeps the OSM building and its parts as drawn and
  multiplies every member's `height_m`, `min_height` (from `building:min_level` if needed) and
  numeric `roof:height` by `height_m` / the tallest member's height
  (`city2stl/landmarks.py::scale_heights`, applied in `LandmarkPlan.take`). Members are those
  `take` already groups: features with the `osm_id` plus features lying ≥ 50 % inside. The
  footprint is registered like a replaced one, so `towers` / `churches` / `fortifications` copies
  inside it are dropped (those layers carry no `osm_id`, so they cannot be matched by id; the
  2026-10-13 copy rule below already drops most of them). `height_m` must be > 0 and < 1000 m
  (`override_height_m`), checked on save (`app/server/core/landmarks.py::validate_spec`), in
  `resolve_overrides` and in `coerce_overrides`. The Landmarks panel offers it as "Height" with a
  metres field and an optional source note.
- **Why:** OSM tags most Alhambra towers `height=10` (Torre de Comares is ~45 m from its outer
  base, Torre de la Vela ~27 m); the user chose landmark overrides to fix this per region.
  Scaling keeps gabled/pyramidal roofs and part layouts in proportion and makes the given value
  the building's top, which is what published heights describe. The built top scales exactly
  by the factor (tests compare against the plain OSM build, since the roof code does not put a
  pyramid apex at full `height_m`).
- **Rejected:** flat top at `height_m` dropping roof tags — loses the roof shapes OSM has and
  flattens part layouts; setting only the outline's height — parts keep their old heights and
  show through or float; the Buildings panel's per-index height edits
  (`modules/layers/building-heights.js::buildingsWithOverrides`) — keyed by feature index, lost on
  refetch, not stored per region, one feature without its parts.
- **Supersedes / superseded by:** —

### 2026-10-13 — Churches, towers and fortifications drop the outlines the buildings layer already has
- **Decision:** `city2stl/city_model.py::build_on_terrain` drops a `churches`, `towers` or
  `fortifications` polygon that lies at least `REPLACE_OVERLAP` (50 %) inside the buildings
  layer's footprints (`drop_building_copies`, index from `building_cover`), when the buildings
  layer is on. The report counts them as `building_copies_dropped`. Outline leftovers between
  building parts are kept (`assemble_parts` unchanged).
- **Why:** those queries return the same outlines as the buildings layer, without
  `building:part` data, so each became one flat block at the outline's height. Granada
  Cathedral printed as a single 45 m prism over its 41 parts (16.5–50 m). On Granada's cache
  the rule drops 46 of 46 churches, 40 of 43 towers and 8 of 10 fortifications; every church
  has a building outline with full tags under it. The user asked to keep the outline remainder
  between parts.
- **Rejected:** drop the outline remainder in `assemble_parts` (user: keep leftovers);
  rebuilding the footprint index per layer (22.6 s on Granada vs 2.9 s once).
- **Supersedes / superseded by:** —
- **Source:** [Extrude audit](../history/audits/AUDIT-2026-10-13-extrude.md)

### 2026-09-27 — A roof that cannot be closed falls back to flat at mid-roof, never fails the build
- **Decision:** `city2stl/city_model.py::_roofed` catches Triangle errors from
  `city2stl/roofs.py::building_solids` (and any non-manifold roof solid) and emits a flat prism at
  mid-roof height.
- **Why:** seen on a plain pyramidal rectangle at preview scale; one bad roof must not fail a city.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 F-LANDMARK §3-§5 entry (last bullet).

### 2026-09-27 — Landmarks are a Settings panel with a true-scale preview through the real build
- **Decision:** `CityLandmarksSection.vue` (Settings → Fetch, under Fetch Layers) lists notable
  buildings (place of worship, town hall, castle, attraction, tallest N) with part count, roof shapes
  and height source; per landmark choose OSM parts / nDSM / uploaded mesh. The preview renders the
  landmark alone at true scale, without the slenderness cap, through `city2stl/city_model.py::build_on_terrain`;
  saved overrides go out with the City Model build as `landmark_overrides`.
- **Why:** the preview must show what the export will build, so it uses the same builder rather than
  a separate renderer.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 F-LANDMARK §3-§5 entry; plan §5.

### 2026-09-27 — Landmark overrides are keyed by OSM id, stored in their own table, and resolved before the build
- **Decision:**
  - buildings carry `osm_id` (`way/123`) plus `name/building/amenity/historic/tourism`
    (`CITY_PIPELINE_VERSION` 4 in `city2stl/cache_policy.py`; a v3 OSM cache re-fetches only its
    buildings layer). A dissolved group keeps its largest member's id, so overriding that id replaces
    the merged footprint;
  - overrides live in table `region_landmarks` (`app/server/core/landmarks.py`), not in `region_settings`;
  - all I/O (mesh load + manifold check, nDSM fetch for footprint + 5 m) happens in
    `city2stl/landmarks.py::resolve_overrides` before geometry; a bad file or survey outage fails the
    export naming the landmark. A geometry-time failure (e.g. < 30 % nDSM coverage) keeps the OSM
    building and is reported (`landmarks: {osm_id: {status}}`);
  - `city2stl/landmarks.py::LandmarkPlan` removes the overridden building, its parts, and any other
    extrude-layer feature ≥ 50 % inside it (e.g. the `churches` copy).
- **Why:** every panel save replaces the `region_settings` blob wholesale and would silently drop
  overrides; resolving I/O first makes failures early and attributable.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 F-LANDMARK §3-§5 entry; plan §3.

### 2026-09-27 — A placed landmark mesh is scaled vertically by the geometric mean of its horizontal stretches
- **Decision:** mesh placement aligns the min-rotated-rectangle long axis to the footprint's
  (per-axis `fit=rectangle` or uniform), picks 0°/180° by overlap, then applies manual
  rotation/scale/offset (`city2stl/landmarks.py::fit_mesh_xy`). Vertical `true` = mesh proportions at the
  geometric mean of the two horizontal stretches; anything stricter needs `vertical=fit` + `height_m`
  (OSM height by default). glTF is Y-up. Base on the footprint's highest ground, skirt to the lowest.
- **Why:** a per-axis fit has no single vertical scale; the geometric mean preserves volume-like
  proportions without guessing which axis is right.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 F-LANDMARK §3-§5 entry; plan §3.

### 2026-09-27 — Roof geometry is exact planar facets over convex pieces, and building parts are their own solids
- **Decision:** `city2stl/roofs.py` replaces the old mesh.py roof generator: a pitched roof over a
  convex footprint is the lower envelope of one plane per eave edge (all edges = hipped, ridge edges
  only = gabled with vertical gable ends); concave footprints are split into convex pieces
  (Hertel-Mehlhorn) roofed at the building's slope; dome/onion/cone/round are surfaces of revolution over
  the largest inscribed circle. Where `building:part`s exist, each part is its own solid from
  `min_height` (or `building:min_level` × 3.2 m) with its own roof; the outline contributes only the
  uncovered area.
- **Why:** the old generator produced flat tops for gabled/hipped on any convex footprint (no ridge
  vertices were added); cathedral towers, spires, domes and apses can then come from OSM tags alone.
- **Rejected:** straight-skeleton roofs (planned) — robustness risk on messy OSM outlines; the
  convex-split envelope gives exact planar facets instead.
- **Supersedes / superseded by:** —
- **Source:** [F-LANDMARK plan](../plans/active/F-LANDMARK-roofs-and-building-parts.md) "Where it stands", §1-§2; code docstring.

### 2026-08-30 — Whole-city roof classification prefetches tiles
- **Decision:** `city2stl/roof_tiles.py::prefetch_bbox` warms the zoom-18 tile cache concurrently;
  `city2stl/roof_classifier.py::classify_roof_shapes` calls it whenever a checkpoint is loaded. A failed
  prefetch is logged and ignored (`city2stl/roof_tiles.py::crop_for_ring` fetches what is missing).
- **Why:** the serial per-building path managed ~4 tiles/min against a few hundred tiles under a 3 km box.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Roof geometry will not be fitted against the plates
- **Decision:** the default roof rise stays 0.3 × building height (now `city2stl/city_model.py::_roof`,
  when `roof:height` is untagged). It is not defended by evidence; nothing we have can measure it.
- **Why:** roof rise moves plate error by 0.02-0.4 m against a 2.9-11.3 m floor; three of eight cities do
  not move at all; a leave-one-city-out critic disagrees with the plate's optimum in 8 of 8
  ([plate-critic](../research/plate-critic.md)).
- **Rejected:** the plate critic as referee — AUC on plate-vs-extrusion is a renderer signature (0.999
  with the roof signal pinned at the sigmoid floor). Do not reopen without a per-building reference at
  about 1 m/px.
- **Supersedes / superseded by:** supersedes [Granada ridge from plates](#2026-08-29--granadas-ridge-height-comes-from-the-spanish-plates-not-a-fixed-angle-superseded) for the shipped rise.
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Roof pitch keeps one global tilt correction and per-region rise curves; the 0.3 rule stays
- **Decision:**
  - one global tilt correction (gain 1.0, floor 8.5°), not per city;
  - rise curves per region (iberian, central_european, french, north_american); only
    central_european has representative tagged truth, so it sets the amplitude and the others inherit
    it; french and north_american are labelled degenerate placeholders;
  - a fitted cap that runs to the edge of its search grid is not a cap: consumers clamp at
    `rise_at_fit_max_m`, and `cap_identified` is written into the model file;
  - do not replace `0.3 × height_m` with the regional width curve;
  - keep `flat` as the untagged default; the fix for flat extrusions is to classify roofs.
- **Why:**
  - per-city constants disagreed 2.6×: `roof:height` is a landmark tag, each city's tagged subset is
    skewed differently (Lisbon's 7 pitched: median half-width 13.6 m vs 5.8 m stock; Paris's 12 are
    pavilions and spires). A calibration that fits a biased sample looks earned and is worse than none;
  - on 39 roofs with both tags, 0.3 × height wins median |error| (1.00 vs 1.80 m) and within-1 m hit
    rate (51 vs 38 %); span rules win correlation (0.50 vs 0.43). Neither is strong enough to act on;
  - pitched share differs by city (classifier: Miami 19.6 %, Valencia 70.4 %), so no default fits all.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 "Roof pitch calibration and the export path".

### 2026-08-30 — Roof classification is opt-in at export, resolved lazily [superseded]
- **Decision (then):** `export_city_3mf(classify_roofs=False)`; `generate_city_3mf` imported lazily in
  the endpoint because `server.py` imported the cities router before putting `Code/` on `sys.path`.
- **Why (then):** classification costs a satellite fetch and changes geometry, so it should be asked for.
- **Supersedes / superseded by:** superseded by the F-ARCH vector city model (every export goes
  through `city2stl/city_model.py::build_on_terrain`; mesh.py and `export_city_3mf` are gone). The roof classifier is SDK-only
  (`city2stl/roof_classifier.py::classify_roof_shapes`); see [F-ARCH](../plans/active/F-ARCH-consolidation.md).
- **Source:** decisions.md 2026-08-30 "Roof pitch calibration and the export path" (last two paragraphs).

### 2026-08-29 — Roof shape is learned from OSM roof shape tags plus zoom-18 imagery, not from heights, plates or STLs
- **Decision:** supervise flat-vs-pitched with real `roof:shape` tags and per-building imagery at
  native zoom 18 (~0.5 m/px); the trained checkpoint is `city2stl/roof_model.py` (reference:
  [roof-shape-model](../reference/roof-shape-model.md)).
- **Why:**
  - an OSM height is one scalar; shape needs `roof:shape`. Cartagena has zero `roof:shape` tags;
  - the hand-written `city2stl/roof_classifier.py` cascade scored exactly the majority rate (Miami RGB:
    121/122 pyramidal; never says flat);
  - Granada: 640 real tags, five folds grouped by imagery window: footprint shape + imagery with
    gradient boosting AUC 0.913, balanced accuracy 0.783 (shape alone 0.833, imagery alone 0.839);
  - at 2 m/px a median 9 × 10 m building is 4 × 5 px — resolution, not the model, was binding;
  - on a DSM, read roof slope from a plane fit, not the p10-p90 spread (clutter/parapets inflate it).
- **Rejected:** see [Rejected hypotheses](#rejected-hypotheses) below.
- **Supersedes / superseded by:** —
- **Source:** claude/memory-bank/shadow_heights.md lines 478-787 → [../research/roof-shape-research.md](../research/roof-shape-research.md).

### 2026-08-29 — Granada's ridge height comes from the Spanish plates, not a fixed angle [superseded]
- **Decision (then):** replace the 26° / 6 m-cap hip with a two-parameter rise curve fitted on 7,400
  Barcelona and Valencia roofs at 0.5 m/px; rise = `tan(slope_med − 8.5°) × half_width`, applied
  pointwise to the union distance transform (distance from wall = local half-width).
- **Why:** the 8.5° is what roofs tagged flat still measure on the mesh; validated on Prague's 21 tagged
  `roof:height` roofs (26-33° vs 28.9°). The union transform lets one ridge run along a terrace
  instead of a pyramid per house.
- **Rejected:** rise from relief (does not track tagged rise once footprint size is removed); keeping
  26° (one window read without floor correction); a per-city gain (Prague vs Salzburg differ 2×).
- **Open then:** fitted 13-15° is 1.7× shallower on wide blocks than the Granada 3D Tiles read
  (width-binned pitch 24-29° over 1,647 footprints).
- **Supersedes / superseded by:** superseded for the shipped rise by
  [Roof geometry will not be fitted against the plates](#2026-08-30--roof-geometry-will-not-be-fitted-against-the-plates);
  the tilt floor and unity gain carried into
  [the global tilt correction](#2026-08-30--roof-pitch-keeps-one-global-tilt-correction-and-per-region-rise-curves-the-03-rule-stays).
- **Source:** decisions.md 2026-08-29 entry.

## Rejected hypotheses

### 2026-08-29 — The STL plates or source STLs carry roof shape
- **Hypothesis:** the vendor plates (continuous meshes, 1.3-2.2 m spread in 3×3 windows) or Granada's
  source STLs could teach roof pitch.
- **Measured:** plane-fit slope in tagged footprints separates pitched from flat at AUC 0.596
  (Salzburg) and 0.528 (Prague); flat-tagged roofs already read 9-12° — the warp smearing neighbours
  over a 4-px footprint. `Granada_buildings.stl`: 100 % of upward area within 2° of horizontal
  (flat extrusion); `Granada_buildings2.stl`'s sloped faces are hillside (only 3 % of upward area > 1.5
  units above ground, median tilt 7.9°).
- **Verdict:** refused. The source city models are footprint extrusions; re-cutting finer would never
  produce a roof. Nothing in the Granada roof layer depends on a plate.

### 2026-08-29 — Roof redness transfers as a pitch cue across cities
- **Hypothesis:** CIELAB a* (clay tile ⇒ pitched) is a general cue.
- **Measured:** AUC 0.958 on 42 hand labels in Cartagena, 0.724 on 640 real tags in Granada, where
  roofs are terracotta whether flat or pitched. Geometric cues (ridge contrast 0.494, gradient
  coherence at majority rate) failed even at 0.59 m/px.
- **Verdict:** refused as a general rule — a fact about Cartagena's stock; use it only as one feature.

### 2026-08-29 — The Google mesh can make the per-building flat-vs-pitched call
- **Hypothesis:** per-building pitch from the Granada 3D Tiles mesh (1.52 m/px) can referee the classifier.
- **Measured:** AUC 0.488 vs the classifier (0.470 on well-fitted footprints); only nine tagged roofs in
  the probe box; a footprint is ~10 px across. A mesh-set-angle layer changed roof mean to 1.42 m
  (+4.8 %) with no evidence of improvement.
- **Verdict:** refused. The mesh sets the city's pitch angle by width-binned aggregation; the
  classifier keeps the per-building call.
