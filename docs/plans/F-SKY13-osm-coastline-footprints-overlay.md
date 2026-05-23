# F-SKY13 — OSM-coastline registration + footprints-view overlay

Proposal entry: `docs/proposals.md` F-SKY13

## Goal

Use **OSM as the primary coastline ground truth** for
pano↔geography registration, and surface the result directly
on the per-seed **footprints minimap** in the PDF report.

Two coastline sources are involved:

- **OSM coastline** (PRIMARY, this proposal) — `natural=coastline`
  linestrings and `natural=water` / `place=sea` polygons.
  Vector data, no thresholding, no model errors. Fetching is
  already supported in `city2stl/fetch.py:122`.

- **Pano coastline** — from the stitched-pano SegFormer water
  mask (classes 21/26/60). Independent observation from the
  street-level images, used to register pano heading against
  OSM.

The **satellite HSV coastline** that F-SKY11.1 currently
uses (`detect_sat_water_mask` in
`coastline_registration.py`) is **unreliable** and is being
demoted. It produces noisy boundaries on dark water, shadows,
boat traffic, and sediment plumes. F-SKY11.1's existing
satellite-vs-pano registration stays in place for backward
compatibility, but OSM is the trusted target going forward.
If satellite-derived coastline is ever needed again (e.g.
where OSM is sparse), it must be a **trained model
supervised by OSM ground truth** — tracked separately as
F-SKY14. See feedback memory
`feedback_satellite_coastline_hsv_unreliable`.

The user value is **diagnostic**: registering pano against
OSM directly removes one error source (the satellite HSV
mask) and lets the user visually verify pano↔OSM agreement
on the minimap.

## Approach

### Step 1 — OSM coastline accessor

New module `city2stl/skyline_cv/osm_water.py` (kept separate
from `coastline_registration.py` because the OSM fetch path
and the keypoint sweep are independent concerns):

```python
def fetch_osm_coastline(
    bbox: BBox,
    seed_lonlat: tuple[float, float] | None = None,
    radius_m: float = 1000.0,
) -> dict:
    """Return OSM coastline + water polygons within ``radius_m`` of seed.

    Reuses ``city2stl.fetch.fetch_osm_data`` (already cached) and
    filters to natural ∈ {water, coastline, bay, strait}. When
    ``seed_lonlat`` is given, additionally clips features to the
    1 km circle. Returns ``{"coastline": [LineString...], "water":
    [Polygon...]}``.
    """
```

The 1 km window matters because at greater distances:
- the SegFormer water reach is only ~5–7 m (per F-SKY11.2
  failure analysis)
- the pinhole projection of distant coastline keypoints
  becomes a sub-pixel line, useless for visual verification
- OSM coastline data at city scale is typically dominant near
  the shoreline only

### Step 2 — Project OSM coastline into pano

For each OSM `coastline` linestring, project each vertex
into the stitched pano using
`coastline_registration.project_lonlat_to_view` (already
exists). Use the same recovered heading from F-SKY11.1
so the projected OSM coastline overlays the SegFormer
water mask in pano space.

### Step 3 — Score: OSM ↔ pano agreement

Reuse `score_pano_offset_keypoints` machinery but feed OSM
keypoints (sampled along each coastline polyline at fixed
arc-length spacing) instead of satellite-derived keypoints.

**OSM becomes the primary registration target.** The
existing satellite-HSV score remains computable for
backwards compatibility but is **demoted to a fallback**
when OSM has no coastline within 1 km (inland seeds). The
PDF will quote `score_osm` as the main number; the
satellite score, if shown at all, is captioned as
"satellite (HSV — unreliable, fallback only)".

### Step 4 — Footprints-view overlay

In `region_pdf.py`, the `_render_seed_minimap` function
(around line 2630) currently draws OSM building footprints
in greyscale with matched ones coloured. Extend it to:

a. **Satellite image background** — under the footprint
   polygons. The ESRI tile is already fetched by the
   pipeline (`satellite_image.py`); reuse the cached tile,
   warp to the minimap's projection, plot at low alpha
   (~0.5) so the building polygons remain readable.

b. **OSM coastline** (PRIMARY) — overlay the
   natural=coastline linestrings within the 1 km window as a
   solid blue line (~2 px), distinct from the matched-building
   colours. This is the trusted reference.

c. **Pano-projected coastline** — overlay the SegFormer
   pano water boundary, projected back to lat/lon using the
   recovered heading, as a dashed orange line. Agreement
   with the OSM line is the registration signal.

d. **Satellite HSV coastline** (OPT-IN, default OFF) —
   only rendered when a debug flag is set. Drawn as a thin
   dotted cyan line with a "(unreliable)" label. Kept for
   diagnostic A/B comparison while F-SKY14 (trained
   detector) is pending. Do not display on production PDFs.

e. **1 km circle** — a dashed grey circle at exactly
   1 km from the seed to make the consideration window
   visible.

The three overlaid coastlines being identical means
"registration confirmed across all sources". Divergence is
exactly the diagnostic the user is asking for.

### Step 5 — Add small text block

Near the existing minimap legend, add a 2-line summary:

```
Heading recovered: 314° (OSM-aligned)
Coastline IoU pano↔OSM: 0.78
```

The pano↔OSM IoU (in the minimap's projected space) is the
single-number registration confidence. Satellite-HSV IoU is
only reported behind the debug flag.

## Target files

- `city2stl/skyline_cv/osm_water.py` (NEW) — feature
  extraction (`extract_coastline_features`,
  `extract_water_features`), 1 km clipping (`clip_to_radius`),
  keypoint sampling (`sample_coastline_points`).
  **Reuses** `pipeline.lonlat_to_local_m` (promoted from
  private `_lonlat_to_local_m` in this work) for distance
  metric so the projection matches the rest of skyline_cv.
- `city2stl/skyline_cv/pipeline.py` — promote
  `_lonlat_to_local_m` to public `lonlat_to_local_m` (new
  alias) so other skyline_cv modules can reuse it instead of
  duplicating the equirectangular formula.
- `city2stl/skyline_cv/coastline_registration.py` — add
  `score_osm_pano_offset` (mirrors the existing satellite
  score function but takes OSM keypoints). **Phase A.2.**
- `city2stl/skyline_cv/region_pdf.py` — added
  `_draw_osm_coastline_overlay` helper + call site in
  `_draw_view_minimap`. Gated by `SKYLINE_CV_F_SKY13` env
  var (default ON). Phase A.2 will add the satellite-image
  background, the pano-projected coastline overlay, and the
  pano↔OSM IoU summary text block.
- `tests/test_skyline_cv_osm_water.py` (NEW) — unit tests on
  the fetch-result extraction + 1 km clip + keypoint
  sampling, plus matplotlib smoke tests for the minimap
  overlay.

## Success criteria

Phase A is successful if:
- OSM coastline data is fetched and clipped to the 1 km
  radius without performance regression (≤ 1 s overhead per
  seed; results are cached by the existing Overpass cache)
- the footprints minimap renders the 1 km circle + at least
  one of the three coastline overlays for Cartagena seed_5
  (a known coastal seed)
- the satellite-image background renders at ~0.5 alpha,
  legible underneath the matched-building polygons
- no behavioural change to height aggregation — pure
  rendering/diagnostic addition
- the pairwise IoU summary text appears on the page

## Risks & open questions

- **Sparse OSM coastline data** — inland or
  partially-mapped regions have no coastline data at all.
  The renderer must degrade gracefully: skip the OSM
  overlay, drop the line from the summary, leave the other
  two coastlines.
- **Coordinate projection mismatch** — OSM is lat/lon
  (WGS84), the minimap uses an ad-hoc local metres
  projection. Verify the seed-centred projection used by
  `_render_seed_minimap` and apply the same to OSM
  features before plotting.
- **Satellite tile cache key** — the ESRI tile cache is
  keyed by region bbox, not seed. The minimap is
  seed-centred, so we either re-fetch a tile at the seed
  centre, or crop the region tile to the seed window.
  Cropping is preferable (no network call).
- **Visual clutter** — three coloured coastlines + the
  building polygons + the seed bearing lines is a lot.
  Test on Cartagena and Madrid before adopting; if too
  busy, expose a config flag to suppress the satellite-image
  background (it's the easiest to drop).

## Relationship to other F-SKY items

- **F-SKY11.1** — this **supersedes** the satellite-HSV
  registration target with OSM. The 11.1 satellite path is
  demoted to fallback/diagnostic. The pano-side scoring
  machinery and the heading-sweep API are reused unchanged.
- **F-SKY14 (NEW, not yet proposed)** — trained
  satellite-to-coastline model supervised by OSM
  ground truth. Would replace `detect_sat_water_mask` with
  a CNN once it's needed. Out of scope here; the
  feedback memory `feedback_satellite_coastline_hsv_unreliable`
  records the constraint that any future satellite-side
  detector must be supervised by OSM, not heuristic.
- **F-SKY10** — non-ML cross-view registration; orthogonal
  signal (uses building texture). Both signals can coexist.
- **F-SKY12** — depth-from-pano; orthogonal (height
  verifier). No interaction with this work.

## Phase A.2 — landed (this round)

- ✅ Satellite-image background overlay on minimap, behind
  `SKYLINE_CV_F_SKY13_SAT_BG=1` env flag (default OFF since
  it adds a per-seed network fetch on first run). Reuses
  the existing `fetch_region_satellite` primitive — no new
  fetch path.
- ✅ `osm_keypoints_for_scoring` — thin adapter in
  `osm_water.py` that converts OSM coastline samples into
  the `{bearing_deg, distance_m}` dict shape that the
  existing `score_pano_offset_keypoints` already consumes.
  No modification to the scoring function — it was already
  source-agnostic by design.

## Phase B — landed

- ✅ **Pano-projected coastline overlay** — `pano_water_top_to_lonlat`
  in `coastline_registration.py` inverts the forward pinhole
  projection, returning sea-level (lon, lat) points for the top
  of the water band in each pano column. Rendered as scattered
  orange dots on the minimap (scatter rather than polyline so
  piers / discontinuous near-far transitions don't zig-zag).
  Computed in the existing pano_recovery block (no separate
  fetch path) and threaded through `SeedViewRegistration.pano_projected_coastline`.
- ✅ **Pano↔OSM IoU summary text block** — uses
  `osm_keypoints_for_scoring` to feed OSM keypoints into the
  existing `score_pano_offset_keypoints` at the recovered
  heading offset. Threaded through `SeedViewRegistration.pano_osm_iou`
  + `pano_osm_n_keypoints`; rendered as a blue annotation in
  the top-left of the minimap.
- ✅ Both diagnostics activate only when pano-recovery is
  enabled for the region (existing site config flag). For
  seeds without pano-recovery, the minimap falls back to
  Phase A behaviour (just the OSM coastline + 1 km circle).

## Phase C (future, not this round)

- Make OSM the **only** coastline target after Phase B
  validates that pano↔OSM registration works reliably on
  coastal seeds. At that point `detect_sat_water_mask`
  becomes truly dead code and can be deleted (or kept only
  as the training-data input for F-SKY14).
- Decouple pano-recovery state from the satellite path so
  OSM-driven recovery can run on regions where the satellite
  HSV detector is skipped entirely.
- Extend the 1 km window to a configurable per-region
  parameter for very large or very small seeds.
- See F-SKY14 (separate proposal, filed) for a
  trained satellite coastline detector supervised by OSM.
