# Learned registration — layered plan

_Last updated: 2026-09-28_

Scope: how a learned component is introduced into `numpy2stl/src/numpy2stl/registration/` without
replacing the classic registration. This page covers the four layers, what already exists to build
on, and the seam a learned mask producer attaches to. The classic pipeline's own behaviour is in
`numpy2stl/src/numpy2stl/registration/docs/ARCHITECTURE.md` and `numpy2stl/docs/registration.md`.

**Status (2026-09-28)**
- Layer 0 — done, and ground truth now exists: 6 hand-aligned packs in
  `map2stl/tools/align_tool/ground_truth/` (barcelona, bilbao, lisbon, miami, paris, salzburg).
- Scale/extent — settled: export scale comes from the plate's XY extent
  (`tools/align_tool/export_align_data.py::plate_scale_m_per_unit`); plan closed in
  [scale-window-sizing-plan.md](../done/scale-window-sizing-plan.md), why in
  [registration-plate-location](../../decisions/registration-plate-location.md).
- Eval script — written: `tools/align_tool/eval_registration.py` (geometric error, registration
  quality at predicted and ground-truth transforms, segmentation IoU).
- Mask-producer seam — landed: `numpy2stl/src/numpy2stl/registration/align/mask_source.py::produce_mask`
  / `::produce_edges`, configured by `RegistrationConfig.mask_producer`.
- Layer 1 (segmentation network) — not started.
- Why layered: [registration-plate-location](../../decisions/registration-plate-location.md)
  § "2026-08-04 — The registration learning stack is layered, not end-to-end".

## Why layered rather than end-to-end

A single network regressing the transform is the obvious approach and is deliberately last. The
classic registration already works on the cities where the input masks are clean (Barcelona, Paris);
it fails where segmentation of the STL heightmap is wrong — Valencia overlap-IoU 0.025, Salzburg
0.039, Miami 0.155. That is a *mask quality* failure, not a *search* failure, so the cheapest
learned component fixes masks and leaves the solver alone. Each layer is only attempted if the one
before it proves insufficient, measured by the eval script (below), not by impression.

## Layer 0 — manual ground truth (done)

`map2stl/tools/align_tool/` produces a human-placed 2×3 affine per city. STL is the stationary frame; export
inverts to the pipeline's STL px → OSM px convention. `/api/refine` runs an ECC ladder to
fine-tune a hand placement (`tools/align_tool/refine_guess.py`, see [align-refinement](../../reference/align-refinement.md)). 8 target cities, 6 aligned so far; transforms land in
`map2stl/tools/align_tool/ground_truth/`.

Ground truth is what makes Layer 1 labels free, and is also the only thing that can score any of
the layers.

## Layer 1 — segmentation network on the STL heightmap

**Task.** Per-pixel classification of the STL-derived heightmap into `building` /
`river-water` / `terrain-hill` / `base`. CPU training, torch 2.12.0+cpu.

**Labels are free.** The OSM side is already semantically masked and needs no network:
`city2stl/osm_raster.py::get_osm_semantic_masks` returns `vegetation`, `water`,
`elevated_roadway`; buildings come from the OSM heightmap's non-NaN cells. Warp those masks into
STL pixel space with the Layer 0 ground-truth transform (`numpy2stl/src/numpy2stl/registration/align/transform.py::apply_transform`)
and every STL pixel has a label. No hand-labelling at any point.

**The solver does not change.** The network's output replaces the *input* to the existing
registration, i.e. it is a drop-in for `building_mask` / `building_edges`
(`numpy2stl/src/numpy2stl/raster/segment.py::building_mask` and `::building_edges`). Fourier–Mellin, the global centre
search, ECC refinement, the lock gate, and the scoring in `numpy2stl/src/numpy2stl/registration/align/metrics.py` are all untouched.

**Where it attaches (done).** Every STL-side mask consumer goes through the seam in
`numpy2stl/src/numpy2stl/registration/align/mask_source.py`:

| Consumer | What it produces |
|---|---|
| `numpy2stl/src/numpy2stl/registration/align/global_search.py::register_global` | `produce_edges` — the registration signal itself |
| `numpy2stl/src/numpy2stl/registration/stages/compare.py::_run_comparison` | comparison mask, polygon-ICP mask, hi-res report footprint mask |
| `numpy2stl/src/numpy2stl/registration/stages/registration.py::_polygon_register_dict` | polygon-registration fallback |

- Each producer is `ndarray → ndarray` of identical shape (checked by `mask_source.py::_validated`).
- No producer installed = the classic `building_mask` / `building_edges`, byte for byte.
- Install one for a bounded scope with `mask_source.py::use_config` (reads
  `RegistrationConfig.mask_producer`) or `::use_mask_producer`; there is no global setter.
- OSM masks never route through it: `building_mask(source='osm')` is the rasterized footprints,
  i.e. ground truth.

## OSM ↔ satellite — the same network, more data

The same segmentation idea applied to satellite RGB. Ground truth is **identity**: both rasters are
georeferenced to the same bbox, so a fetched pair is already aligned and needs no manual work at all.
This is what turns 8 hand-aligned cities into a corpus large enough to train on.

`geo2stl/sat2stl.py::fetch_satellite_tiles` (ESRI World Imagery, no API key) plus
`get_osm_semantic_masks` gives an (RGB, label-mask) pair for any bbox. Target ~50–100 cities,
auto-fetched. `tools/align_tool/export_align_data.py::fetch_sat` already wraps the fetch and resamples satellite
onto the OSM pixel grid, so OSM and satellite share pixels — that resampling is the piece worth
lifting out of the align tool into a reusable data-collection script.

Training conventions to follow rather than reinvent — the repo already has a CPU tile-training
pipeline for building heights:

- `city2stl/height/train.py::TileDataset`, loads `.npz` pairs, augments.
- `city2stl/height/train.py::combined_loss` (L1 + Sobel gradient).
- `app/server/core/height/train.py::collect_tiles`, fetches and tiles per city into
  `cache/height_tiles/<city>/<idx>.npz`, drops tiles that are >50 % NaN.
- `map2stl/tests/test_height/test_train.py` — the test shape to copy.

A segmentation trainer should reuse the `.npz` tile-cache layout and the dataset/collect split, and
swap the regression head + loss for per-class cross-entropy.

## Layer 2 — distinctive-template regions (only if Layer 1 is insufficient)

If clean masks still do not register, the failure is that the solver is matching on ambiguous
structure. Select regions that are *distinctive* rather than merely dense, and register on those.

Classic self-similarity scoring first — for each candidate patch, correlate it against the rest of
the raster and keep the ones with a sharp, isolated autocorrelation peak. A repetitive city grid
scores low and drops out on its own, which is the desired behaviour and needs no special case. Only
if that proves too weak, replace the scorer with a small patch-distinctiveness network trained on the
same tiles as Layer 1.

## Layer 3 — transform regression (last resort)

A network regressing the 2×3 affine parameters directly, trained on synthetic OSM ↔ satellite warps
(sample a known transform, apply it, learn to recover it). Unlimited training data, no ground truth
needed, but it discards the classic solver's precision and its failure mode is silent, so it is last.

Note the argument in [F-SKY10](../done/skyline/F-SKY10-non-ml-cross-view-registration.md) ("learned
descriptors don't survive top-down vs side-on") is about a 90° viewpoint change and does **not**
apply here — every layer above is top-down ↔ top-down.

## Success criteria

Nothing advances a layer without `tools/align_tool/eval_registration.py`:

- Per-city translation error in **metres** and rotation error in **degrees**, pipeline solve vs
  Layer 0 ground truth, across every city with a ground-truth pack.
- Baseline is the current pipeline. Layer 1 must beat it on the four cities that currently fail
  (Valencia, Salzburg, Miami, Prague) **without regressing** Barcelona or Paris.
- Segmentation quality reported separately from registration quality — a mask IoU against warped OSM
  labels, so a mask win and a registration win can be told apart (the eval script does this).
- `numpy2stl/src/numpy2stl/registration/align/metrics.py::score_alignment` emits `footprint_iou`,
  `overlap_iou`, `edge_iou`, `edge_lift`, `height_corr`; the eval script should aggregate those
  and the eval script aggregates them rather than computing its own.

## Known risks

- **STL over-segmentation is real but modest, and is NOT a unit bug** (resolved 2026-08-05). The
  3.83-vs-100.0 height gap is by design: the STL heightmap carries raw mesh Z units
  (`numpy2stl/src/numpy2stl/stl2numpy/heightmap.py::mesh_to_heightmap`, never converted) while the OSM one carries metres
  (100.0 is a genuine tagged height, Barcelona's Torre Glòries is
  144 m). The two are reconciled by robust linear regression inside `compare()`
  (`numpy2stl/src/numpy2stl/registration/compare.py::compare`), which fits `height_scale` and applies
  `stl_m = stl_aligned * height_scale + height_offset`; a fitted ratio near 100/3.83 ≈ 26 m/unit is
  the expected result, recorded as `ComparisonResult.height_scale_used`. Segmentation never compares
  STL heights to OSM heights, and the STL path's adaptive triangle threshold is Z-scale-invariant.
  The `cell_size_m=None` figure was measured off the real path — `cell_size_m` is computed
  unconditionally in `numpy2stl/src/numpy2stl/registration/pipeline.py::register_city_stl` and threaded through as `cell_size_m_reg`, so it is never
  None in production; it only sizes the XY top-hat kernel. Measured on the exported Barcelona
  rasters: OSM coverage 0.642, STL coverage 0.754 with the correct `cell_size_m` (0.800 with None).
  So the segmentation gap is ~0.11 absolute, not the 83.3 % blow-out previously recorded — worth a
  network's attention, but not a one-line fix hiding underneath one.
- 6 hand-aligned cities is a very small corpus for the STL branch. The satellite branch exists partly
  to carry the representation learning; if it does not transfer, Layer 1 is data-starved.
- CPU-only training bounds model size and tile count. The existing height model is 22 k parameters —
  the U-Net should be sized in that spirit, not at reference-implementation scale.
- `building_mask(source='osm')` is literally `~isnan(heightmap)`. Never `nan_to_num` a heightmap
  before segmentation or every cell becomes a building and every IoU reads 0.0. This has already
  bitten the export path once.
- Rotation capture range of the classic refiner is weak (~3° seeds only recover on the finest blur
  rung). Better masks may not fix that; it may need its own work.

## Immediate next steps

- ~~Settle the scale/extent problem~~ — done, see [scale-window-sizing-plan.md](../done/scale-window-sizing-plan.md).
- ~~Write the eval script~~ — done (`tools/align_tool/eval_registration.py`).
- ~~Resolve the 3.83 vs 100.0 unit mismatch~~ — not a bug, see "Known risks".
- ~~Add the mask-producer seam to `RegistrationConfig`~~ — done (`mask_producer`).
1. Align the remaining cities (Valencia, Prague and the other micropolitans) in
   `map2stl/tools/align_tool` and save ground truth.
2. Re-export every aligned city with `tools/align_tool/export_align_data.py` and record the
   baseline numbers from the eval script.
3. Start Layer 1: a segmentation producer trained on warped OSM labels, installed via
   `mask_source.py::use_config`.
