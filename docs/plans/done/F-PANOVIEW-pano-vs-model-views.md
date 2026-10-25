# F-PANOVIEW — Panorama vs model views, and an export height score

_Approved 2026-10-20 (user). Status: done 2026-10-23. Proposal: [F-PANOVIEW](../../proposals.md)._

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

## Results (2026-10-23)

- **Export:** a fresh Granada city export (DEM 1000 px SRTMGL1, `mm_per_px` 1.0, 999 x 721 mm)
  is `cache/city_models/2ee6aaa3538a0aa6a183872e7739a6f56deb5f0c.npz`, buildings
  `cache/city_polygons/db1dfbff27f1…`; both are now the renderer's defaults (the previous default
  npz was gone). Outputs in `runs/panoview/granada/` (gitignored): `images/`, `view_<n>_v2.png`, `score_*.json`.
- **Panos:** 6 of 6 paid requests used (`manifest.json` `paid_requests`).
- **Views:**
  - 2 Gran Vía, 5 Plaza Nueva, 6 Puerta Real: street-canyon skylines match the photos.
  - 3 Alcazaba from the Plaza de los Aljibes: aligned in heading, but the towers look half height.
    The tower meshes are right (Vela, Homenaje, Quebrada 25–29 m above their base). The camera is
    wrong: the model's SRTM terrain puts the plaza about 15 m above the tower bases, while the
    IDEE MDT 5 m ground model has it level (Aljibes 775 m, Homenaje 776 m, Vela 767 m). This is a
    terrain fault in the export, see [issues](../../issues.md) "SRTM terrain on the Alhambra hill".
  - 1 Cathedral (Pasiegas): the pano stands against the facade and inside the Sagrario's OSM
    footprint. `render_pano_view.py::Scene.outside` now moves such a camera just outside the
    footprint (`CLEAR_M`), but the wall still fills the frame: a poor view for a footprint-scale model.
  - 4 San Nicolás: trees hide the Alhambra in the photo; poor view, not a model fault.
- **Score on the rebuilt truth** (`score_2026-10-23_truth-cnig.json`; truth 12,805 CNIG +
  327 REDIAM records, 11,365 matched):
  - intended `height_m`: MAE 3.04 m, bias +2.22 m, 66 % within 25 % (old REDIAM truth: 6.60 /
    +6.21 / 26 %); without the survey-filled rows 3.26 / +2.55 / 63 %.
  - `osm_levels` (9,979 rows) reads +2.5 m high; `survey` rows 1.39 / -0.24 / 89 % (not
    independent: CNIG fills them).
  - mesh-measured: MAE 5.9 m, bias -0.5 m, 44 % within 25 % (old truth 7.93 / +3.42 / 16 %).
- **Criteria:** the Gran Vía / Plaza Nueva / Puerta Real skylines line up; the cathedral tower
  view and the Alcazaba view do not, for the two reasons above. Neither is a building-height error.
  The score runs in well under a minute.
- **Left open (not this plan):** the SRTM terrain on the Alhambra hill ([issues](../../issues.md));
  `osm_levels` reading +2.5 m high on the CNIG truth.
