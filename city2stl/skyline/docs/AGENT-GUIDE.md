# skyline — Agent navigation guide

Practical orientation for a future agent (or human) picking this up cold.
Read [`STATUS.md`](STATUS.md) for "what works / what doesn't" feature-level
notes. **This file** is the map of *where things live* and *how the
pieces fit together*.

Last updated: 2026-06-07 (F-CLEAN14 split region_pdf.py into focused
modules; earlier: cross-view smoothing, auto-seed replacement, SegFormer-b1).

## How to run

```powershell
# From the strm2stl directory:
cd "d:/OneDrive/Documents/Projects/3D Maps/Code/strm2stl"

# Cartagena (canonical test region):
SKYLINE_CV_SEGFORMER_SIZE=b1 "C:\venvs\strm2stl\Scripts\python.exe" \
  -m city2stl.skyline.scripts.08_region_skyline_pdf --region Cartagena

# Outputs (HTML report, PDF, PNGs, JSON aggregates):
#   city2stl/skyline/runs/region_reports/Cartagena_skyline_report/index.html
#   city2stl/skyline/runs/region_reports/Cartagena_skyline_report.pdf
```

Env vars that control the run:

| var | default | effect |
|---|---|---|
| `SKYLINE_CV_SEGFORMER_SIZE` | `b3` | `b0` … `b5`. **Production now uses `b1`** — 3× faster than b3 with no matched-building loss on Cartagena. |
| `SKYLINE_CV_SEGFORMER_INPUT_SIZE` | `512` | Lower (e.g. `384`) trades accuracy for speed. Diminishing returns; measured ~12% speedup at 384, ~14% match loss. |
| `SKYLINE_CV_SEGFORMER_BATCH` | `12` | Images per batched forward pass. The spin is prefetched in one (chunked) pass before per-view work (`prefetch_label_maps`); measured **2.4× faster** on a 12-view spin (b1, CPU) with bit-identical label maps. Set `1` to disable batching, or lower if activation memory is tight. |
| `SKYLINE_CV_SEGFORMER_DEVICE` | auto | `cpu` or `cuda`. Auto-detects CUDA. **Note:** installed torch is currently a CPU-only build (`2.12.0+cpu`), so the `cuda` path is unreachable until a CUDA wheel is installed. |
| `SKYLINE_CV_HTML_REPORT` | `1` | Set `0` to skip the HTML report (PDF still rendered). |
| `SKYLINE_CV_F_SKY5` | unset | Set to `1` to enable MobileSAM instance head (needs checkpoint at `~/.cache/mobile_sam/vit_t.pth`). **Measured 2026-06-02: no benefit on Cartagena** — identical `seed_extracted_buildings` (317) and coverage off-vs-on, while wall time rose 151s → 270s (+79%; Stage 8 added ~155s across 16 view-invocations). Keep off. See "Known dead-ends". |
| `SKYLINE_CV_F_SKY12` | unset | Set to `1` to enable monocular-depth height verification. |

## Pipeline shape (high level)

> **F-CLEAN14 (2026-06-07):** `region_pdf.py` was split into focused modules.
> `region_pdf.py` is now just `run_region_pdf_report` + a re-export hub. The
> column below names the module each function NOW lives in. Precise line
> numbers are omitted because they drift; `grep -n '^def <name>'` the named
> file. Function *names* are the stable handle.

```
run_region_pdf_report(region_name)                          region_pdf.py
  ├─ load OSM data                                          region_data.py / osm_water.py
  ├─ filter buildings in water polygons / centroids         _drop_buildings_in_water        region_data.py
  ├─ optional satellite footprint merge (F-SKY8)            satellite_footprints.py
  ├─ fetch region satellite image (F-SKY10)                 satellite_image.py
  ├─ pano-coastline-recovery precompute (F-SKY11/13)        coastline_registration.py
  ├─ parse seed URLs → SkylinePoint                         _parse_streetview_url           streetview_io.py
  ├─ generate auto-proposals                                _propose_standoff_locations     seed_selection.py
  ├─ AUTO-REPLACE BAD SEEDS                                 _auto_replace_bad_seeds         seed_selection.py
  ├─ screen all candidates (Static API gate)                _screen_locations               seed_selection.py
  └─ _seed_multiview_registration(...)                      pano_registration.py
        For each seed:
        ├─ Phase 1: _capture_pano_views                     pano_registration.py
        │            12 spin headings, screen each, pitch-correct
        ├─ Phase 1b: prefetch_label_maps (batched spin)     pipeline.py
        │            one forward pass for all spin views → cache;
        │            every later per-view mask call is a cache hit
        ├─ Phase 2a: _recover_pano_heading (F-SKY11/13)     pano_registration.py
        │            water + vegetation co-registration
        │            (see "Vegetation co-registration" below)
        ├─ Phase 2b: _recover_anchor_offset                 pano_registration.py
        │            joint IoU sweep across all spin views
        ├─ Phase 3:  _register_views (per-view matcher)     pano_registration.py
        │            ├─ register_view_to_osm                pipeline.py
        │            ├─ Stages 1–7 (segmentation)           pipeline.py
        │            ├─ match_segments_to_buildings         pipeline.py
        │            └─ post-match cross-verify rescue
        ├─ Phase 3b: _smooth_matches_across_views           pano_registration.py
        │            cross-view consensus + post-swap dedup
        ├─ Phase 4:  _stitch_pano_composite                 pano_registration.py
        │            + _smooth_pano_matches_against_views   pano_registration.py
        └─ done; aggregate
  ├─ aggregate_building_heights                             pipeline.py
  │   per-view height outlier rejection inside the seed
  ├─ render PDF (_render_pdf)                               region_render.py
  └─ render HTML report                                     html_report.py / report_plots.py
```

## Where each "stage" of segmentation lives

The 7-stage segmentation pipeline (referenced in the HTML timing table):

| Stage | What | Function |
|---|---|---|
| 1 | SegFormer-b1 inference, label_map cached | `_ensure_label_map` [pipeline.py:631] |
| 2 | Morphology cleanup + glass-tower hole-fill + ground cap | `_neural_sky_and_building_masks` [pipeline.py:771] |
| 3 | Skyline contour | `detect_skyline_contour` [pipeline.py:1258] |
| 4 | Contour-based silhouettes | `detect_building_silhouettes` [pipeline.py:1635] |
| 5 | Mask-based silhouettes (peak/valley splitter) | `detect_buildings_from_mask` [pipeline.py:1866] |
| 6 | Merge silhouette sources | `_merge_silhouette_sources` [pipeline.py:2186] |
| 7 | OSM-anchored re-cut | `osm_anchor_silhouettes` [pipeline.py:2239] |
| 8 | (Optional) MobileSAM instance head | `osm_sam_instance_silhouettes` [pipeline.py:2371] |

Stage 1 is the only learned step. Stages 2–7 are classical CV on top of
the semantic mask.

## Pano path (the 360° report) — where the recent work lives

The per-seed pano is built in `pano_registration._stitch_pano_composite`, which
also runs bearing recovery and stores everything on a
`StitchedPanoResult`. Key functions added/changed 2026-06:

| Concern | Function | File |
|---|---|---|
| Sliding-window splitter (F-SKY22) | `_pano_sliding_window_split` | pano_registration.py |
| Depth (tiled, full-res per tile) | `predict_pano_depth_tiled` | depth_estimation.py |
| Per-column depth→distance | `column_building_distance` | depth_estimation.py |
| Bearing recovery (xcorr + gate) | inline in `_stitch_pano_composite` | pano_registration.py |
| F-SKY1 fundamental detection | `_floor_period_for_building` | pipeline.py |
| Water-only ground cap | `_neural_sky_and_building_masks` | pipeline.py |
| Distance scan (column-indexed) | `_render_pano_bearing_scan_png` | report_plots.py |
| Cardinal N/E/S/W pano lines | `_draw_pano_north_line_inplace` | report_plots.py |
| Heights polar plot | `_render_pano_heights_polar_png` | report_plots.py |
| OSM nearest-per-degree signal | `_build_osm_nearest_per_degree` | report_plots.py |
| Cross-correlation gate | `_bearing_xcorr_offset` | report_plots.py |

The ML height stack (DA2 loader, U-Net, providers) is in
`city2stl/skyline/height/` (moved from `city2stl/height/` 2026-06-07).

## Config: `sites/<region>.json`

Each region has a JSON config controlling:
- `north/south/east/west` — bbox for OSM fetch
- `seed_urls` — Google Street View URLs (synthetic `@lat,lon,...` or
  full Photo Sphere URLs with embedded `pano_id`). The auto-replace
  pass will swap bad ones for nearby auto-proposals at runtime.
- `anchor_offsets_deg` — manual heading overrides for seeds where the
  joint IoU optimiser hits a wrong local maximum. Drop a seed from
  this dict only after measuring; doing it carelessly costs ~14% of
  matched buildings (see [STATUS.md](STATUS.md) under "Heading recovery
  manual overrides").
- `negative_seeds` — seed names whose estimates are excluded from
  height aggregation but whose frames are still kept as labelled bad
  examples. Cartagena: `["seed_2", "seed_3"]`.
- `use_satellite_footprints` (F-SKY8), `use_cross_view_scoring`
  (F-SKY10), `use_pano_coastline_recovery` (F-SKY11.1),
  `drive_pano_recovery_anchor`, `pano_only_pdf`
- `max_plausible_height_m` — cap on plausible building height in metres
  (rejects matches that imply absurdly tall results)
- `known_heights_m` — ground-truth dict used by the validation
  diagnostic on the HTML report

## Cross-view consensus (added 2026-06-01–02)

Three layered correctness passes, all running inside
`_seed_multiview_registration`:

1. **`_smooth_matches_across_views`** (`pano_registration.py`) — after
   per-view matching, build `fid → popularity` across all the seed's
   views. For any segment whose matched `feature_id` has popularity 1
   and whose `match_diagnostics` contains a candidate with popularity
   ≥ 2, swap to the popular candidate. Mirrors the F-SKY6 dedup
   philosophy but at the cross-view level.
2. **Post-swap dedup** (inside the same function) — after swapping,
   two segments in one view may now point at the same OSM building.
   Keep the one with the higher per-fid combined score in
   `match_diagnostics`; clear the loser. Necessary because the swap
   pass doesn't enforce one-to-one match like the original matcher.
3. **`_smooth_pano_matches_against_views`** (`pano_registration.py`) —
   apply the same popularity-based swap to the stitched-pano's
   independent matcher output, so the pano page shows the same OSM
   buildings as the per-view consensus.

`seed_index` (the seed-stable badge number) is rebuilt after smoothing
so consistent numbering survives the swaps. The per-view overlay PNG
is re-rendered using `sv.raw_image` as the base — `image` is mutated
in-place via `object.__setattr__` because `SeedViewRegistration` is a
frozen dataclass.

## Water filter (3 layers)

`_drop_buildings_in_water` (`region_data.py`) applies up to three
checks; first hit drops the record:

1. **Centroid in water polygon** — the original safe check.
2. **Polygon overlap with water > 15%** — catches piers/marinas drawn
   with their footprint extending into a water polygon.
3. **Wet-side-of-coastline** (currently DISABLED). The cross-product
   sign test was caught between two failure modes in two test runs:
   - 880 m radius, "any coastline" → 2087 buildings wrongly dropped
     because one OSM contributor drew a coastline with reversed
     handedness. Run #34 disaster.
   - 550 m radius, "nearest coastline" only → dropped 38 real shore-
     side condos because OSM draws coastlines INLAND of the visible
     beach-front buildings. Run #33 regression.

   The cross-product helper was removed in the 2026-06-02 cleanup
   (no callers, traps for the unwary). If you build a more accurate
   per-building wet/dry test, reintroduce it from git history rather
   than restoring a known-fragile predicate.

## Auto-replace bad seeds (added 2026-06-02)

`_auto_replace_bad_seeds` (`seed_selection.py`, just above
`_screen_locations`] swaps a user-supplied seed for a nearby auto-
proposal when the seed's screening result is "rejected" or
`screen_score < 0.20`. Combined score for replacements is
`0.7 · auto_screen_score + 0.3 · proximity_to_original`. The
replacement adopts the original seed's `name`, `fov`, `pitch` (so
`negative_seeds`, `anchor_offsets_deg`, badge numbering still apply).

Substitutions are logged with `[auto_seed]` lines so the report can
attribute matches to the actual location used.

## bbox base cap

In `_register_views` (`pano_registration.py`) after match dedup:
each matched segment's `base_y` is compared to the OSM-projected
expected ground row (pinhole formula). If the mask-derived base is
more than `max(80, 0.18 × H)` pixels below the expected row, clip
to the expected row — catches the "mask runs through the beach to
the waterline" failure mode without harming legitimate full-tower
bboxes (the slack landed at 80 px after measuring the 40 px version
visibly truncated distant towers).

## Vegetation co-registration (F-SKY18, partial)

`_recover_pano_heading` runs the standard water-keypoint sweep against
the stitched pano's water mask, then ALSO runs the equivalent sweep
against the stitched vegetation mask using OSM
`landuse=park/grass/forest` polygons. The two score curves are
peak-weighted blended to produce the final `pano_recovered_offset`.
Vegetation only fires when `pano_veg_frac > 0.005`. Critical:
`use_base_y=True` is passed for the vegetation sweep — vegetation
keypoints are ground-plane (park edges), not horizon-level. See
`coastline_registration.score_pano_offset_keypoints`.

## Negative-seed handling

A seed listed in `sites/<region>.json::negative_seeds`:
- Frames are still captured during Phase 1
- All analysis (recover pano heading, anchor offset, registration,
  pano stitch) is **skipped** — the seed is marked
  `is_negative=True` and rendered as "bad-skyline example" rows in
  the report
- Estimates are excluded from `aggregate_building_heights`

To skip a seed end-to-end, add it to `negative_seeds`. Saves ~30s
per seed of pano-recovery + multiview compute.

## Quick troubleshooting

| symptom | likely cause | where to look |
|---|---|---|
| Buildings extend into water on minimap | OSM water polygon has a gap; polygon-overlap filter ineffective. Run a polygon-area sampling water filter (planned). | `_drop_buildings_in_water` |
| Beach drawn as red in mask | SegFormer-b1 mis-classifies sand as building class 1. Accepted: aggressive band cap cost 14% of matches. | `_neural_sky_and_building_masks` |
| bbox doesn't reach building base | Mask base is below the OSM-projected expected ground row by > 80 px → cap fires. Loosen the slack or check the OSM building's `terrain_elev_m`. | `_register_views` (bbox cap block) |
| Per-seed match count regresses after smoothing | Dedup cleared more than swap fixed. Check `match_diagnostics` for the cleared segments. | `_smooth_matches_across_views` |
| Pano page doesn't match per-view consensus | `_smooth_pano_matches_against_views` didn't fire (pano matched zero overlapping fids). | `_smooth_pano_matches_against_views` |
| Auto-replaced seeds end up worse | `good_score_threshold` (0.35) may be too lax for the region. Try raising in `_auto_replace_bad_seeds`. | `_auto_replace_bad_seeds` |

## Windows console + Unicode

**Avoid Unicode special characters in `print()` calls.** Windows cmd's
default code page is `cp1252`, which can't encode `→`, `°`, `↳`, etc.
A `UnicodeEncodeError` raised inside the orchestrator crashes the
whole pipeline. Use ASCII fallbacks: `->`, `deg`, etc. The HTML report
and PDF can use any Unicode they want — only the console `print`
statements need the discipline.

## Known dead-ends (don't repeat these)

- **"Check all coastlines within range" for wet-side filter** — one OSM
  contributor's reversed coastline → 69% of Chicago buildings dropped.
  Use nearest-coastline-only.
- **Dropping seed_1's manual `anchor_offsets_deg`** on Cartagena even
  when pano-recovery is within 6° of the manual value — joint IoU
  sweep hits the wrong local maximum and costs 54 matched buildings.
  Don't auto-replace manual offsets without measuring.
- **bbox base cap at 0.08·H slack** — clipped distant towers'
  legitimate bases (`mask_base > expected + 0.08·H` fired for normal
  views). Widened to 0.18·H + min 80 px.
- **Always-on beach band heuristic** — costing 5 matched buildings on
  Cartagena. Reverted in favour of the class-membership ground cap.
- **Ground cap including earth/sand** (reverted 2026-06-07) — using
  water+earth+sand for the waterline clip chopped the seed_5 peninsula-
  tip cluster: Cartagena's bright sandy towers get partially mislabelled
  sand, so a sand-inclusive run extends UP into the facade. Cap is now
  **water-only** and finds the waterline **bottom-up** (so distant bay
  water at the horizon doesn't clip buildings in front of it). See
  `_neural_sky_and_building_masks`.
- **MobileSAM instance head (F-SKY5) on Cartagena** — measured
  2026-06-02: zero change in extracted buildings / coverage vs F-SKY2
  alone, +79% wall time. The merged-tower cases F-SKY5 targets are
  already handled by F-SKY2's OSM-anchored splitting (or SAM's splits
  don't survive the matcher's 1:1 dedup). Stays off by default.
- **"Prune SegFormer to our 6 classes for speed"** — the 150-class head
  is a single 1×1 conv = 0.3% of params / ~0% of FLOPs; the MiT
  backbone (95.9% of params) is class-agnostic, so head-slicing buys
  nothing. **INT8 dynamic quantization** also gave no CPU speedup here
  (704→714 ms/img) and corrupted masks (building IoU 0.667 vs fp32).
  Real model-side speedup needs a smaller variant (b0 ≈ 1.3× faster,
  needs match-parity check) or ONNX-Runtime/static-quant/distillation
  (untested, higher effort). The shipped batched prefetch (2.4×) is the
  realized win.

## SegFormer performance levers (measured 2026-06-02, b1, CPU)

| lever | effect | verdict |
|---|---|---|
| **Batched spin prefetch** (`prefetch_label_maps`) | **2.4×** faster, bit-identical | shipped, default on |
| b0 instead of b1 | ~1.3× faster (527 vs 704 ms/img), 3.6× fewer params | candidate — needs Cartagena match-parity run |
| INT8 dynamic quant | 0× speedup + mask corruption (IoU 0.667) | dead end on this CPU/torch build |
| 150→6 class head prune | 0.27% params, ~0% FLOPs | worthless for speed |
| Input 512→384 | ~12% faster, ~14% match loss | poor trade (prior measurement) |
| CUDA build of torch | ~5–10× per loader notes | **blocked** — installed torch is `2.12.0+cpu` |

## Tests

CV-math unit tests live in `strm2stl/tests/test_skyline*.py` (7 files,
130 tests; **129 pass, 1 skip** as of 2026-06-07). They cover the
deterministic geometry / scoring functions; orchestration is exercised
by full-run smoke tests rather than unit tests.

```powershell
"C:\venvs\strm2stl\Scripts\python.exe" -m pytest tests/test_skyline*.py -v
```

## File map

> **F-CLEAN14 (2026-06-07):** `region_pdf.py` (was ~6500 lines) split into the
> first block below. `region_pdf.py` is now a ~700-line wiring layer that
> re-exports everything, so old import paths still work.

| file | purpose |
|---|---|
| `region_pdf.py` | `run_region_pdf_report` entry point + re-export hub. ~700 lines. |
| `pano_registration.py` | Per-seed multi-view registration loop (capture, heading/anchor recovery, match, smoothing, pano stitch, splitters, orchestrator). ~2570 lines. |
| `region_render.py` | All PDF page builders + minimap/overlay drawing + `_StepTimer`. ~1880 lines (largest fn `_render_pdf` ~556). |
| `seed_selection.py` | Auto-standoff proposal + screening + bad-seed auto-replace. ~640 lines. |
| `region_data.py` | Region bbox + OSM fetch + `BuildingRecord` build + water filter + `sites/*.json` readers. ~560 lines. |
| `streetview_io.py` | Street View Static API I/O + image cache + URL parse/sign. ~330 lines. |
| `region_types.py` | Frozen dataclasses (`RegionBBox`, `SkylinePoint`, `StitchedPanoResult`, `SeedViewRegistration`). ~145 lines. |
| `region_config.py` | Shared F-SKY env flags + `_SEGMENT_PALETTE`. ~75 lines. |
| `pipeline.py` | All segmentation / matching / aggregation primitives. ~4100 lines (largest fn `estimate_heights_from_registration` ~458). |
| `html_report.py` | Per-region HTML diagnostic report assembly. ~1220 lines. |
| `report_plots.py` | matplotlib/PIL PNG renderers for the HTML report. ~1710 lines. |
| `coastline_registration.py` | F-SKY11.1 pano-coastline keypoint sweep. |
| `osm_water.py` | OSM coastline / water / green polygon extraction. |
| `satellite_footprints.py` | F-SKY8 Microsoft Buildings polygon fetch + merge. |
| `satellite_image.py` | F-SKY10 ESRI satellite tile fetch + per-region project. |
| `cross_view.py` | F-SKY10 cross-view (rooftop colour) scorer. |
| `depth_estimation.py` | F-SKY12 monocular depth + height verifier. |
| `height_trace.py` / `height_trace_render.py` | F-SKY tag-based debug tracer. |
| `sites/*.json` | Per-region config. |
| `scripts/` | Standalone CLI entry points (e.g. `08_region_skyline_pdf.py`). |
