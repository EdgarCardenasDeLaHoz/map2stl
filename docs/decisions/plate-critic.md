# Plate critic — what counts as evidence

What may be trusted when judging a plate, a placement or a height: ground truth that could have disagreed, references rebuilt from current data, and cross-checks between independent sources. Research notebook: [../research/plate-critic.md](../research/plate-critic.md).
Related: [registration-validation.md](registration-validation.md), [survey-lidar.md](survey-lidar.md), [building-heights.md](building-heights.md), [terrain-dem.md](terrain-dem.md).

### 2026-09-27 — The promoted critic keeps only the measured-error side
- **Decision:** `city2stl/registration/critic.py::score_model` reports measured error of a model against a reference (F-LANDMARK §6). The learned plate-vs-extrusion classifier from the research page is not promoted.
  - Metrics re-implemented from the page (the scratch code was lost): filled polygons, row 0 = south, tallest-roof metres anchor.
  - Roof error is reported but flagged unresolvable above 2 m cells (`critic.py::ROOF_RESOLVABLE_CELL_M`).
- **Why:** The classifier's score is monotone in relief — it measures how hilly a plate is, not whether it is right.
- **Rejected:** Promoting the learned classifier.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 entry (F-LANDMARK §6 part); [F-LANDMARK plan](../plans/active/F-LANDMARK-roofs-and-building-parts.md)

### 2026-09-04 — The high-rise mask is a real signal with no downstream consumer
- **Decision:** The high-rise detector is recorded, not shipped; the per-pixel height merge stays. The Cartagena router (detect tall buildings, take the tallest coarse provider for them) is dropped.
- **Why:** Validated on 20 cities, 40,819 OSM-tagged footprints. Pooled per-footprint MAE (short / tall / all):
  - pixel merge (current) 9.71 / 26.83 / 14.09
  - oracle router 9.71 / 26.45 / 14.00 — a *perfect* detector is worth 0.09 m
  - leave-one-city-out router 14.66 / 26.49 / 17.70 — the real one costs 3.6 m
  - The within-city config that gave 6.43 → 5.15 beats the merge in 1 of 20 cities: Cartagena, where the idea came from (discovery-city overfit).
  - The merge's tall-band error has no consistent sign (Makati -28.6 m … Miami +5.2 m), so the mask cannot pick a provider or carry a correction (mask-gated x1.07 helps 5 cities, hurts 15).
- **What survives:** the detector transfers — leave-one-city-out AUC median ~0.80 (0.44-0.95); strongest feature `halo_dark_p90` (darkness in a 25 m ring, 6°N to 52°N and 38°S). Fails only in Benidorm (nearly all towers). Next consumer would be a per-footprint height *regressor*, which is different work.
- **Rejected:** Mask-gated provider routing; mask-gated global correction; routing only where no fine provider covers (a no-op, 14.10 vs 14.09).
- **Supersedes / superseded by:** Supersedes the earlier "two-stage high-rise detector works, fitted per city" entry in [building-heights.md](building-heights.md#2026-09-04--a-two-stage-high-rise-router-works-only-fitted-per-city-and-is-not-landed) (the router approach; not shipping it still stands).
- **Source:** decisions.md 2026-09-04 entry "The high-rise mask is real and has nothing downstream to spend it"

### 2026-09-04 — A surveyed city is the only non-circular check
- **Decision:** Old San Juan's lidar survey is written into `data/san_juan_puerto_rico` in the same three stages as a printed pack, so every pack reader reads it too. It has no STL; mesh-based span/rotation checks skip it, raster readers do not.
- **Why:** Every earlier score compared the plate model against the render it was built from, so a systematically wrong terrain estimator would agree with itself. A survey nobody here produced can disagree.
  - Terrain estimator vs surveyed bare earth: 0.51 m mean at 20 m spacing, 0.18 m at 5 m — first evidence it is right, not just self-consistent.
  - Polygon round trip reproduces the surface to 0.92 m mean, 3.38 m p95, 2832 polygons.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry "A surveyed city is the only non-circular check we have"

### 2026-09-04 — A plate is read as a terrain surface plus building geometry
- **Decision:** The export also writes `stl_relief.npy` (render as it came off the mesh) and `stl_residual.npy` (ground removed, nothing discarded), with the Otsu threshold in `meta.plate_segmentation` so the segmented raster can be reproduced rather than trusted. All 14 packs re-exported.
- **Decision:** `tools/align_tool/plate_vectors.py::plate_to_model` reads a plate as a coarse terrain grid plus height-banded footprints, and puts it back.
  - **Height banding (2 m slices):** one polygon per component → IoU 0.989, MAE 2.95 m (tower on podium must pick one height); banded → IoU 0.991, MAE 0.50 m (162 polygons vs 17). This is what makes it an OSM layer, not a contour map.
  - **Ground from bare cells, falling back to building bases** (`plate_vectors.py::estimate_terrain`): the top-hat complement is an envelope with steps at every edge, which a coarse grid cannot carry. 20 m bare-ground samples match what the top-hat needed 5 m for; ground error 1.51 → 1.40 m at 20 m.
  - **Tracing tolerance scales with the cell:** `SIMPLIFY_M` default is half a cell, floored at 0.5 m. Paris 601,177 → 385,659 vertices for IoU 0.954 → 0.947.
- **Why:** `stl_heightmap.npy` alone (Otsu-cut) is right for the solver and useless otherwise — Alhambra keeps 35% of cells, heights start abruptly at the cut.
- **Measured (14 packs, 20 m grid, 2 m bands):** footprint IoU 0.911-0.990, height MAE 0.51-1.59 m, combined error 1.43-5.78 m (tracks relief span, not city).
- **Caveat:** compression is real only where the plate resolves buildings — 32.9x Alhambra (1.70 m/px), 2.01x Salzburg (4.85), 0.43x Paris (5.88). Vectorising a city plate is a featurisation/registration win, not a size win; do not quote compression for coarse packs.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry "A plate is a terrain surface plus building geometry"

### 2026-08-30 — Never measure against the align-tool archive rasters
- **Decision:** Anything comparing heights to a plate rasterises from the current OSM cache, never from `_align_tool/data/<city>/osm_buildings.npy`. Reference implementation now `city2stl/registration/critic.py::buildings_raster`.
- **Why:** The archive is a snapshot predating OSM height tagging and the skyline enhancement; it reads median 10.00 m everywhere.
- **Conventions, both first got wrong:**
  - Row 0 is the south edge (IoU vs the archive's built mask: 0.27 north-up, 0.89 flipped).
  - Polygons filled, not bounding-boxed (a bbox inflates each footprint into its neighbours).
- **Still valid for:** registration, and comparisons where both sides come from the same file.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry "Never measure against the align-tool archive rasters"

### 2026-08-27 — Plausible output is not verified output
- **Decision:** A local DEM source must agree with an OpenTopography dataset over the same extent to within their sampling difference; cross-source checks, not renders, verify data.
- **Why:** `geo2stl/dem.py::fetch_h5_dem` transposed its mosaic (`data[px, py]`). An SRTM tile is terrain everywhere, so the wrong sample still looked like mountains; it passed every visual check, the whole v2 build, and a verification run that recorded Breckenridge at 1682-2329 m against a true 2860-4210 m. One HTTP request would have caught it.
- **Rejected:** Visual inspection as verification.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry "Plausible output is not verified output"

### 2026-08-27 — The satellite channel is not an arbiter
- **Decision:** Satellite imagery is not used as a third registration modality; water stays the only second opinion to buildings.
- **Why:** Sobel edges of `sat.png` vs blurred plate building edges, swept over span: r 0.10-0.30, every peak margin < 1.2, an order of magnitude weaker than building masks. Paris lands 246 px out, Prague 233, Valencia 188, Barcelona 43. The imagery is not registered to OSM accurately enough.
- **Rejected:** Satellite edges as an independent check.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry "The satellite channel is not an arbiter"

### 2026-08-27 — Ground truth is rebased onto the export's window before it is scored
- **Decision:** `tools/align_tool/eval_registration.py::truth_in_export_frame` maps a hand alignment from the window it was dragged in to the current export window (a diagonal affine between two axis-aligned row-0-south lat/lon boxes, from each file's stored `resolution` and `osm_bbox_nsew`).
- **Why:** The drag tool's window differs from the export's (Miami's record is at 1024 px vs 512) and moves whenever centre or span is corrected. Raw matrices measure the difference between coordinate systems: Miami read 3602 m of error while its prediction out-scored the truth on overlap IoU (0.395 vs 0.000); after rebasing, 4.6 m. Four of six scored cities were never misplaced.
- **Rejected:** Comparing raw matrices.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry "Ground truth must be rebased onto the export's window"

### 2026-08-26 — A ground-truth record identical to the pipeline guess is not ground truth
- **Decision:** `tools/align_tool/locate.py::center_from_ground_truth` returns None when the record's `transform` matches its `pipeline_guess` within half a pixel, so an untouched record falls through to the solver.
- **Why:** The drag tool writes a record on open, seeded with the pipeline guess, and rewrites it on save whether or not anything moved. Barcelona's record was byte-identical to the geocoded centroid; since a hand alignment outranks everything in `locate.py::resolve_center`, it pinned the plate 2.5 km inland and every validation scored the solver against noise. Real nudges run 238 m (Salzburg) to 2538 m (Miami), far above the tolerance.
- **Rejected:** A "touched" flag in the file — none exists, and adding one would not fix records already written.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-26 entry

### 2026-08-04 — The ground-truth tool is a standalone HTML page with embedded data
- **Decision:** `tools/align_tool/drag_align.html` plus `data/align_data.js` (base64 data URIs). Scale locked at 0.667 (1/osm_margin geometric anchor) with an explicit unlock. Exported matrices use the pipeline convention: 2x3 affine, STL px → OSM px, `cv2.warpAffine`, rotation about origin.
- **Why:** No server and no CORS issues. (The file:// part did not hold: the tool is now served over HTTP, per [2026-08-05 — Serve the align tool over HTTP](registration-plate-location.md#2026-08-05--serve-the-align-tool-over-http).)
- **Also decided then:** training CPU-only on this machine; satellite corpus of ~50-100 auto-fetched cities via ESRI World Imagery (no API key).
- **Supersedes / superseded by:** Partly — the file:// rationale was overtaken by the [2026-08-05 HTTP-serving decision](registration-plate-location.md#2026-08-05--serve-the-align-tool-over-http).
- **Source:** decisions.md 2026-08-04 entry "Ground-truth tool is standalone HTML with base64-embedded data"
