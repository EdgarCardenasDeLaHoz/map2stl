# F-PHOTOVIEW — building heights from elevated views of Granada

Measure Granada building heights from photos taken from the Alhambra, looking down on the
Albaicín and the city centre, and score them against the REDIAM lidar truth. Two routes: single
photos the user supplies (labelled by hand), and the Commons pipeline run on Granada's view
categories.

User request 2026-10-23: three photos (Albaicín from the Alhambra, web image without EXIF; the
Cathedral and Plaza Nueva from the Alhambra; a Wikipedia panorama of the Albaicín), "use to
improve model". Answers: photo heights pipeline, plan and build now, landmarks "I pick, you
confirm", camera unknown, Commons bulk run yes, CPU for the three photos (this PC has no GPU).

## Why the existing routes do not cover it

- `photo_heights.measure_towers` (Commons route) reads building tops against the sky. From the
  Alhambra almost every roof is seen against other roofs or the hillside; only a few towers
  (San Nicolás, the Cathedral, the San Miguel Alto mast) break the sky line.
- The drone roof fit (`_pano/roof_fit.py::fit_roof_heights`, `_pano/elevated.py::overhead_pose`)
  measures roofs below the camera, but takes a 360° `footprint_detect.Pano` with a recorded
  position. A single photo needs its own camera solve first.
- Depth Anything on photo 1 (2026-10-23): a smooth near-to-far ramp by image row; individual
  houses separate only in the front two rows (0.6 edges per row at best). Depth can order
  occlusions and check distances, not measure heights.

## Approach

1. **Camera from labels.** Mark 4–6 landmarks per photo with their OSM names; the user confirms.
   `photo_localize.solve_from_labels` gives position, heading, pitch and focal length (pinhole).
   Renders of the v8 model from Torre de la Vela (`tools/eval/render_pano_view.render` with an
   eye-height override) help pick and check landmarks.
   *Labelling a photo by hand:* run `python tools/photo_label/server.py` (port 8767), open
   `http://localhost:8767/`, pick the photo, click a point on it, then the same point on the
   satellite map (or the reverse). With 4 or more pairs, Solve draws the camera and view cone on the
   map and, on the photo, a line at each pair's predicted column plus ticks for named footprints in
   view, so a wrong pair shows as a large residual. Save writes
   `city2stl/skyline/sites/annotations/<region>_<photo>.json` (labels keep `x`, `y`, `osm_names` and add
   `lat`, `lon`, which `solve_from_labels` uses directly)).
   The "Map: 2D / 3D" button swaps the map pane for Google Photorealistic 3D Tiles (CesiumJS 1.120.0,
   loaded on first use; needs `GOOGLE_MAPS_API_KEY` in `map2stl/.env`, which the server keeps and adds
   to `/tiles3d/v1/3dtiles/...` requests). Orbit and pan to match the photo, then click (without
   dragging) to pair a point: it records `lat`, `lon` and `h_ellipsoid_m`, the WGS84 ellipsoidal
   height of the Google mesh surface, and the pair shows in both the 2D and 3D views. Orthometric
   height is the ellipsoidal height minus the geoid undulation (about +50 m in Granada); that
   conversion is not applied yet, and the solve ignores `h_ellipsoid_m`. After
   *Terrain solve (2026-10-24):* `/api/solve` seeds from the column solve, then fits lat, lon, elevation,
   heading, pitch and focal length (roll only with 8+ labels, kept when it cuts the median residual
   to 80%) on the DEM (`--dem`, default the OpenTopography tile in `cache/opentopo`), each label
   at terrain height plus a free height >= 0 for named labels (towers, roofs), `soft_l1`, about
   3 s. *Roof prior (2026-10-24):* the user clicks roof tops, so every label now gets a free height; an
   unnamed one is pulled toward the OSM height of the footprint containing it (0 outside) by a soft
   residual (`terrain_solve.PRIOR_SIGMA_M` 4 m, weight 3 px; `label_height="ground"` restores the old
   fit). Median px (m): albaicin 3.26 (1.98) to 1.96 (1.04), cathedral 14.2 (2.8) to 13.8 (2.0) with
   camera elevation 797 to 820 m and pitch -14.7 to -17.6, albaicin_pano 2.75 to 1.30; solve 2-4 s.
   The pairs table shows each height with the OSM one, e.g. `14 m (OSM 15)`. It reports residuals in px and metres and the fitted heights, and falls back to the
   column solve when no DEM covers the labels. Granada photo 3 (albaicin_pano): median 2.8 px;
   photo 2 (cathedral): median 14 px (about 3 m). "Buildings" (on after Solve, `POST /api/project`)
   draws only what the camera would see: the 150 nearest footprints in view are rasterised as
   prisms (base = lowest terrain under the ring, top = base + `height`, else `building:levels` x
   3 m, else 9 m) into an id buffer (`terrain_solve.project_buildings`), and the visible outline
   of each (faint fill, 1 px line, roof edge in white) comes back with the id buffer, which also
   serves the hover pick; hovering a building highlights it on the 2D map, clicking a footprint on
   the map highlights it on the photo. The
   photo list shows one file per folder+stem (png, jpg, webp) and hides derived outputs.
   Solve, "View from camera" flies the 3D camera to the solved position, heading, roll and field of
   view, at the surface height under it plus a nudge (default 15 m, with a pitch box).
2. **Photo adapter** (`city2stl/skyline/photo_view.py`, new): wrap one pinhole photo as a
   `footprint_detect.Pano` (heading per column, focal, pitch), run SegFormer labels, Depth
   Anything and MobileSAM instances once per photo.
3. **Roof fit.** `check_pose_from_bases` refines camera height from building feet, then
   `fit_roof_heights` measures each footprint in view. Terrain is not flat (centre about 680 m,
   Albaicín up to 780 m), so the base height of each footprint comes from the terrain model,
   not from a single ground plane; check whether `invert_top` already takes a per-footprint base.
4. **Score** against `runs/benchmark/truth/granada.json` (REDIAM, survey-only), per photo and
   by distance band; compare with the current merged heights on the same footprints.
5. **Commons bulk.** `13_photo_profiles` accepts explicit categories for a region (Granada has
   no "skyline" category); list Alhambra-view categories in `sites/granada.json`; run profiles
   and `17_photo_pipeline`; elevated photos go through step 3 instead of the tower route.
6. **Ship** only if the scored error beats the current source on the same buildings; otherwise
   record the result as a decision and keep the code behind a switch.
7. **Register without control points (requested 2026-10-24).** The user asked for it to be
   as automatic as possible, with SIFT first and a learned matcher only if SIFT is short of
   matches.
   - *Start pose without the user:*
     - Use EXIF GPS if the photo has it.
     - Otherwise take candidate viewpoints from the region: OSM `tourism=viewpoint`, terraces
       and towers within about 3 km, at the DEM height plus 2 m.
     - For each candidate, sweep the heading in 5° steps, with pitch from −25° to +5° and
       a range of fields of view.
     - Score each pose by how well the photo's skyline (from SegFormer sky) matches the DEM
       horizon, using the existing column solve (`photo_view`). Keep the best 3.
   - *Refine against the Google mesh:*
     - The browser renders the photorealistic tiles headless at each candidate, capturing
       colour and depth at the photo's size (three.js 3DTilesRenderer, already in the tool).
     - SIFT matches the photo against the render, with a ratio test.
     - Each match takes its 3D point from the depth buffer; mesh heights are ellipsoidal, so
       subtract the geoid (about 50 m at Granada).
     - `cv2.solvePnPRansac` with free focal length (a grid over f), then `least_squares` on
       the inliers.
     - Repeat render, match and solve 2–3 times.
   - *Output:* the camera and the inlier pairs, which go to the pairs table and the annotation
     as `auto` labels. The user's own labels, if any, still count, with a higher weight.
   - *Success:* photos 1–3 solve with no clicked pairs, within 10 m and 1° of the clicked
     solve, and with median reprojection under 5 px on the inliers.
   - *Risks:*
     - Lighting, season and mesh blur may leave SIFT with too few inliers. If so, ask before
       installing kornia LightGlue (pip, with weights from GitHub releases).
     - Each render loads tiles and counts against Google's quota.
     - Map Tiles terms limit storing tile content, so keep matches and camera poses, never
       saved renders or meshes.

## Target files

- `city2stl/skyline/photo_view.py` (new), `city2stl/skyline/_pano/roof_fit.py` (base height),
  `city2stl/skyline/commons_photos.py` and `scripts/13_photo_profiles.py` (categories),
  `city2stl/skyline/sites/granada.json`, `sites/annotations/granada_*.json`.
- Tests: `tests/test_photo_view.py` (synthetic render with known heights round-trips).

## Success criteria

- Each of the three photos gets a camera with landmark reprojection under 10 px.
- Heights for at least 50 footprints per photo; median error under 3 m on houses (6–12 m) within
  800 m, against REDIAM.
- The Commons run on Granada reports photos found, placed and measured.

## Risks

- At 300–1500 m one pixel is 0.3–2 m; tile roofs of houses are 6–12 m. Errors may be the size
  of the signal past about 1 km.
- No EXIF: focal length is free, so 4+ labels are needed per photo.
- No GPU on this PC: SegFormer and MobileSAM run on CPU for the three photos only; the Commons
  bulk run waits for the GPU PC.
- Web images (photos 1 and 2) stay in git-ignored `runs/photo/`; only Commons files with a
  licence are cached by the pipeline.

## Progress

2026-10-24, photo 1 (Albaicín from the Alhambra, `runs/photo/granada_albaicin/`). Steps 2–4
are built and run once. The target is not met.

- **Depth view in the photo-label tool** (2026-10-24). Tabs Photo depth / Google depth /
  Difference (`tools/photo_label/index.html::dvGoogle`, `server.py::api_depth_fit`). On photo 1
  Depth Anything fits Google's mesh depth with 4,710 pairs at 6 % median error. The Google
  render only settled once the tile proxy reused connections (`server.py::pooled_urlopen`,
  HTTP/1.1, `loopback_v6`): a cold view now settles in about 30 s instead of never. An
  Orientation tab (surfaces, wall facing, roof pitch, per-building summary written to
  `<stem>_orient.json`) is derived from the fitted depth's normals; a normals network on the
  photo is the planned second source if the depth normals prove too noisy.

- **Camera.** The camera comes from the 11 unnamed ground labels in
  `sites/annotations/granada_granada_albaicin_photo.json`. `photo_view.py::solve_from_rows`
  fits seven parameters: position, heading, pitch, roll, focal length and elevation. Each label
  sits on the terrain model `cache/opentopo/d6eb6dd762c161a4afb3471521b53e14.tif` (1 arc-second).
  - The solved camera: lat 37.177322, lon -3.592061, 778.8 m, i.e. 14.5 m above the model's
    ground at that point. Pitch -14.81°, heading 335.76°, roll -1.36°, f 553.8 px.
  - Median label residual is 2.3 px.
  - The earlier column-only solve assumes a level camera, and that put the camera about 30 m
    off.
- **Refining from building feet.** `photo_view.py::refine_from_bases` refines the camera from
  building feet (`roof_fit.check_pose_from_bases` with per-footprint bases). After five rounds it
  had not converged: the height went 78.7, 69.9, 63.7, 63.4, 63.1 m above the median base, then
  jumped back. It ended at 772.9 m and pitch -12.74°, and scored worse than the label camera.
  The label camera is the better one.
- **Models and inputs.**
  - SegFormer b3 ran on the CPU in 62 s. Classes: 66 % building, 20 % sky.
  - Depth Anything output was already cached.
  - MobileSAM did not run: the `mobile_sam` package is not installed in `~/.venvs/map2stl`. So
    no instance map was used.
  - Footprints are the 5813 OSM building ways in the cached overpass file. Their bases are the
    terrain minimum over the ring.
- **Score.** Truth is `runs/benchmark/truth/granada.json`, scored with
  `benchmark.py::scoring_truth(SINGLE_SOURCE)`. It is mostly `cnig_mdsn` (269 of 283 matched
  records); only 14 are REDIAM. No measured footprint with truth lies beyond 527 m, so the
  800–1500 m and >1500 m bands are empty.

| Run | measured | with truth | median AE | bias | AE <300 m | AE 300–527 m | houses 6–12 m (median AE, bias) |
|---|---|---|---|---|---|---|---|
| Label camera, per-footprint bases | 435 | 286 | 6.51 | +6.17 | 3.88 (n 94) | 8.20 (n 192) | 5.36, +5.73 (n 191) |
| Refined camera, per-footprint bases | 470 | 283 | 7.70 | +8.55 | 4.30 | 10.71 | 6.23, +8.10 |
| Label camera, one ground plane | 154 | 120 | 4.70 | +1.55 | 4.00 | 6.77 | 3.43, -0.15 (n 74) |

- **Error grows with distance.** On the refined run, error ≈ -17 m + 0.077·d. In the overlay,
  the over-reads (red) are the rows of houses stacked up the Albaicín slope. There, the building
  class runs unbroken from one roof into the next house uphill. Without instances or a depth
  edge, the fit takes the higher house's roof as the top: every matched top edge is `roof`, none
  is `sky`.
- **The one-plane control.** It scores better, but only because it measures fewer buildings: it
  keeps mostly the near ones with a clear base. It is not evidence that per-footprint bases hurt.
- **Merged heights.** These were not compared. They are not cached offline, and in Granada the
  merge includes `cnig_mdsn`, the truth source itself.

2026-10-24, second run: separating stacked roofs (same photo, label camera, cached SegFormer
labels and Depth Anything). The baseline above is kept as `heights_baseline.json` /
`overlay_baseline.png`; `heights.json` / `overlay.png` are now the `depth_break` variant.

- **Depth break.** Two cuts in `roof_fit.py::observed_top`, both off by default:
  - a local disparity step of 5 % (`stop_ratio=0.95`; the roof fit's default 15 % never fires
    on a 3-7 % jump from a roof to the wall of the house uphill at 300-500 m);
  - a metric cut: Depth Anything calibrated per photo as `disp = a / d + b`
    (`calibrate_depth`, fitted on the feet a plain pass saw, 375 feet, median error 5.8 %:
    `a 575.9, b -0.62`), the run rescaled so its wall sits at `d_in`, cut at the first row
    farther than `d_out + 5 m`. A first calibration on all predicted feet (occluded ones
    included) was off by 14 % and lost most footprints; the two-pass one fixed that.
  - `photo_view.measure(depth_break=True)` turns on both (`DEPTH_BREAK_KW`).
- **The geometric prior** (a roof cannot rise past the next footprint's base row) was not built:
  from above, a nearer roof hiding a farther foot bounds the near height from below, not above.
- **MobileSAM** ran on CPU (vit_t, 1299 s, 364 instances, 43 % of pixels):
  `photo_instances_pano.npy`, on the label camera's pano grid. Its instances span several
  stacked houses, and the fit never stops inside one instance, so they make things worse.
- **Step ratio sweep** (median AE): 0.85 6.51, 0.90 6.32, 0.92 5.52, 0.95 3.51, 0.97 3.63,
  0.98 4.61. The footprints measured peak at 0.95 (676, against 668 at 0.97 and 583 at 0.98),
  which picks the same value without truth.

| Run | measured | with truth | median AE | bias | AE <300 m | AE 300-527 m | houses 6-12 m (median AE, bias) |
|---|---|---|---|---|---|---|---|
| Baseline | 435 | 286 | 6.51 | +6.17 | 3.88 (n 94) | 8.20 (n 192) | 5.36, +5.73 (n 191) |
| 5 % step only | 676 | 363 | 3.51 | +0.63 | 3.41 (n 73) | 3.51 (n 290) | 3.53, -0.53 (n 252) |
| Metric cut only | 443 | 288 | 5.45 | +5.07 | 4.05 (n 94) | 6.72 (n 194) | 4.90, +4.65 (n 193) |
| **Depth break (both)** | 676 | 363 | **3.48** | +0.55 | 3.41 (n 73) | 3.49 (n 290) | 3.52, -0.63 (n 252) |
| Instances | 378 | 260 | 9.69 | +10.28 | 5.76 (n 95) | 12.69 (n 165) | 10.58, +10.36 (n 169) |
| Instances + depth break | 620 | 358 | 3.71 | +1.50 | 3.90 (n 81) | 3.63 (n 277) | 3.67, +1.07 (n 254) |

- **Open.** The near band lost 21 matched footprints (94 to 73) and turned to -2.2 m bias: the
  5 % step also fires inside some near facades. The metric cut adds only 0.03 m on top of the
  step. One photo only; the 0.95 step needs checking on photos 2 and 3 before it is trusted.

## Decisions

- Unnamed labels get a soft roof-height prior from their OSM footprint (sigma 4 m), not a fixed
  height: fixing h = OSM height scored worse (albaicin 6.25 px vs 1.96) because OSM heights are
  defaults for many footprints; ground projection biased the camera low and level.

- The camera for an elevated photo is solved from the labels' full pixel positions with the
  camera position free. The column-only solve assumes a level camera, which biases the position
  when the photo looks down. Refining height and pitch from building feet is kept as a diagnostic
  only: on photo 1 it oscillated and scored 1.2 m worse in median error than the label camera.
- Per-footprint bases enter the roof fit as a per-footprint camera height
  (`roof_fit.py::fit_roof_heights`, `cam_h`). The default (none) keeps the old single camera
  height.
- The next step is to separate stacked roofs, not to tune the camera. Options are MobileSAM
  instances (install `mobile_sam` in the venv, or run on the GPU PC) or a depth-discontinuity
  top edge. The distance trend matches the overlay's stacked roofs, and correcting pitch on the
  truth would be fitting to the answer.
- Stacked roofs on an elevated photo are separated by depth, not by MobileSAM instances
  (2026-10-24, photo 1): `photo_view.measure(depth_break=True)` cut median error from 6.51 to
  3.48 m and bias from +6.17 to +0.55 m; instances raised it to 9.69 m, because one instance
  spans several houses up the slope. The roof fit's defaults stay as they were (drone callers);
  the step ratio 0.95 is also where the measured-footprint count peaks, a choice that uses no
  truth.
