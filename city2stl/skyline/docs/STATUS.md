# Skyline status — metrics and known issues

- **Scope:** what the pipeline measurably does today and what is still broken.
- **Not here:** how it works / where code lives ([../README.md](../README.md)); open work
  ([../README.md#open-items](../README.md#open-items)); history before 2026-06
  ([archive/status-2026-05.md](archive/status-2026-05.md)).
- **Snapshot:** metrics from the 2026-06-07 full runs; later changes are dated inline.
- Code citations use `file::symbol` (line numbers drift). Paths are relative to `city2stl/skyline/`.

## Height accuracy on surveyed truth (F-SKYBENCH baseline, 2026-10-04)

The headline. Truth per footprint = survey lidar and Google 3D Tiles agreeing within
max(3 m, 10 %) ("confirmed"); production settings (`SKYLINE_CV_SEGFORMER_SIZE=b1`, tag filter on);
run `runs/benchmark/2026-10-03_1952`, scored `2026-10-04_1415` (Benidorm `2026-10-04_1357`,
Madrid `2026-10-04_1425`, after the Overpass outage). How to run: [README → Benchmark](../README.md#benchmark-f-skybench).

| Region | Scored | Confirmed | Disputed | MAE | Median AE | Bias | Within 25 % | Untagged MAE / bias (n) | Tagged MAE / bias (n) |
|---|---|---|---|---|---|---|---|---|---|
| Miami | 463 | 192 | 37 | 102.3 | 90.2 | +86.6 | 7 % | 115.5 / +104.7 (163) | 28.5 / −15.3 (29) |
| Chicago | 348 | 265 | 74 | 50.5 | 30.1 | +38.8 | 18 % | 80.3 / +75.8 (130) | 21.9 / +3.1 (135) |
| Seattle | 346 | 156 | 185 | 62.2 | 39.4 | +56.1 | 15 % | 77.9 / +72.6 (115) | 18.0 / +9.8 (41) |
| Boston | 707 | 587 | 108 | 47.1 | 24.9 | +40.2 | 19 % | 68.0 / +65.5 (344) | 17.5 / +4.5 (243) |
| La Défense | 101 | 87 | 14 | 108.7 | 114.5 | +108.4 | 2 % | 113.2 / +112.9 (83) | 15.3 / +15.3 (4) |
| Prague Pankrác | 77 | 23 | 54 | 29.7 | 24.5 | +9.7 | 9 % | 45.6 / +29.5 (7) | 22.8 / +1.0 (16) |
| Benidorm | 98 | 33 | 62 | 31.6 | 27.9 | +30.3 | 15 % | 89.1 / +89.1 (4) | 23.7 / +22.2 (29) |
| Madrid Cuatro Torres | 197 | 64 | 129 | 62.1 | 30.5 | +53.7 | 6 % | 84.6 / +83.2 (42) | 19.3 / −2.6 (22) |

- **Relative heights are near chance** (scored `2026-10-04_1527`): building pairs in the right
  order inside one view — Miami 44 %, Seattle 48 %, Madrid 47 %, Prague 53 %, Boston 56 %,
  La Défense 59 %, Chicago 66 %, Benidorm 86 % (mostly tagged buildings). A camera-height error
  would keep the order, so the wrong order points at roof-to-building assignment.
- **The main product gap is untagged buildings read far too tall** (+65 to +113 m bias), in every
  city. Mostly small low buildings (Miami: true median 9.7 m, footprint ~420 m², estimate median
  123 m): a tower roof behind them is credited to them. Every seed shows it, so it is not one bad
  heading.
- **Tagged buildings look good because of the tag filter** (`_core/height.py::_tag_filter_enabled`):
  per-view estimates far from the OSM tag are dropped, so the tag rescues them. Scores against OSM
  tags (the old yardstick) hid the problem; the benchmark reports the two groups separately.
- Towers > 100 m still read low (−15 to −34 m bias) but are now the smaller error.
- More views help only at 4+ (Miami: 4+ views MAE 40 m vs 103 m for one view).
- Truth quality per city:
  - Chicago, Boston, La Défense, Miami: sources agree (median 3D Tiles − survey +0.2 to +1.4 m).
  - Seattle: 3D Tiles reads +3.9 m above lidar (IQR +1 to +8 m), likely its ground estimate on
    hills; 185 disputes.
  - Prague: 3D Tiles returned one value (104.15 m) over many low footprints, a coarse-tile
    artefact; the cross-check rejects them, leaving 23 confirmed buildings.
  - Benidorm and Madrid: 3D Tiles reads +5.6 / +5.4 m above CNIG (IQR about +3 to +9 m) in both
    Spanish cities, so the offset is CNIG's (MDSn: 2.5 m grid, 2008-15), not one city's. Only 33
    and 64 confirmed; none of Madrid's four towers confirm.
  - Madrid needed four hand-picked seeds: its auto-proposals saw no skyline (0 buildings).
  - Miami: the only USGS EPT project (2019 Keys topobathy) has points on the coast only; inland
    buildings are 3D Tiles only and stay out of the headline.
- Cartagena is not scored: no open survey exists, and its old figures rested on ~20 OSM tags.

## Headline metrics (full pipeline, 2026-06-07)

| Region | Seeds (user + auto) | `seed_extracted_buildings` | Bearing recovery |
|---|---|---|---|
| Cartagena | 5 + 6 auto = 11 | ~716 | all SKIP (already aligned) |
| Miami | 4 + 2 auto = 6 | ~381 | seed_3/4 APPLY, rest SKIP |
| Chicago | 2 + 3 auto = 5 | ~210 | 4/5 APPLY (Loop was ~180° off) |

- Cartagena `negative_seeds` = `["seed_2", "seed_3"]` (seed_2: dense interior, screened out; seed_3: gas-station view).
- Auto-proposed standoff locations now run the **full** pipeline, not just act as a swap pool.
  - Roughly doubled coverage: Cartagena 375 → 716, Chicago 101 → 210.
- 17 regions have a `sites/*.json`; F-DET calibration used 82 panos across them
  ([F-DET plan](../../../docs/plans/active/F-DET-detection-quality-and-early-out.md)).

## HTML report (per seed page)

- Two **independent** tab groups, so a pano view and a top-down view are visible together:
  - **Pano-space** strips: Street view · SegFormer mask · Depth · Distance scan.
  - **Top-down** polar plots (shared 1500 m axis): Footprints · Satellite · Reconstruction · Heights.
- Overlays:
  - Cardinal N/E/S/W lines on every pano strip — `_report_plots/_pano_plots.py::_draw_pano_north_line_inplace`.
    - Why: makes the column → bearing mapping visually checkable.
  - Distance scan — `_report_plots/_pano_plots.py::_render_pano_bearing_scan_png`.
    - x = pano column; depth-derived nearest-building distance (blue) vs OSM nearest (orange).
    - The horizontal gap between the curves *is* the bearing error.
  - Click any image → zoom/pan modal.
- Anything beyond `_report_plots/_plot_utils.py::POLAR_MAX_M` (1500 m) is dropped from top-down plots and bearing recovery.

## Bearing recovery (F-SKY24 Phase 3)

- Where: applied in `_pano/detect.py::_build_and_detect_pano`; gate in `_report_plots/_plot_utils.py::_bearing_xcorr_offset`.
- What: silhouette × OSM cross-correlation refines (or rescues) the satellite-coastline anchor.
  1. Per-degree depth silhouette vs per-degree nearest-OSM distance.
     - Empty bearings get the `NO_BUILDING_M` (3000 m) sentinel.
     - Why: "pano sees a building where OSM says open space" must be a strong penalty; without it depth
       saturation flattened the score landscape and a wrong rotation could win.
  2. Cross-correlate over all 360 rotations; **apply only when `improve ≥ 45 %`** (MAE drop vs current anchor).
     - Why 45 %: already-aligned seeds improve < 30 %, misaligned ones > 50 %.
     - Rejected: a "distinct peak" gate — broke on broad clusters (Chicago Loop).
- Result: Chicago Loop (~180° off) auto-corrected; Cartagena/Miami anchors preserved.

## Splitting (F-SKY22, F-SKY24 Phase 1, F-SKY2 on the pano)

- **Sliding-window splitter** — `_pano/detect.py::_pano_sliding_window_split`.
  - Why: the global splitter's per-component cap clipped towers when the whole skyline is one blob.
  - 360 px window / 280 px stride; seed_1 12 → 33 segments, seed_5 13 → 25.
- **Depth-fused post-cut** (F-SKY24) — **measured no-op** on all 9 seeds.
  - F-SKY21's per-cluster depth pass (threshold 0.08) already drains that signal upstream. Kept (cheap).
- **OSM-anchored split on the pano** (F-SKY2) — splits a wide segment at contained OSM projections.
  - +5 segments across 9 seeds; catches same-distance adjacent towers that depth cannot separate.

## Mask: water-only ground cap

- Where: `_core/segmentation.py::_neural_sky_and_building_masks`.
- Finds the foreground waterline **bottom-up** — distant bay water at the horizon no longer chops buildings in front of it.
- Uses **water classes only** (earth/sand removed).
  - Why: buildings stand on sand, and Cartagena's bright sandy towers were partly labelled sand and clipped.
  - Recovered the seed_5 peninsula-tip cluster.

## Depth (Depth Anything V2, F-SKY12)

- Tiled inference — `depth_estimation.py::predict_pano_depth_tiled`.
  - Why: the HF pipeline squashes a 2688-px pano to ~518 px; full-res tiles sharpen depth. ~4–7 s, once per seed.
- **Saturation past ~1.2 km** is structural (DA2's [0,1] inverse depth) and is the dominant residual error.
  - Not fixed by: Base model (≈ Small, 3× cost), higher input res, column-averaging.
  - Workaround: OSM clamp — > 1200 m values with a nearby OSM building adopt the OSM distance.
  - Real fix: a metric-depth model (ZoeDepth / Metric3D) — not attempted.
- Aggregation use (2026-06-10) — `_core/height.py::aggregate_building_heights`:
  - Confidence halved when depth disagrees and `depth_height_m < 0.70 × geometric`.
  - Upward rescue when ≥ 2 views have depth > 1.30 × geometric and the rescue exceeds 1.40 × geometric.
- Geometry fix (2026-08-28): anchors moved from silhouette top to the horizon row; height takes its angle
  from the roof pixel and distance from a facade pixel (`depth_estimation.py::depth_height_from_segment`).
  - Why: a z-depth equals `forward_m` only where the sight line is horizontal, and the silhouette top reads sky.

## F-SKY1 floor-strip periodicity (default on)

- Where: `_core/skyline.py::_floor_period_for_building`; disable with `SKYLINE_CV_F_SKY1=0`.
- Sub-harmonic descent: the autocorrelation peak is often a 2–3× multiple, so descend to the fundamental.
- `inferred_height` (a ratio, `f_px`-independent) is usable — ~97 m median on Cartagena.
  - Feeds an upward rescue in `aggregate_building_heights` (2026-06-10): ≥ 2 views with
    `floor_confidence ≥ 0.30` and F-SKY1 median ≥ 1.4 × geometric → `effective_height_source = "f_sky1"`.
- `inferred_distance` is **not trustworthy** — ~2.5× low (~122 m vs ~300 m) because the stitched-pano `f_px` is wrong.
- Hit rate is low: ~64/1672 on Cartagena; 0 on Chicago/Miami (far facades < 80 px tall).

## Heading recovery — precedence

1. `anchor_offsets_deg` in `sites/<region>.json` — manual, highest precedence.
2. F-SKY11.1 pano-coastline recovery → seeds the anchor fine sweep (`_pano/heading.py::_recover_anchor_offset`).
   - Only when the peak is sharp (σ ≤ 0.10, peak > 0.40); opt-in `SKYLINE_CV_F_SKY11_1=1` or per-site flags.
3. Joint anchor IoU sweep — 3° coarse over 360°, then 0.5° fine.
4. Bearing xcorr rescue on the stitched pano (above).
- Why manual overrides survive: peninsula seeds are near-symmetric 180° apart, so coastline methods lock onto the twin
  (Cartagena seed_4, seed_5). See F-SKY16 in the README's feature table.

## Height stack location (corrected 2026-09-28)

- The ML height stack and providers live in `city2stl/height/` (general-purpose, not skyline-specific).
- The 2026-06-07 move into `skyline/height/` was **reversed 2026-09-27**; `skyline/height/__init__.py` is a
  one-release re-export shim.

## Known issues

- **Untagged buildings read 65–113 m too tall** (benchmark above) — the main product gap since
  2026-10-04. Roofs of farther towers are credited to near low buildings; the tag filter hides it
  on tagged buildings.

- **Tall glass towers under-predict** — 15–34 m on the 2026-10-04 benchmark (was "50–100 m" on
  Cartagena/Miami tags).
  - Hypotheses: mask under-reach on reflective tops; closest-in-column gate drops the tall tower; roof-y → height math.
  - Trace plan and Phase 1 tooling: [glass-roof-height-fix-plan.md](../../../docs/plans/done/skyline/glass-roof-height-fix-plan.md),
    `scripts/09_height_trace.py`.
- **Cross-seed coverage is thin** (~3 buildings seen from ≥ 2 seeds, 2026-05 measurement).
  - Why it matters: without n ≥ 10 cross-checked buildings, MAE is dominated by single-seed estimates.
- **Depth saturation > 1.2 km** (see Depth).
- **F-SKY1 pano `f_px` calibration** blocks a trustworthy floor-derived distance.
- **Miami seed_3** bearing correction passed at the 47 % gate edge — needs a visual confirm (a 50 % gate would skip it).
- **Auto-proposal positions drift** with live OSM fetches, so coverage varies run to run
  (fix: persist proposals per region, like `seed_resolution_cache.json`).
- **Display (unverified since 2026-05):** FOV cone on the auto-zoomed minimap sized for 1500 m, not the zoomed span.

## Accepted weaknesses

- Photo Sphere panos have arbitrary frames; rotation is re-discovered per seed. Some Photo Sphere IDs return the
  "no imagery" placeholder, so we fall back to a location-resolved road pano.
- Image band crop comes from the mask itself: an under-detected mask truncates a real rooftop in the displayed image.
- DEM base elevation is unreliable: most buildings get `terrain_elev_m = 0.0` (Castillo San Felipe's 40 m hill is missing).
- SegFormer-b1 labels some beach sand as building. Accepted: an aggressive band cap cost 14 % of matches.
