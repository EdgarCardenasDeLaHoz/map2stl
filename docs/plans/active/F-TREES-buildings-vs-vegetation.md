# F-TREES — tell buildings from trees and terrain bumps in printed city models

**Requested:** user, 2026-10-03 ("proceed" on the neural-network review, which ranked this
first). Part of F-STL2NUMPY ([plan](F-STL2NUMPY-decompose.md)): the building table
over-detects on prints that carry trees (Salzburg: 51 % of the plate called built, OSM 25 %).

## Goal

Each region the building table finds gets a probability of being a building rather than
vegetation or another raised thing (viaducts, terrain bumps); the table keeps buildings only.
Measured by footprint IoU against OSM on the 13 registration packs, leave-one-city-out.

## Approach

1. **Labels from OSM, per region** (`tools/align_tool/data/<slug>`: the plate's
   `stl_relief.npy`, `register_transform`, `osm_buildings.npy`, and
   `city2stl.osm_raster.get_osm_semantic_masks` for vegetation and elevated roads, as
   registration's `semantic_exclusion` uses them). A region over half covered by OSM
   buildings is a building; one under a tenth covered and over half under vegetation or a
   viaduct is not; the rest is left out of training.
2. **Features per region** in `numpy2stl` (geo-free): height, area, roof plane RMS and slope,
   surface roughness (second differences), boundary wall sharpness (how abruptly it rises),
   compactness and rectangularity of the footprint.
3. **First model: gradient boosting** (scikit-learn), leave-one-city-out. Ship it if it lifts
   IoU on most packs.
4. **Only if that falls short: a small U-Net** on surface-height tiles (GPU), trained on the
   same OSM labels per cell.

## Target files

- `numpy2stl/src/numpy2stl/stl2numpy/buildings.py` (region features, a `keep` filter)
- `map2stl/tools/ml/` or `claude/scripts/` (training and evaluation), model in `map2stl/models/`
- tests in `numpy2stl/tests/test_stl2numpy_buildings.py`

## Success criteria

- Footprint IoU against OSM above the registration mask's (today's) on at least 10 of 13
  packs, leave-one-city-out, and no pack worse by more than 0.03.
- Salzburg's built fraction within 10 points of OSM's.

## Risks

- Label noise: registration error (a few cells), OSM gaps (missing buildings counted as
  "not building" are excluded by the 10 % rule but not entirely).
- Block-level prints (Valencia, Barcelona) merge buildings and courtyards; region features
  of whole blocks differ from single buildings.
- 13 cities is a small set for leave-one-out; per-region samples number in the tens of
  thousands, but cities differ in print style.

## Progress

2026-10-03, first two attempts (scripts `claude/scripts/trees_eval.py`, `trees_cells.py`;
features `numpy2stl/stl2numpy/buildings.py::region_features`). Both failed:

- **Per region, gradient boosting, leave-one-city-out:** AUC 0.51-0.82 (most 0.5-0.6);
  filtering lowered IoU on 12 of 14 packs. Cause, from Salzburg's map
  (`claude/e2e/trees/salzburg_regions.png`): canopies break into many small regions (their
  steps read as walls), buildings into merged blocks, so region features compare fragments
  with blocks.
- **Per cell, window texture at 3 scales:** AUC 0.48-0.90 (half the cities at chance);
  better than today on 1 of 14.
- **Suspected cause: the labels**, not only the features. OSM is placed on the plate by the
  registration (correlation 0.28-0.81 per pack) and OSM vegetation polygons are land use,
  not printed trees. At 5-10 m cells (the packs' 512 px grid, coarser than the print)
  canopy texture is mostly gone.
- The region features themselves work on a controlled case (test
  `test_region_features_tell_a_roof_from_a_canopy`).

Options to continue (user to choose): re-grid the packs from the STLs at the print's own
resolution (1-2 m, z-buffer) and check label alignment per tile; hand-label a few tiles as
clean truth; or a small U-Net, which would face the same label noise.

2026-10-03, third attempt: print resolution (user chose "Re-grid at print resolution";
script `claude/scripts/trees_fine.py`, grids in `claude/e2e/trees/fine/<slug>_v3.npz`).
11 packs (the Boston, Denver and Paris miniatures are OneDrive cloud-only, not downloaded).

- **Re-grid works:** each STL rendered with `mesh_to_heightmap(method="zbuffer")` at
  512·k (1.5-1.7 m cells); its block max matches the pack's own render at r 0.93-1.00, and
  OSM burned at the same resolution matches the pack's OSM raster at IoU 0.82-0.94 (after a
  `flipud`: the packs are south-up).
- **The packs' `register_transform` is the main label error:** printed buildings vs OSM
  were 50-170 m apart, not uniformly (Salzburg's quadrants 6-35 px apart), so a per-pack
  affine ECC fit of OSM onto the raised cells (`trees_fine.py::refine_labels`) was added.
  It worked on 5 packs (ECC ≥ 0.48; Salzburg raised-vs-OSM IoU 0.195 → 0.450, Bilbao
  0.476 → 0.680) and failed on 5 (Barcelona, Lisbon, Paris, Prague, Valencia, ECC ≤ 0.14:
  dense block prints with no distinctive layout).
- **Scores (leave-one-city-out, per cell, texture at 4/10/20 m):** on the aligned packs AUC
  0.76-0.91 (Bilbao, Miami, Philadelphia, Salzburg), at chance elsewhere. Still, keeping
  cells with p ≥ 0.5 beat keeping all raised cells on **1 of 11** (Miami, 0.452 → 0.498),
  also when trained on aligned packs only (`--min-ecc 0.4`). The ranking is real but every
  cut removes more building cells than tree cells: trees are a small share of raised cells,
  and courtyards, roof edges and small buildings score like canopy.
- **Not shipped:** no keep-filter in `building_table`. Remaining options: hand-labelled
  tiles as clean truth (would also settle how much of the shortfall is label noise), or a
  small U-Net trained on the 4-5 aligned packs, scored on the rest.

2026-10-03, fourth attempt: a small U-Net on the GPU (user chose "Small U-Net on GPU";
script `claude/scripts/trees_unet.py`). Inputs per cell: height above ground and the surface
high-pass (DSM minus a 10 m blur); target: the refined OSM building mask; loss on raised cells
only. Trained on the 5 packs with ECC ≥ 0.4 (Bilbao, Granada, Miami, Philadelphia, Salzburg),
each held out in turn; one model on all five scores the other 6. Tiled fp32 prediction
(autocast gave NaN).

| held-out pack | ECC | AUC | IoU raised → kept | built fraction OSM / kept |
|---|---|---|---|---|
| Salzburg | 0.55 | 0.94 | 0.450 → **0.645** | 0.22 / 0.18 |
| Bilbao | 0.78 | 0.93 | 0.680 → **0.778** | 0.38 / 0.35 |
| Philadelphia | 0.75 | 0.92 | 0.703 → **0.748** | 0.46 / 0.39 |
| Miami | 0.59 | 0.88 | 0.452 → **0.540** | 0.22 / 0.20 |
| Granada | 0.48 | 0.67 | 0.241 → 0.157 | 0.29 / 0.08 |
| Barcelona | 0.14 | 0.56 | 0.383 → 0.324 | 0.49 / 0.38 |
| Lisbon | 0.07 | 0.52 | 0.340 → 0.257 | 0.37 / 0.32 |
| Paris | 0.07 | 0.53 | 0.352 → 0.291 | 0.45 / 0.39 |
| Philadelphia (miniature) | 0.39 | 0.52 | 0.348 → 0.297 | 0.45 / 0.42 |
| Prague | 0.05 | 0.56 | 0.296 → 0.275 | 0.39 / 0.38 |
| Valencia | 0.10 | 0.52 | 0.269 → 0.248 | 0.31 / 0.41 |

- **Better on 4 of 11**, all four on packs whose labels aligned (ECC ≥ 0.55): the U-Net learns
  trees vs buildings where the truth is clean. Salzburg's built fraction (0.18 vs OSM 0.22) now
  meets the second success criterion.
- **Worse on 7**, by 0.02-0.08. Six have labels that never aligned (ECC 0.05-0.39) and are
  scored against OSM that is 50-170 m off, so their AUC at chance may be the labels, not the
  model; Granada (ECC 0.48,
  AUC 0.67) is a real loss: its terraced hillside buildings are cut as canopy.
- **Criterion 1 not met** (10 of 13, none worse by > 0.03). Not shipped; no keep-filter in
  `building_table`.
- **To settle it:** clean truth for the misaligned packs (hand-labelled tiles, or a better
  registration of the dense block prints) would show whether the 6 losses are real. Applying the
  filter only when the print has distinctive tree texture (a confidence gate) is the other path.

## Decisions
