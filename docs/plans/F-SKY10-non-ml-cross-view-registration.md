# F-SKY10 — Non-ML cross-view registration (skyline ↔ satellite)

Proposal entry: `docs/proposals.md` F-SKY10

Status: **implementation in progress (2026-05-17)**. F-SKY8 has
landed (4061 satellite polygons merged into Cartagena's 3015 OSM
records, exit code 0, PDF 20.5 MB) so the "do F-SKY8 first"
precondition in the *Why this captured as plan-only* section below
is satisfied. Phase 1 begins now: satellite imagery fetcher +
lat/lon → pixel projection helper, modeled on F-SKY8's quadkey-tile
cache pattern. Phase 2 (cross-view scorer) and Phase 3 (matcher
wiring) follow.

## Goal

Add a verification / reranking step that scores each candidate
(segment ↔ building) match by **how well the building's appearance in
the satellite image agrees with its appearance in the street view**,
plus geometric consistency between the building's polygon shape and
its observed facade width. No learned models — pure classical CV.

This is what the user asked for as the "3D feature matching
registration step". The mental model: the OSM polygon claims this
building is at lat/lon X with shape Y. Satellite imagery shows what
the building looks like from above. Street view shows what it looks
like from the side. The two views should be consistent; if they're
not, the match is suspect.

## Why non-ML

Classical CV is the right tool here because:

1. **The features that survive a 90° viewpoint change are colour and
   geometry, not learned descriptors.** A SIFT or SuperPoint
   descriptor computed on a top-down roof has nothing in common with
   the same building's side-facade SIFT — descriptors are
   orientation-sensitive in ways that don't survive top-down vs
   side-on. So learned local features don't help here.
2. **The bridging quantity IS geometric.** OSM gives us the polygon
   in lat/lon — we can directly project it into both views without
   any learning step. The verification is "did the projection land
   on something that looks like a building", which is a colour /
   edge / area question, not a descriptor question.
3. **No model dependency to manage** — fits the user's stated
   preference for small models in multi-stage pipelines.

## Three signals to compute per (segment, projection) pair

### Signal 1 — Rooftop colour consistency

- Crop the satellite image to the OSM polygon's pixel bounds.
- Compute the dominant colour (median RGB) of the roof pixels.
- In the street view, the building's roof corresponds to the top
  ~10–20 px of the matched silhouette (the actual roof edge, not
  the facade). Compute the median RGB there.
- Score = `1 - distance(rgb_sat, rgb_sv) / 442`  (442 ≈ max RGB
  distance, gives [0, 1]).
- A glass-curtain-wall tower has a distinct grey-blue roof; a
  terracotta-tile colonial building has a red roof. These match
  across views even though geometry doesn't.

### Signal 2 — Geometric width consistency

- Project the OSM polygon's 3D extent (lat-lon vertices, height
  estimate) into the street view via the existing pinhole camera.
- The projected width at the camera's roof-y elevation is the
  expected facade width in pixels.
- Compare to the actual silhouette segment width.
- Score = `1 - |W_observed - W_expected| / W_expected`.
- Already partially done by `_width_ratio_score` in the matcher;
  F-SKY10 makes it 3D-aware (uses the polygon's shape, not just
  its x-extent).

### Signal 3 — Vertical edge consistency

- Detect vertical building-edge segments in the street view via
  Hough lines on the masked edge image (left/right facade edges).
- Project the OSM polygon's vertical edges (corner verticals) into
  the street view.
- For each projected edge, find the nearest detected vertical Hough
  line within ±5 px.
- Score = fraction of projected edges that found a near Hough line.
- A building whose facade corners line up with the projection is the
  building; a mis-matched OSM polygon whose corners fall on blank
  facade pixels isn't.

## Combining the signals

Add a fourth term to the matcher's `combined`:

```
combined = 0.45*iou + 0.25*w_score + 0.15*(1-occ) + 0.15*cross_view
cross_view = 0.5*colour + 0.3*width + 0.2*edge
```

Weights tuned empirically — keep the existing matcher dominant
(0.85 weight on existing signals), use cross-view as a
reranking nudge (0.15 weight). When all four signals agree the
match is highly confident; when cross-view disagrees with the
others the match goes to the second-place candidate.

## Required infrastructure (most of the cost)

The implementation surface is moderate, but the **infrastructure for
fetching satellite imagery for each region is the big-cost part**:

1. **Satellite tile fetcher** — the strm2stl backend already pulls
   satellite imagery (`app/server/routers/terrain.py` mentions
   satellite endpoints). The skyline_cv module would need to call
   into that or fetch tiles directly.
2. **Pixel-level alignment** — converting OSM lat/lon polygons to
   satellite pixel coordinates needs a proper geographic projection.
   We have the pieces (the `geo2stl/` package has Web Mercator
   helpers) but they need to be wired into the skyline_cv flow.
3. **Per-region cache** — satellite images are static for a given
   region/zoom; fetch once and reuse. Same pattern as the existing
   Street View image cache.
4. **Verifier scoring** — actual signal computation per Stage A/B/C
   above. Numpy + opencv only.

Approximately 60% of the implementation effort is the fetcher /
cache / projection plumbing; 40% is the actual scoring logic.

## Target files (when implementation begins)

- `city2stl/skyline_cv/satellite_image.py` (new) — tile fetcher,
  cache, lat/lon → pixel projection.
- `city2stl/skyline_cv/cross_view.py` (new) — `score_cross_view`
  with the three signals.
- `city2stl/skyline_cv/pipeline.py` —
  `match_segments_to_buildings` accepts an optional callback for
  the cross-view scorer; matcher folds the score into `combined`.
- `city2stl/skyline_cv/region_pdf.py` — wire up the satellite image
  fetch once per region, pass into per-view matcher calls.

## Success criteria (when implementation begins)

- Each per-view diagnostic table row gains a `cv=` field showing the
  cross-view score. Rows with `cv < 0.3` are visually flagged.
- Reranking changes the match for at least some segments where the
  IoU-only matcher picked an obviously-wrong inland building over a
  visible waterfront one (the Cartagena seed_5 case the user has
  been pointing to).
- Tagged-building MAE either improves OR stays within ±1 m of the
  b3 + F-SKY7 baseline (currently 13.73 m). Cross-view is a
  reranking signal; it shouldn't introduce regressions if weights
  are tuned conservatively.
- Run-time increase ≤ 2× (satellite image fetched once per region;
  per-building scoring is cheap).

## Known risks

- **Satellite imagery licensing.** Google Maps Static API for
  satellite imagery is allowed under the standard Google Maps
  terms; **the existing project already uses this path** in the
  main strm2stl app. Mapbox, Bing, and Sentinel-2 are alternatives
  if Google's terms become problematic for derived products.
- **Sentinel-2 resolution (10 m/px) too coarse for building roofs.**
  Need the higher-resolution providers (Google ~30 cm/px, Bing
  similar) for the colour signal to work.
- **Roof colour ambiguity.** Many buildings have the same colour
  (gray concrete dominates dense skylines). The colour signal alone
  is weak; only the COMBINED weight (colour + width + edge) is
  diagnostic.
- **OSM polygon offset error.** OSM polygons can be 2–10 m offset
  from true position. At satellite zoom levels this is 5–20 px.
  Mitigation: dilate the polygon by ~5 px when sampling satellite
  colours; use median to be robust to outliers.

## Out of scope (deferred even further)

- Learned cross-view matching (LoFTR, SuperGlue, GLU-Net). High-
  performance but model-dependency-heavy; user explicitly preferred
  smaller models.
- Multi-view triangulation across seeds for satellite-anchored
  bundle adjustment.
- Time-of-day-aware lighting normalization for the colour signal.

## Why this captured as plan-only

Three reasons:
1. **The next concrete win on Cartagena is F-SKY8** (data
   enrichment) — it directly addresses the central seed_5 gap with
   no new infrastructure.
2. **F-SKY10 needs satellite imagery fetching plumbed in** — that's
   60% of the work and unlocks future ideas (shadow heights,
   illumination-based features) beyond just F-SKY10. Worth a
   focused implementation pass when the infra build is on the
   agenda, not a partial bolt-on now.
3. **Validation depends on stable ground truth.** With Cartagena's
   n=17 tagged-building set still noisy, we can't reliably
   measure whether F-SKY10 helped. Better to do F-SKY8 first (more
   matches → more validation samples) and revisit F-SKY10 when MAE
   measurement is more stable.

When the user is ready to start: read this plan, then read F-SKY8's
plan (the satellite-fetcher infra can be shared), then implement.
