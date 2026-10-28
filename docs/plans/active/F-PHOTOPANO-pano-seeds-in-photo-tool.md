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
  `tools/photo_label/regions.json`. `server.py::pano_seed` only reads the
  `02_streetview/<site>/<site>_<HHH>.png` layout. `screen.py` would flag 2688×540 strips as
  panoramas.

## Approach

1. **Import, not live reads.** A new `tools/photo_label/pano_import.py --region R` copies each
   seed's frames, its stitched strip and its mask PNGs into `runs/<region>/03_seeds/seed_<N>/`.
   It writes `pose.json` per seed and `views.json` with the per-frame heading/pitch/fov. It
   reads the pipeline's caches through its own readers (`stage_cache.py`, `local_dirs.py`,
   `sites/*.json`) and never writes there. The output is git-ignored. The import reruns when
   the pipeline reruns.
2. **List and seed.** `regions.json` gets a `seeds` root (Cartagena) and a `panoview` root
   (Granada). `server.py::pano_seed` gains a branch that reads `pose.json`/`views.json`, or
   `manifest.json` for panoview. The camera source becomes `pano_anchor`, used as a
   registration anchor. `screen.py` exempts photos with a known pose.
3. **Panorama overview.** On a frame, a strip view in `index.html` shows the stitched
   panorama with the frames' extents marked; clicking a frame opens it. This is read only: no
   cylindrical camera in the solver.
4. **Mask overlay.** A new layer in the depth view tabs (`index.html::DV_TABS`) shows the
   pipeline's sky/building mask from the imported `_seg` images. It is cropped to the frame
   from `views.json`.
5. **Heading calibration.** The heading recovered by the pipeline is shown per seed, next to
   the frame-name heading. A "Re-run heading" action calls
   `_pano/heading.py::_recover_pano_heading` in a job (GPU queue, `wait_for_gpu`) and stores
   the result in `pose.json`. The pipeline's own caches stay as they are.
6. **Per-building heights.** The pipeline's measured heights per footprint, read from its stage
   cache or report, are shown in the building list and on the map next to the model height.

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
