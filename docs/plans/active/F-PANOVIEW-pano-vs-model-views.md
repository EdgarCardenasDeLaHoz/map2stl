# F-PANOVIEW — Panorama vs model views, and an export height score

_Approved 2026-10-20 (user). Status: in progress. Proposal: [F-PANOVIEW](../../proposals.md)._

Gives two checks of a finished city export: a numeric height score against survey lidar truth,
and side-by-side pictures of a Street View panorama next to the model rendered from the same
camera.

## Goal

- Score an export's building heights against `runs/benchmark/truth/granada.json` (REDIAM 1 m
  nDSM, 13,132 records), before and after a height change.
- Show about 6 Granada views (cathedral x2, Alhambra / Carlos V x2, Plaza Nueva, Puerta Real) as
  two-panel PNGs: panorama | model render, with skyline edges overlaid.

## Approach

1. `tools/eval/score_export_heights.py`: match export buildings to truth footprints by IoU >= 0.5
   (as `city2stl/panorama_heights.py` does); report MAE, bias, share within 25 %, split by
   `height_source`. Works on a city-model cache `.npz` or an export's features.
2. `tools/eval/pano_manifest.py`: one paid Street View metadata + image fetch per view (about 6,
   user-approved 2026-10-20, about $0.10), saved with pano id, lat/lon, heading, pitch and FOV to
   `runs/panoview/granada/manifest.json`. Images cache to disk; reruns never refetch.
3. `tools/eval/render_pano_view.py`: load the exported mesh, place a camera at the pano position
   (eye 2.5 m above terrain), same heading / pitch / FOV, render offscreen; compose the two-panel
   PNG with skyline edges from both.

## Success criteria

- Score runs in under a minute on Granada and prints one table per `height_source`.
- Six PNGs where the cathedral tower and the Alcazaba line up within a few degrees of the photo.

## Risks

- Street View camera pose error (a few metres, a degree or two of heading).
- Model scale: renders must undo the print's vertical exaggeration.
- Paid calls: never more than the approved six without asking.
