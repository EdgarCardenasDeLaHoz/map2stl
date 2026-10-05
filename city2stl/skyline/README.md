# skyline — building heights from Street View

The one skyline doc: what it does, how to run it, where code lives, feature status, dead ends and open items.

- **What:** estimates per-building heights for a city region by registering Google Street View panoramas
  against OSM building footprints (+ Microsoft satellite footprints).
- **Not here:** current metrics and known issues → [docs/STATUS.md](docs/STATUS.md).
  History → [docs/archive/](docs/archive/) and the plan folders listed under [Plans](#plans).
- **Height stack:** the ML height model and height providers live in `city2stl/height/` (general-purpose).
  The 2026-06-07 move into `skyline/height/` was reversed 2026-09-27; `skyline/height/` is a one-release
  re-export shim.
- **Citations** use `file::symbol` (paths relative to this folder). Line numbers drift; symbol names don't.
- Merged here 2026-09-28: the former skyline agent guide (where things live, dead ends) and the
  F-SKY integration doc (feature status, open items).

## Quick start

```powershell
# from map2stl/, with ~/.venvs/map2stl (packages installed editable)
$env:GOOGLE_MAPS_API_KEY = "..."          # or put it in map2stl/.env
$env:SKYLINE_CV_SEGFORMER_SIZE = "b1"     # production setting (see Environment variables)
& "$HOME\.venvs\map2stl\Scripts\python.exe" -m city2stl.skyline.scripts.08_region_skyline_pdf --region Cartagena
```

- Outputs (gitignored, under `city2stl/skyline/runs/region_reports/`):
  - `runs/region_reports/Cartagena_skyline_report/index.html` — the HTML diagnostic report (per-building tables live here).
  - `runs/region_reports/Cartagena_skyline_report.pdf` — compact archival PDF (`pano_only_pdf: true` trims tables).
- Cost: a Cartagena run is a few minutes and ~$0.10 of Street View quota (12 spin views × seeds + screening probes).
- `scripts/build_landing_page.py` builds the cross-region landing page; `scripts/discover_city_seeds.py`
  proposes seeds for a new city (`discover_city_seeds <city> ...`; an existing `sites/<city>.json`
  is kept unless `--force`).

## Pipeline shape

Entry: `region_pdf.py::run_region_pdf_report` (called by `scripts/08_region_skyline_pdf.py`).

```
run_region_pdf_report(region_name)                      region_pdf.py
  ├─ load bbox (SQLite) + OSM buildings/water           region_data.py, osm_water.py
  ├─ drop buildings in water                             region_data.py::_drop_buildings_in_water
  ├─ optional satellite footprint merge (F-SKY8)         satellite_footprints.py
  ├─ region satellite image (F-SKY10)                    satellite_image.py
  ├─ pano-coastline precompute (F-SKY11.1/13)            coastline_registration.py
  ├─ parse seed URLs → SkylinePoint                      streetview_io.py::_parse_streetview_url
  ├─ auto-proposals (8 dirs × 3 standoffs)               seed_selection.py::_propose_standoff_locations
  ├─ auto-replace bad seeds                              seed_selection.py::_auto_replace_bad_seeds
  ├─ screen candidates (1-image probe + F-DET2 gate)     seed_selection.py::_screen_locations
  └─ per seed: _seed_multiview_registration              _pano/orchestrator.py
       ├─ capture 12-view spin (every 30°)               _pano/capture.py::_capture_pano_views
       ├─ batched SegFormer prefetch                     _core/segmentation.py::prefetch_label_maps
       ├─ F-DET1 building-count early-out                _pano/detect.py::_count_skyline_buildings
       ├─ pano heading (water + vegetation)              _pano/heading.py::_recover_pano_heading
       ├─ joint anchor sweep                             _pano/heading.py::_recover_anchor_offset
       ├─ per-view match (±8° around anchor)             _pano/detect.py::_register_views
       ├─ cross-view consensus                           _pano/detect.py::_smooth_matches_across_views
       └─ stitched pano + bearing recovery               _pano/detect.py::_build_and_detect_pano
  ├─ aggregate heights (F-SKY1/F-SKY12 rescue)           _core/height.py::aggregate_building_heights
  ├─ PDF                                                 _region_render/_pages.py::_render_pdf
  └─ HTML report                                         html_report.py, _report_plots/
```

- Why a joint anchor: one pano = one rigid pano-to-geographic rotation. Per-view escape hatches let views drift
  to different offsets and were rolled back (see [Dead ends](#dead-ends-dont-repeat-these)).
- Why masks are stitched, not RGB: SegFormer on the wide stitched image gave seams; stitching per-view masks is
  faster and seam-free.

### Segmentation stages (the HTML timing table's numbering)

| Stage | What | Where |
|---|---|---|
| 1 | SegFormer inference, label map cached (the only learned step) | `_core/segmentation.py::_ensure_label_map` |
| 2 | Morphology, glass-tower hole-fill, water-only ground cap | `_core/segmentation.py::_neural_sky_and_building_masks` |
| 3 | Skyline contour | `_core/skyline.py::detect_skyline_contour` |
| 4 | Contour silhouettes (+ F-SKY7 local maxima) | `_core/skyline.py::detect_building_silhouettes` |
| 5 | Mask silhouettes (peak/valley splitter) | `_core/skyline.py::detect_buildings_from_mask` |
| 6 | Merge silhouette sources | `_core/skyline.py::_merge_silhouette_sources` |
| 7 | OSM-anchored re-cut (F-SKY2) | `_core/registration.py::osm_anchor_silhouettes` |
| 8 | Optional MobileSAM head (F-SKY5) | `_core/registration.py::osm_sam_instance_silhouettes` |

Matching and heights: `_core/registration.py::match_segments_to_buildings` (interval-IoU or ≥ 50 % containment,
1:1 dedup), `_core/registration.py::register_view_to_osm`, `_core/height.py::estimate_heights_from_registration`.

## Where things live

Every caller, inside `skyline/` or not (tests, demos), imports the defining `_core/` or `_pano/` module directly.
- Why: a star import carries neither private names nor later rebinding of module globals.
- The `pipeline.py` façade over `_core/` was removed 2026-09-30; the other façades (pano_registration,
  region_render, report_plots) on 2026-09-25.
- Split history: [F-CLEAN14](../../docs/plans/done/F-CLEAN14-skyline-file-split.md).

| Path | Role | Feature tags |
|---|---|---|
| `region_pdf.py` | `run_region_pdf_report` wiring (no re-exports) | — |
| `_core/` | CV primitives: `types`, `util`, `segmentation`, `projection`, `skyline`, `pano`, `registration`, `height`, `timing` | F-SKY1/2/5/6/7/12 |
| `_pano/` | Per-seed loop: `capture`, `heading`, `detect`, `orchestrator` | F-SKY11.1/13/18/22/24, F-DET1 |
| `_region_render/` | PDF pages + minimap/overlay drawing (`_draw`, `_pages`) | F-SKY4, F-SKY13 overlay |
| `_report_plots/` | PNG renderers for the HTML report (`_plot_utils`, `_view_plots`, `_pano_plots`) | F-SKY15, pano report v2 |
| `html_report.py` | HTML report assembly (per-building tables) | F-SKY15, F-DET3 |
| `report_index.py` | Parses per-seed stats back out of a report's `index.html` (landing page, `/api/reports/index`) | F-DET5 |
| `seed_selection.py` | Standoff proposals, screening, auto-replace, OSM-FOV gate | F-DET2 |
| `region_data.py` | Bbox (SQLite), OSM fetch, `BuildingRecord`, water filter, `sites/*.json` readers | F-SKY8 merge |
| `streetview_io.py` | Street View Static API: URL parse/sign, metadata, image cache | — |
| `region_types.py` | Frozen dataclasses: `RegionBBox`, `SkylinePoint`, `SeedViewRegistration`, `StitchedPanoResult` | — |
| `region_config.py` | F-SKY env flags + `_SEGMENT_PALETTE` | all flags |
| `coastline_registration.py` | Coastline keypoint heading sweep (`sweep_pano_heading_offset`) | F-SKY11/11.1/13 |
| `osm_water.py` | OSM coastline / water / green polygons; primary keypoint source | F-SKY13, F-SKY18 |
| `depth_estimation.py` | Depth Anything V2 (tiled), depth → height/distance | F-SKY12 |
| `cross_view.py` | Roof colour / width / edge cross-view scorer | F-SKY10 |
| `satellite_footprints.py` / `satellite_image.py` | MS Building Footprints; ESRI satellite tiles | F-SKY8, F-SKY10 |
| `height_trace.py` / `height_trace_render.py` | Per-building gate-decision tracer + render | glass-roof Phase 1 |
| `web_image_seed.py` | Wikipedia/Wikimedia/Flickr skyline image seeds | F-WEB1 |
| `scripts/` | `08_region_skyline_pdf.py` (production), `09_height_trace.py`, `build_landing_page.py`, `discover_city_seeds.py`, `height_diagnostic_report.py`; research probes 13–16 in `scripts/demos/` | — |
| `sites/*.json` | Per-region config (17 regions) | — |

Module DAG (acyclic): `region_types`/`region_config` ← `region_data`/`streetview_io` ← `seed_selection` ←
`_region_render` ← `_pano` ← `region_pdf`; `_report_plots` ← `html_report`.
Inside `_core/`: `types` ← `projection` ← {`pano`, `registration`, `height`}; `segmentation` ← {`skyline`,
`registration`, `height`}; `skyline` ← {`registration`, `height`}.

### Pano path (360° report)

| Concern | Where |
|---|---|
| Stitch + detect + bearing recovery | `_pano/detect.py::_build_and_detect_pano` |
| Sliding-window splitter (F-SKY22) | `_pano/detect.py::_pano_sliding_window_split` |
| Tiled depth; per-column distance | `depth_estimation.py::predict_pano_depth_tiled`, `depth_estimation.py::column_building_distance` |
| Cross-correlation gate (`improve ≥ 45 %`) | `_report_plots/_plot_utils.py::_bearing_xcorr_offset` |
| OSM nearest-per-degree signal | `_report_plots/_plot_utils.py::_build_osm_nearest_per_degree` |
| Distance scan, cardinal lines, heights polar | `_report_plots/_pano_plots.py::_render_pano_bearing_scan_png`, `::_draw_pano_north_line_inplace`, `::_render_pano_heights_polar_png` |
| Pano consensus with per-view matches | `_pano/detect.py::_smooth_pano_matches_against_views` |

## Feature status

As of 2026-09-28. "Plan" links go to `map2stl/docs/plans/`.

| ID | What | State | How to enable | Plan |
|---|---|---|---|---|
| F-SKY1 | Floor-strip periodicity → OSM-independent height; upward rescue in aggregation | on | `SKYLINE_CV_F_SKY1=0` disables | [done](../../docs/plans/done/skyline/F-SKY1-floor-periodicity.md) |
| F-SKY2 | OSM-anchored split of merged silhouettes | on | — | [done](../../docs/plans/done/skyline/F-SKY2-osm-anchored-segments.md) |
| F-SKY3 | Voronoi split over OSM markers | **removed** (MAE 17.3 → 22.1 m) | — | [archive](../../docs/plans/archive/skyline/F-SKY3-osm-marker-instances.md) |
| F-SKY4 | SegFormer mask panel on per-view PDF pages | on (diagnostic) | — | [done](../../docs/plans/done/skyline/F-SKY4-mask-overlay.md) |
| F-SKY5 | MobileSAM instance head | off — no benefit measured | `SKYLINE_CV_F_SKY5=1` + checkpoint | [done](../../docs/plans/done/skyline/F-SKY5-mobilesam-instance.md) |
| F-SKY6 | 1:1 segment ↔ building dedup + "considered but lost" dots | on | — | [done](../../docs/plans/done/skyline/F-SKY6-one-to-one-matching.md) |
| F-SKY7 | Local-maxima peaks + mask panel layout | on | — | [done](../../docs/plans/done/skyline/F-SKY7-local-max-peaks-and-layout.md) |
| F-SKY8 | Microsoft satellite footprints | per region | `"use_satellite_footprints": true` | [done](../../docs/plans/done/skyline/F-SKY8-satellite-footprints.md) |
| F-SKY10 | Cross-view colour/width/edge scorer (0.15 nudge; `cv̄` in PDF header) | per region, diagnostic-only | `"use_cross_view_scoring": true` | [done](../../docs/plans/done/skyline/F-SKY10-non-ml-cross-view-registration.md) |
| F-SKY11.1 | Pano-coastline heading → seeds the anchor sweep (Phase B) | off by default | `SKYLINE_CV_F_SKY11_1=1` or per-site flags | [done](../../docs/plans/done/skyline/F-SKY11.1-pano-coastline-alignment.md) |
| F-SKY11.2 | Pano → bird's-eye IPM registration | **removed** (depth reach ~5–7 m) | — | [archive](../../docs/plans/archive/skyline/F-SKY11.2-FAILURE-ANALYSIS.md) |
| F-SKY12 | Depth Anything V2 cross-check; downweight + rescue in aggregation | off by default | `SKYLINE_CV_F_SKY12=1` | [done](../../docs/plans/done/skyline/F-SKY12-depth-from-panos.md) |
| F-SKY13 | OSM coastline overlay (on); OSM-primary keypoints, Phase C (off) | partly on | `SKYLINE_CV_F_SKY13=0` disables overlay; `SKYLINE_CV_PHASE_C=1` | [done](../../docs/plans/done/skyline/F-SKY13-osm-coastline-footprints-overlay.md) |
| F-SKY14 | Trained satellite coastline detector | proposed, deferred | — | — |
| F-SKY15 | HTML diagnostic report | on | `SKYLINE_CV_HTML_REPORT=0` disables | [done](../../docs/plans/done/skyline/F-SKY15-html-diagnostic-report.md) |
| F-SKY16 | Coastline-ICP heading | Phase A measure-only | — | [done](../../docs/plans/done/skyline/F-SKY16-coastline-icp-heading.md) |
| F-SKY17 | MS ↔ OSM footprint registration | failed | — | [archive](../../docs/plans/archive/skyline/F-SKY17-ms-osm-registration.md) |
| F-SKY18 | Coastline depth-snap + vegetation co-registration | Phases 1–2 on | — | [done](../../docs/plans/done/skyline/F-SKY18-vegetation-landmarks-depth-snap.md) |
| F-SKY19 | Multi-resolution per-view segmentation | off (experiment) | `SKYLINE_CV_MULTIRES=1` | — |
| F-SKY22/24 | Sliding-window splitter; depth post-cut (no-op); bearing xcorr rescue | on | — | — (see [STATUS](docs/STATUS.md)) |
| F-DET1/2/3/5 | Blob-count early-out, OSM-FOV gate, weak sub-labels, landing-page det column | on | — | [active](../../docs/plans/active/F-DET-detection-quality-and-early-out.md) |
| F-DET4a–c | Per-city Type 2 fixes | on hold — instrument first | — | [active](../../docs/plans/active/F-DET-detection-quality-and-early-out.md) |

- F-SKY14 constraint: any satellite-side coastline detector must be trained against OSM ground truth; HSV heuristics
  proved unreliable.
- Pipeline-order rationale (signal source-of-truth table, 2026-05):
  [F-SKY-PIPELINE-CONSOLIDATION](../../docs/plans/archive/skyline/F-SKY-PIPELINE-CONSOLIDATION.md).

## Key mechanisms (why they are the way they are)

- **OSM-anchored split (F-SKY2).** SegFormer merges adjacent towers into one blob. After ≥ 3 matches, re-split at
  OSM-projected gaps, snapping to the mask-coverage minimum inside the gap (the visible separator), not the OSM
  midpoint. Containment ≥ 50 % lets narrow projections inside wide blobs still match when IoU is small.
- **Local maxima (F-SKY7).** Glass-tower rows without sky valleys keep the contour high everywhere. A second pass finds
  peaks against a 40 px smoothed baseline with a 6 px prominence floor.
- **1:1 dedup (F-SKY6).** The loser keeps `match_diagnostics`; orange minimap dots show top-3 candidates that lost.
  Reading an unmatched stretch: no dots → OSM gap; dots but no segments → detector miss; both → matcher rejection.
- **Satellite footprints (F-SKY8).** De-duped against OSM by area-IoU ≥ 0.5; OSM wins (height tags, stable IDs).
  Satellite polygons fall back to `_core/registration.py::_height_proxy`.
- **Cross-view consensus** (`_pano/detect.py::_smooth_matches_across_views`): a segment whose building is seen in only
  one view swaps to a candidate seen in ≥ 2 views, then a post-swap dedup restores 1:1 (the swap alone doesn't
  enforce it; `_pano/detect.py::_dedup_matches`). The pano gets the same pass, and a swapped pano segment that
  loses the dedup goes back to its own match. `seed_index` is rebuilt after smoothing.
- **Water filter** (`region_data.py::_drop_buildings_in_water`): centroid in water, or > 15 % polygon overlap.
  The wet-side-of-coastline test is removed (see Dead ends).
- **Auto-replace bad seeds** (`seed_selection.py::_auto_replace_bad_seeds`): a rejected seed or `screen_score < 0.20`
  swaps to a nearby proposal scored `0.7 · screen + 0.3 · proximity`, keeping the original name/FOV/pitch so
  per-seed config still applies. Logged as `[auto_seed]`.
- **Bbox base cap** (in `_pano/detect.py::_register_views`): a mask base more than `max(80 px, 0.18 · H)` below the
  OSM-projected ground row is clipped. Catches masks running down the beach to the waterline.
- **Vegetation co-registration (F-SKY18)** (`_pano/heading.py::_recover_pano_heading`): water sweep plus a sweep
  against OSM park/grass/forest, peak-weighted blend; only when `pano_veg_frac > 0.005`. Uses `use_base_y=True`
  because vegetation keypoints are ground-plane, not horizon-level.
- **Negative seeds:** frames captured and shown as bad-skyline examples; all analysis skipped; excluded from
  aggregation. A regression fixture: they should contribute nothing.

## Site configuration — `sites/<region>.json`

- `north/south/east/west` — bbox for the OSM fetch.
- `seed_urls` — Street View URLs (`@lat,lon,...` or Photo Sphere with `pano_id`); each becomes `seed_<N>`.
- `anchor_offsets_deg` — manual pano-to-geographic heading per seed; skips the joint sweep.
  - Why kept: the IoU objective is multi-modal when buildings surround the seed; peninsula seeds have a 180° twin.
  - Drop one only after measuring (dropping seed_1's on Cartagena cost 54 matched buildings).
- `negative_seeds` — excluded from aggregation, still rendered (Cartagena: `["seed_2", "seed_3"]`).
- `elevated_seeds` — drone Photo Spheres (Cartagena: `["seed_1", "seed_4", "seed_5"]`), measured
  footprint first (`_pano/elevated.py`, F-DET6) instead of by the street-level path:
  - camera from the waterline, then its position (waterline over ±600 m, parks and streets
    nearby); seed_4's recorded position was ~360 m off;
  - every OSM footprint in view measured with Depth Anything; seeds fused (the nearer,
    better-seen view wins a disagreement) before the usual aggregation.
  - Why: the street path assumes a camera 1.7 m up, so a drone view's heights are wrong by
    construction; the screens and F-DET1 also judge street views and dropped good drone seeds.
    Anchor offsets don't apply to these seeds.
- `commons_categories` — extra Wikimedia Commons categories for the photo pipeline
  (`scripts/13_photo_profiles.py`), beside the "<city> skyline" ones it finds itself. Use it for
  neighbourhoods whose categories don't say "skyline" (Cartagena: Bocagrande, its beaches,
  Hotel Estelar).
- `max_plausible_height_m` (default 300) — bounds the glass-facade contour override and the y-consistency gate.
  Set just above the region's tallest tower (Cartagena 200).
- Opt-ins: `use_satellite_footprints`, `use_cross_view_scoring`, `use_pano_coastline_recovery`,
  `drive_pano_recovery_anchor`, `pano_only_pdf`.
- `known_heights_m` — ground truth for the HTML report's validation panel.

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | — | Street View Static API key (required). |
| `GOOGLE_MAPS_SIGN_SECRET` | unset | HMAC-signs requests; spin views go to 1280×720. Unsigned requests are clamped to 640 px wide. |
| `SKYLINE_SV_TALL_FRAME` | unset | `1` requests 640×640 instead of 640×540. |
| `SKYLINE_CV_SEGFORMER_SIZE` | `b3` | `b0`…`b5`. **Production pins `b1`**: ~3× faster than b3, no matched-building loss on Cartagena. b3 became the default 2026-05-16 (vs b0: matched tagged 8 → 17, MAE 22.1 → 13.7 m). |
| `SKYLINE_CV_SEGFORMER_INPUT_SIZE` | `512` | `384` is ~12 % faster but loses ~14 % of matches. |
| `SKYLINE_CV_SEGFORMER_BATCH` | `12` (CPU); on CUDA by model: b0/b1 8, b2/b3 4, b4/b5 2 | Images per batched forward pass (`prefetch_label_maps`); 2.4× faster, bit-identical. `1` disables. On a 4 GB GPU, b3 at 12 spills into shared memory and runs 9× slower than at 4. |
| `SKYLINE_CV_SEGFORMER_DEVICE` | auto | `cpu`/`cuda`. `scripts/setup-venv.ps1` installs the CUDA build of torch when an NVIDIA GPU is present (torch 2.6.0+cu124 since 2026-10-03). GTX 1650: b3 0.20 s/image on CUDA vs 3.6 s on CPU (18×), b0 0.036 vs 0.20 s (`claude/scripts/segformer_bench.py`). |
| `SKYLINE_CV_HTML_REPORT` | `1` | `0` skips the HTML report. |
| `SKYLINE_CV_F_SKY1` | `1` | Floor periodicity. |
| `SKYLINE_CV_F_SKY5` | `0` | MobileSAM head; needs `pip install git+https://github.com/ChaoningZhang/MobileSAM.git` and `vit_t.pth` at `~/.cache/mobile_sam/` (or `MOBILESAM_CHECKPOINT_PATH`). |
| `SKYLINE_CV_F_SKY11_1` | `0` | Pano-coastline recovery seeds the anchor sweep. |
| `SKYLINE_CV_F_SKY12` | `0` | Depth Anything V2 cross-check and aggregation rescue. |
| `SKYLINE_CV_F_SKY13` / `SKYLINE_CV_F_SKY13_SAT_BG` | `1` / `0` | OSM coastline minimap overlay / satellite minimap background. |
| `SKYLINE_CV_PHASE_C` | `0` | F-SKY13 Phase C: OSM keypoints drive the heading sweep. |
| `SKYLINE_CV_MULTIRES` | `0` | F-SKY19 multi-resolution segmentation. |
| `SKYLINE_TAG_FILTER` | `1` | `0` measures unaided error (don't filter using OSM height tags). |
| `OPENTOPO_API_KEY`, `FLICKR_API_KEY` | unset | DEM terrain for building bases; Flickr web seeds (Wikimedia is the keyless fallback). |

## Caches (under `runs/`, gitignored)

- `runs/seed_resolution_cache.json` — pins each seed URL's snapped (lat, lon, pano_id).
  Why: the Static API's location snap drifts between calls. Delete to re-resolve.
- `runs/image_cache/*.png` — Street View images keyed by request hash (API key excluded).
- `runs/satellite_footprints_cache/` — ~12 MB per quadkey tile (F-SKY8).

## Benchmark (F-SKYBENCH)

Height accuracy is scored on surveyed truth, never on Cartagena (no open survey; its truth was
22 OSM `building:levels` values). Plan: [F-SKYBENCH](../../docs/plans/active/F-SKYBENCH-height-benchmark.md).

```powershell
# from map2stl/; runs each region in its own process, then scores it
& "$HOME\.venvs\map2stl\Scripts\python.exe" -m city2stl.skyline.scripts.10_benchmark --regions miami boston
# re-score the newest existing report per region, no Street View calls
& "$HOME\.venvs\map2stl\Scripts\python.exe" -m city2stl.skyline.scripts.10_benchmark --score-only
```

- Regions and their survey (`benchmark.py::REGIONS`): Miami, Chicago, Seattle, Boston (USGS 3DEP via
  the EPT octree, `city2stl/height/providers/lidar_3dep_ept_laspy.py`), Benidorm and Madrid (CNIG),
  La Défense (IGN LiDAR HD), Prague Pankrác (ČÚZK).
- Truth per footprint (`benchmark.py::footprint_truth`): p95 of the nDSM inside the footprint shrunk
  by 1 m, from the survey and from Google 3D Tiles; a source counts only where at least half the
  footprint's cells have a value (`MIN_FINITE_FRACTION`). `confirmed` when they agree within
  max(3 m, 10 %); only confirmed buildings make the headline, `disputed` ones are counted.
- Measured on the footprints a run scored (`heights.json` → `footprint_lonlat`), keyed by geometry,
  cached in `runs/benchmark/truth/<region>.json`. Only complete reads are cached (every source
  answered, "not covered" included): a failed source or a `--no-tiles` run is scored but not
  saved. `--refresh-truth` re-measures cached footprints.
- Output: `runs/benchmark/<stamp>/summary.json` (MAE, median AE, bias, within 15/25 %, per height
  band, per view and seed count, and how far OSM tags sit from truth) and one printed table.
- Same flags every run: each region's process gets `scripts/10_benchmark.py::PINNED_FLAGS` (the
  baseline's b1/512, tag filter on, experimental flags off) over the shell's values; `--keep-env`
  lets the shell through. `summary.json` records the flags and whether Street View signing was on.
- Same cameras every run: auto-proposals are saved in `runs/auto_proposals/<region>.json` with the
  region's bbox (`seed_selection.py::_persisted_proposals`); a changed bbox proposes afresh, and
  so does deleting the file. Files from before the bbox was saved are kept and upgraded.

## Tests

```powershell
& "$HOME\.venvs\map2stl\Scripts\python.exe" -m pytest tests/test_skyline*.py -q
```

- 8 files, 152 pass + 1 skip (2026-09-28, ~45 s).
- Cover CV math: URL parsing, projection, occlusion, matching, silhouettes, aggregation, depth/pano-inverse
  geometry, height datum, OSM water, height trace, HTML report.
- Orchestration (`region_pdf.py`, `_pano/`) is exercised only by full region runs.

## Troubleshooting

| Symptom | Likely cause | Look at |
|---|---|---|
| Buildings extend into water on the minimap | Gap in the OSM water polygon | `region_data.py::_drop_buildings_in_water` |
| Beach drawn as building in the mask | SegFormer-b1 labels sand as building; accepted (see STATUS) | `_core/segmentation.py::_neural_sky_and_building_masks` |
| Bbox doesn't reach the building base | Base cap fired (> 80 px below expected ground row) | `_pano/detect.py::_register_views` |
| Match count drops after smoothing | Dedup cleared more than the swap fixed; check `match_diagnostics` | `_pano/detect.py::_smooth_matches_across_views` |
| Pano page disagrees with per-view consensus | Pano matched no overlapping buildings, so the pano pass didn't fire | `_pano/detect.py::_smooth_pano_matches_against_views` |
| Auto-replaced seeds end up worse | `good_score_threshold` (0.35) too lax for the region | `seed_selection.py::_auto_replace_bad_seeds` |
| `UnicodeEncodeError` crashes a run | Windows console is cp1252; `print()` with `→`/`°` | use ASCII in console prints (reports may use Unicode) |

## Dead ends (don't repeat these)

- **Wet-side-of-coastline filter.** "Any coastline in range": one reversed coastline dropped 2087 buildings (69 % of
  Chicago). "Nearest coastline only": dropped 38 real shore condos because OSM draws coastlines inland of the beach.
  Helper removed 2026-06-02; rebuild from git history only with a more accurate per-building test.
- **Dropping seed_1's manual anchor** on Cartagena when pano recovery was within 6°: the joint sweep hit a wrong local
  maximum and lost 54 matched buildings. Measure before removing manual offsets.
- **Bbox base cap at 0.08 · H slack** clipped distant towers' real bases; widened to `max(80 px, 0.18 · H)`.
- **Always-on beach band heuristic** cost 5 matched buildings; replaced by the class-membership ground cap.
- **Earth/sand in the ground cap** (reverted 2026-06-07): chopped sandy-coloured towers (seed_5 peninsula tip).
  The cap is water-only and bottom-up.
- **MobileSAM (F-SKY5) on Cartagena** (2026-06-02): identical extracted buildings (317) and coverage, +79 % wall time.
  F-SKY2 already handles the merged-tower cases, or SAM's splits don't survive 1:1 dedup.
- **F-SKY3 Voronoi split:** unconditional splitting regressed MAE 17.3 → 22.1 m and tagged matches 13 → 8.
- **F-SKY11.2 bird's-eye IPM:** monocular water masks reach ~5–7 m against a 1 km+ bay, so the rotation IoU is flat.
- **Per-bearing F-SKY11 sweep as primary:** lossier than the pano sweep; kept only as a visualisation idea.
- **Coastline ICP as primary heading (F-SKY16):** fixes one seed, fails another; the 180° twin is the real blocker.
- **Distinct-peak gate for bearing recovery:** broke on broad clusters (Chicago Loop); replaced by `improve ≥ 45 %`.
- **Sliding-window SegFormer on stitched RGB:** per-tile class probabilities don't compose, so seams became bad column
  heights. Mask stitching replaced it (glass-roof plan, rejected approach A).
- **Per-view escape hatch around the joint anchor:** views drifted to different offsets, breaking "one pano = one
  offset". Reconsider only with a signal that makes the objective unimodal (glass-roof plan, rejected approach B).
- **SegFormer speed-ups that don't work** (b1, CPU, 2026-06-02):
  - Pruning the 150-class head to 6: the head is 0.3 % of params and ~0 % of FLOPs.
  - INT8 dynamic quantization: no speedup (704 → 714 ms/img) and corrupted masks (building IoU 0.667).
  - What worked: batched spin prefetch, 2.4×. Untested: b0 (~1.3× faster, needs a match-parity run),
    ONNX Runtime / static quant / distillation.

## Open items

The single list of skyline open work (the plans roadmap in `map2stl/docs/plans/` points here rather than repeating it).

- **Heights**
  - **First: untagged buildings read 65–113 m too tall** in all six benchmarked cities
    ([STATUS](docs/STATUS.md#height-accuracy-on-surveyed-truth-f-skybench-baseline-2026-10-04)).
    A roof pixel is credited to a near low building when a farther tower owns it. Check the
    owner before crediting: per-column depth vs each candidate's OSM distance, and the
    closest-in-column gate (`_core/height.py::estimate_heights_from_registration`). Measure with
    `scripts/10_benchmark.py --score-only` on the 2026-10-03 baseline reports.
  - Glass-tower under-prediction (50–100 m): run the Phase 1 trace on ≥ 3 tall tagged towers and pick the dominant
    cause before any Phase 2 depth work
    ([glass-roof plan](../../docs/plans/done/skyline/glass-roof-height-fix-plan.md)).
  - Depth saturation > 1.2 km: try a metric-depth model (ZoeDepth / Metric3D).
  - F-SKY1: calibrate the stitched-pano `f_px` so `inferred_distance` becomes usable.
  - Raise cross-seed coverage (n ≥ 10 cross-checked buildings), e.g. place auto-seeds per 2×2 bbox cell.
- **Heading**
  - F-SKY16 Phase B: 180° symmetry disambiguation — ICP/keypoint consensus gate, asymmetric building-density
    tiebreaker, narrowed search around a trusted prior.
  - F-SKY18 Phase 3: feed bearing landmarks (coastline + vegetation) into registration; measure vs manual offsets.
  - F-SKY13 Phase C validation: `SKYLINE_CV_PHASE_C=1` on Cartagena seed_5; expect heading ≈ 320° and better IoU.
  - F-SKY11.1 Phase B validation: seed_5 with `SKYLINE_CV_F_SKY11_1=1`; anchor within ±5° of manual, MAE within ±1 m
    of 13.73 m. Then consider raising the σ gate 0.10 → ~0.15 (seed_1 σ = 0.120 just misses).
  - Miami seed_3: visually confirm the bearing correction that passed at the 47 % gate edge.
- **Detection (F-DET)** — assumptions challenged 2026-06-23, see the plan's "Critical review":
  - A7: instrument Type 2 seeds (bearing shift, pano snap distance, segment ↔ footprint overlap) in
    `_pano/orchestrator.py` + the pano summary table before any F-DET4a–c fix.
  - A2: correlate the good/medium/weak label with per-building height MAE on curated regions.
  - A4: make the F-DET2 OSM-FOV gate count satellite footprints too (it currently rejects the OSM-sparse cities
    F-DET4a targets).
- **Benchmark truth**
  - Spain (Benidorm, Madrid): CNIG reads ~5.5 m under 3D Tiles in both cities (2.5 m grid,
    2008-15); only 33 / 64 confirmed. Spain's 0.5 m PNOA surfaces would help (download form
    only, see `reference/survey-sources.md`), or measure the offset and correct for it.
  - Seattle: 3D Tiles reads +3.9 m above lidar on hills; check `google_3d.py::_ground_from_dsm` on slopes.
  - Prague: 3D Tiles gave one value (104.15 m) over many low footprints; find the coarse-tile cause.
  - Miami inland has no USGS EPT lidar; Miami-Dade County's survey would be the next source.
- **Validation and regions**
  - F-SKY5: only revisit on a region with merged towers F-SKY2 can't split; Cartagena showed no gain.
  - Wire Miami and Chicago to the opt-in flags (F-SKY8/11.1/13, `pano_only_pdf`) and record per-seed recovery
    accuracy in STATUS (was F-CLEAN13).
- **Tests**
  - Unit test for `coastline_registration.py::sweep_pano_heading_offset` on synthetic keypoints.
  - Edge-case tests for F-SKY13 OSM coastline extraction (`osm_water.py`).
- **Housekeeping**
  - Decide on the research probes `scripts/demos/13`–`16`: fold conclusions into STATUS and archive (AUDIT-2026-06-07).

## Plans

- Active: [F-DET](../../docs/plans/active/F-DET-detection-quality-and-early-out.md).
- Done: `map2stl/docs/plans/done/skyline/` — F-SKY1, 2, 4–8, 10, 11.1, 12, 13, 15, 16, 18, glass-roof plan.
- Archived (failed or superseded): `map2stl/docs/plans/archive/skyline/` — F-SKY3, F-SKY11.2, F-SKY17, the
  F-SKY10/11.2 work order, F-SKY-PIPELINE-CONSOLIDATION.
- Audits: `map2stl/docs/history/audits/` (F-SKY-AUDIT 2026-05-17 / 05-24, AUDIT-2026-06-07).
- Older skyline notes: [docs/archive/](docs/archive/) (Cartagena audit, implementation plan, status to 2026-05).
