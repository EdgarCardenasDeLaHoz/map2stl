# F-PHOTOPANO — Panoramic-pipeline seeds and capabilities in the photo-label tool

Scope: bring the skyline pipeline's panorama seeds (their view frames, stitched panoramas and
known poses) into the photo-label tool, with the pipeline's mask, heading and height results
shown there. The tool reads the pipeline's outputs; it never edits `city2stl/skyline/`.

Status: approved 2026-10-26, steps 1–2 first (import and list), then 3–6. Old `02_streetview` root dropped from `regions.json` (files kept). User choices (2026-10-26 form):
- **Images:** Cartagena seed views, Cartagena stitched panoramas and the Granada panoview frames.
  The old `02_streetview` crops are not wanted.
- **Panorama format:** both. Single frames are used for registration, and the stitched strip is
  a read-only overview.
- **Capabilities:** known pose as anchor, sky/building mask overlay, heading calibration and
  per-building heights.
- **Ownership:** `city2stl/skyline` is read only (owned by the panoramic session).

## Audit (2026-10-26)

- **Seeds:** `city2stl/skyline/sites/cartagena.json::seed_urls` has 6 seeds.
  - Seeds 1, 4 and 5 are drone Photo Spheres with `anchor_offsets_deg`.
  - Seeds 2 and 3 are negative seeds.
  - Granada has no skyline seeds.
- **Frames:** 12 views per seed, 30° apart, 640×540, captured by
  `_pano/capture.py::_capture_pano_views`. They are kept in the external image cache
  (`local_dirs.py::local_dir`, `C:\Users\eac84\data\skyline-local\image_cache`), keyed by
  request hash.
- **Stitched panoramas:** cylindrical arrays in the external `stage_cache/pano`
  (`_pano/stage_cache.py`). 13 are rendered as 2688×540 PNGs, each with `_seg`, `_depth` and
  `_recon` variants, in `city2stl/skyline/runs/region_reports/Cartagena_skyline_report/assets/pano/`.
- **Poses:**
  - lat/lon/`pano_id` come from `city2stl/skyline/runs/seed_resolution_cache.json`.
  - North comes from `anchor_offsets_deg` or `_pano/heading.py::_recover_pano_heading`.
  - The drone pose and horizon tilt are fitted and kept in the stage cache (`pose`).
- **Granada:** `runs/panoview/granada/images/` holds 6 frames of 640×640, with pose in
  `manifest.json`.
- **Why the tool misses them:** every one of these lies outside the photo roots in
  `tools/photo_label/regions.json`. `seeds.py::pano_seed` only reads the
  `02_streetview/<site>/<site>_<HHH>.png` layout. `screen.py` would flag 2688×540 strips as
  panoramas.

## Progress (audit 2026-11-01)

| Step | State |
|---|---|
| 1 Import (`pano_import.py`) | Superseded: the panoramic session exports seeds into `runs/cartagena/03_seeds/` itself; the module was never built. |
| 2 List and seed | Done (`seeds` and `panoview` roots, `seeds.py::pano_seed`). A known pose no longer exempts a photo from screening; only the aspect rule is skipped. |
| 3 Read-only strip | Superseded by extension A+B (cylindrical camera). |
| 4 Mask overlay tab | Not done. |
| 5 Heading re-run | Not done; in the GPU PC queue (low priority). |
| 6 Per-building heights | Not done as a tool view; extension E covers the comparison. |
| Ext. A, B | Done (`pano_camera.py`, strip listed with a `pano_cylindrical` pose). |
| Ext. C depth | Done on the CPU (user, 2026-10-31) for 48 frames and 4 strips. |
| Ext. D missing heights | Not started. |
| Ext. E strip vs frames | Not started. |
| Ext. F auto-correct | Coded (`mask_heights.py`); the Google 3D yardstick `mask_heights.py::google3d_yardstick` is a TODO stub. |

## Approach

1. **Import, not live reads.** A new `tools/photo_label/pano_import.py --region R` copies each
   seed's frames, its stitched strip and its mask PNGs into `runs/<region>/03_seeds/seed_<N>/`.
   It writes `pose.json` per seed and `views.json` with the per-frame heading/pitch/fov. It
   reads the pipeline's caches through its own readers (`stage_cache.py`, `local_dirs.py`,
   `sites/*.json`) and never writes there. The output is git-ignored. The import reruns when
   the pipeline reruns.
2. **List and seed.** `regions.json` gets a `seeds` root (Cartagena) and a `panoview` root
   (Granada). `seeds.py::pano_seed` gains a branch that reads `pose.json`/`views.json`, or
   `manifest.json` for panoview. The camera source becomes `pano_anchor`, used as a
   registration anchor. `screen.py` exempts photos with a known pose.
3. **Panorama overview.** On a frame, a strip view in `index.html` shows the stitched
   panorama with the frames' extents marked; clicking a frame opens it. This is read only: no
   cylindrical camera in the solver.
4. **Mask overlay.** A new layer in the depth view tabs (`tools/photo_label/static/state.js::DV_TABS`) shows the
   pipeline's sky/building mask from the imported `_seg` images. It is cropped to the frame
   from `views.json`.
5. **Heading calibration.** The heading recovered by the pipeline is shown per seed, next to
   the frame-name heading. A "Re-run heading" action calls
   `_pano/heading.py::_recover_pano_heading` in a job (GPU queue, `wait_for_gpu`) and stores
   the result in `pose.json`. The pipeline's own caches stay as they are.
6. **Per-building heights.** The pipeline's measured heights per footprint, read from its stage
   cache or report, are shown in the building list and on the map next to the model height.

## Extension 2026-10-31: pano camera, depth, missing heights, pano vs frames

Status: approved 2026-10-31 (A+B first, then D, then C+E after the Commons run; seed 6 included, flagged as a weak pose). The seed export landed 2026-10-27 (seeds 1, 4, 5
and 6, 12 frames each, `pano.png` / `pano_seg.png` / `pano.json` / `heights.json` per seed).
User choices (2026-10-31 form):
- **Missing height:** a footprint visible from the seed with no entry in its `heights.json`.
- **Yardsticks:** Google 3D mesh top at the same pose, `published_m`, and cross-seed agreement.
- **Depth:** after the 57-photo Commons auto-register run finishes (GPU contention).
- **Pano:** a full panorama camera; register and label on the strip (this replaces the
  read-only strip of step 3).

Approach:
- **A. Cylindrical camera** `tools/photo_label/pano_camera.py`: column to heading
  (`deg_per_col`, a column-0 heading), row to elevation angle (`f_px`, pitch), with
  `project` / `backproject` matching `PinholeCamera`'s interface. `pano.json` notes the PNG is
  rolled (north mid-strip) and that `col0_heading_deg` refers to the un-rolled strip, so the
  column-0 heading is fitted from the 12 frames (each frame lands on known columns) and
  checked before anything relies on it.
- **B. Strip in the tool:** each `pano.png` is listed as a photo with a `pano_cylindrical`
  pose. `terrain_solve.py::project_buildings` and `_solve` take either camera. Pairs on the
  strip solve heading offset, pitch and height. Auto register renders Google 3D at the 12 frame
  poses (pinhole, as now) and maps the matches onto strip columns, so `register.py` is reused
  unchanged.
- **C. Depth** for the 48 frames and the 4 strips (`_compute_depth`, GPU queue), run after the
  Commons batch.
- **D. Missing heights:** `project_buildings` over 360° from each seed, minus that seed's
  `heights.json`, listed per seed and drawn on the map.
- **E. Per-building heights, both ways:** the building top row (from the sky/building mask, with
  depth giving distance) is converted to a height. This is done (1) per frame, with the frame
  camera, and (2) on the strip, with the cylindrical camera. Each is scored against the three
  yardsticks. Output: `runs/cartagena/03_seeds/compare.json` and a table in the tool, then a
  decision entry stating whether strip, frames or neither is better.

- **F. Auto-correct loop** (added 2026-10-31, user: start now, apply automatically when 2+
  views agree; ask the panoramic session to relax withholding only after scoring). Per frame,
  with the frame's pinhole camera: project every footprint, read the sky/building mask's top
  edge over its columns, and classify it `ok` / `too_short` / `too_tall` / `unmatched_building`
  (mask building with no footprint). Turn each mask top into a height (elevation angle x
  distance from the pose). Keep a height only where 2+ frames or seeds agree (log ratio within
  ln 1.25); when an outline rises into sky but a neighbouring footprint lines up with the mask,
  move the reading there. Agreed readings are written automatically as method `photo_mask` to
  the region's satellite readings file (they cannot publish over `city2stl/skyline` rules), and
  scored against the 7 published Bocagrande towers and the Google 3D mesh. New module
  `tools/photo_label/mask_heights.py`; tool wiring waits for A+B.

Target files: `tools/photo_label/pano_camera.py` (new), `terrain_solve.py`, `server.py`
(`pano_seed`, listing, a `/api/seeds/compare` route), `index.html` (strip canvas, wide-photo
layout), `register.py` callers only, tests `tests/test_photo_label_pano_camera.py`. No edits to
`city2stl/skyline/`.

Success: a strip opens in the tool with buildings projected on it lining up within ~1° of
the frames; both height sets exist for every seed building; the comparison table states the
error against each yardstick and which method wins.

Risks: the strip column-0 heading is unknown until fitted. Seed 6's pose is the weak
`overhead` branch. Google 3D has building-top bias where tiles are coarse. Depth Anything
is relative and needs per-photo fitting against the Google render (`api_depth_fit`).

## Target files

`tools/photo_label/pano_import.py` (new), `tools/photo_label/regions.json`,
`tools/photo_label/server.py` (`pano_seed`, `_photo_files`, routes for views/mask/heights),
`tools/photo_label/screen.py`, `tools/photo_label/index.html`, tests
`tests/test_photo_label_pano_import.py` and additions to `tests/test_photo_label_server.py`,
`docs/index/photo-label.md`.

## Success criteria

- The Cartagena list shows each seed's frames, grouped by seed. Opening one starts from the
  pipeline's pose with no guess search, and the mask overlay lines up with the photo.
- The Granada list shows the 6 panoview frames with their manifest pose.
- No file under `city2stl/skyline/` changes (checked with `git status`).
- Heading calibration result per seed is visible, and frames of one seed share it.

## Risks

- Frames in the image cache are keyed by request hash: matching a frame to a seed and heading
  needs the pipeline's request builder (`streetview_io.py::_streetview_image`). If a frame is
  missing from the cache, the import reports it and does not fetch (no keys used).
- Drone sphere poses are fitted and may differ from the URL pose; the import keeps both.
- Stage cache formats may change under the panoramic session; the import fails loudly on an
  unknown version.

## Decisions

(none yet)
