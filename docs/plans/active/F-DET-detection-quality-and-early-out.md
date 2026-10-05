# F-DET — Detection Quality & Early-Out

**Status**: F-DET1/2/3/5 done; F-DET4a–c on hold pending the instrumentation in "Critical review"; F-DET6 (footprint-first detection for elevated panoramas, seed_5) in progress 2026-10-04 (open items tracked in `city2stl/skyline/README.md`)  
**Date**: 2026-06-13 / implemented 2026-06-20  
**Trigger**: User feedback from per-pano landing page review across 17 regions / 82 panos

---

## Background — what the data shows

Across 82 panos in 17 regions (15 auto-seed, 2 curated):

| nseg range | pano count | quality breakdown |
|---|---|---|
| 0–3 | 1 | 100% weak |
| 4–9 | 5 | 0% good, 80% medium, 20% weak |
| 10–19 | 23 | 13% good, 74% medium, 13% weak |
| 20+ | 53 | 43% good, 40% medium, 17% weak |

**Lowest nseg in any "good" seed**: 7 (busan auto_180_2000m — 7/7 matched, borderline).  
**Median nseg** — good: 32 · medium: 28 · weak: 26.

The critical insight: **weak quality is NOT caused by low nseg at nseg > 10**.
There are two distinct failure modes that look the same in the quality column:

### Type 1 — Bad viewpoint (nseg < 10)
Camera is not facing a skyline. SegFormer finds few or no building silhouettes.
- **Cause**: auto-seed proposed a position behind terrain, inside a dense cluster, or
  facing ocean/park with no buildings in frame.
- **Signal**: `nseg < 10` before registration.
- **Action**: early-out before registration runs (saves the entire anchor + per-view
  registration + pano stitch cost).

### Type 2 — OSM/heading mismatch (nseg = 20–50, match rate < 50%)
Segments are detected but most cannot be matched to OSM building footprints.
- **Examples**: tel_aviv (4 weak seeds, nseg 15–37), melbourne (nseg 28–52),
  honolulu auto (nseg 33, 27% rate), boston auto (nseg 49, 49% rate).
- **Causes** (multiple):
  a. OSM coverage sparse — buildings visible in SegFormer but no OSM polygon exists.
  b. Heading offset large enough to misalign projections by >1 building width.
  c. Buildings at unusual angles (non-rectangular street grid) → segments don't map
     to axis-aligned OSM footprints.
  d. Auto-seed pointing too far from the cluster centroid → visible buildings are not
     in the high-rise set the pipeline knows about.
- **Action**: cannot early-out; must run registration to determine this. Needs
  separate diagnosis and targeted fixes per cause.

---

## Proposed improvements

### F-DET1 — Blob-count pano screen (Type 1 early-out)
**File**: `city2stl/skyline/_pano/orchestrator.py::_seed_multiview_registration`  
**Where**: after the existing coverage screen (`_best_building_coverage`), before
  `_recover_pano_heading` (which triggers the expensive coastline sweep + SegFormer
  stitch).

**Algorithm**:
1. From the SegFormer batch (already prefetched), count distinct connected
   building-mask components across the best 3 views using `cv2.connectedComponents`.
2. Sum the top-3 view blob counts → `total_blobs_estimate`.
3. If `total_blobs_estimate < _MIN_BLOB_COUNT_FOR_REGISTRATION` (proposed: 6),
   mark seed as auto-negative with reason `"low building detection (N blobs)"` and
   `continue` — no anchor, no registration, no pano stitch.

**Threshold calibration**:
- `< 6 total blobs across 3 views` → always bad (confirmed by data: lowest good seed
  has 7 matched, implying ≥ 7 distinct blobs across its views).
- `6–12` → suspect zone: run registration but add a warning badge to the pano row.
- `≥ 13` → normal path.

**Cost**: 3 × `cv2.connectedComponents` calls (~1 ms each) vs. the saved
  coastline sweep + per-view registration (~30–60 s per seed). Net savings large.

**Risk**: false positives (legitimate close-range skyline views might have merged
  segments → fewer blobs). Mitigated by the threshold (6 ≤ legitimate, 2 = never).

---

### F-DET2 — OSM-backed expected-detection gate (Type 1 — proposal time)
**File**: `seed_selection.py:_propose_standoff_locations`  
**Where**: inside the candidate scoring loop, alongside `fg_count > 12` and
  `_nearest_m < 50`.

**Algorithm**: count distinct OSM building centroids visible in the forward 80° FOV
  cone at the proposed standoff. If fewer than `_MIN_OSM_BUILDINGS_IN_FOV` (proposed: 3)
  buildings are in the cone, the viewpoint will almost certainly produce nseg < 10.

```python
def _osm_buildings_in_fov(lat, lon, heading, fov=80.0, max_dist_m=3000.0):
    count = 0
    for (blat, blon) in all_centroids:
        d = _distance_m(lat, lon, blat, blon)
        if d > max_dist_m:
            continue
        delta = abs((_bearing_deg(lat, lon, blat, blon) - heading + 540) % 360 - 180)
        if delta <= fov * 0.5:
            count += 1
    return count
```

This differs from `_foreground_density` (which counts buildings < 200 m for
occlusion) — here we want buildings at ANY distance inside the FOV cone.

**Hard reject**: `osm_in_fov < 3` → skip candidate entirely.  
**Score bonus**: `osm_in_fov / 10.0` added to candidate score to prefer
  positions facing denser skylines.

---

### F-DET3 — nseg prerequisite in quality classification
**File**: `html_report.py:write_region_report` (pano summary table)  
**Where**: quality label assignment, currently:
```python
if mrate >= 65 and ncov >= 15 and nseg >= 10:   # our recent nseg gate
    qlabel = "good"
elif mrate >= 50 and ncov >= 5 and nseg >= 4:
    qlabel = "medium"
else:
    qlabel = "weak"
```

**Improvement**: add explicit "Type 1" and "Type 2" weak sub-labels so the landing
  page table can show WHY a seed is weak rather than just "weak":

| Condition | Label | Colour |
|---|---|---|
| `nseg < 10` | weak — no detection | red |
| `nseg >= 10` and `mrate < 40%` | weak — mismatch | orange-red |
| `nseg >= 10` and `mrate 40–50%` and `ncov < 5` | weak — low coverage | amber |
| existing medium/good path | medium / good | existing |

This also surfaces better in the landing page table (currently all weak looks the same).

---

### F-DET4 — Type 2 root cause investigation (OSM gap vs. heading)
**Files**: `city2stl/skyline/html_report.py`, `city2stl/skyline/_pano/orchestrator.py`

Type 2 seeds (high nseg, low match rate) need per-case root-cause diagnosis. Three
sub-causes require different fixes:

**4a. OSM footprint gaps** (tel_aviv, possibly honolulu):
- Signal: detected segments in areas with no OSM polygons.
- Fix: enable `use_satellite_footprints=true` in the site JSON for these regions
  to supplement OSM with Microsoft ML footprints.
- Validation: re-run and check whether match rate improves.

**4b. Heading offset too large** (auto-seeds that snap to panos far from proposed position):
- Signal: large `bearing_shift_deg` in the pano summary + low match rate.
- Fix: tighten the auto-seed snap tolerance from 200 m to 100 m OR add a
  bearing-deviation penalty when the resolved pano is > 100 m from the proposed position.
- File: `city2stl/skyline/_pano/orchestrator.py::_seed_multiview_registration` (snap loop).

**4c. Non-grid street geometry**:
- Signal: consistently low match rate across multiple seeds in a city regardless
  of heading, but high nseg.
- Fix: enable F-SKY7 (local-maxima peaks) more aggressively for non-rectangular
  building layouts. May also need OSM polygon expansion (buildings at non-90° angles
  project to narrower x-ranges).
- Measurement: compare match rate before/after F-SKY7 on affected cities.

---

### F-DET5 — Landing page detection column + sub-label filter
**File**: `city2stl/skyline/scripts/build_landing_page.py`

**Changes**:
- Add `nseg` column to the pano table (currently shows Det / Mat / Rate / Cov — keep
  all, they're all useful).
- Surface the F-DET3 sub-labels (no-detection / mismatch / low-coverage / medium / good)
  as distinct filter options in the quality dropdown.
- Colour the `Det` cell red when nseg < 10, amber when nseg 10–19.

---

## Implementation order

| Step | Feature | Risk | Effort | Status | Expected impact |
|---|---|---|---|---|---|
| 1 | F-DET1 blob-count screen | Low | Small | ✅ Done | Eliminates useless Type 1 registrations (< 10 panos but saves 30–60 s each) |
| 2 | F-DET3 weak sub-labels | Low | Small | ✅ Done | Makes report actionable — shows WHY each seed is weak |
| 3 | F-DET2 OSM-backed gate | Low | Small | ✅ Done | Prevents Type 1 seeds from even being proposed |
| 4 | F-DET5 landing page columns | Low | Small | ✅ Done | Det cell colored red/amber by nseg; sub-label filter in dropdown |
| 5 | F-DET4a satellite footprints | Medium | Small | Pending | Fixes tel_aviv / honolulu Type 2 |
| 6 | F-DET4b snap tolerance | Medium | Small | Pending | Fixes auto-seeds that resolve to wrong panos |
| 7 | F-DET4c non-grid geometry | High | Medium | Pending | Needs per-city validation |

---

## Calibration data

From 82 panos across 17 regions:

```
nseg threshold for "definitely bad":    < 6   (no good seeds below 7)
nseg threshold for "suspect":           6–12  (few good seeds, mixed medium)
osm_in_fov minimum to propose seed:     3
blob count for early-out:               < 6 total across 3 best views
snap distance tighten:                  200 m → 100 m (reduces heading drift)
```

Type 2 affected cities (high nseg, low match rate):
- tel_aviv: 4/5 seeds weak — likely OSM gap + heading drift
- melbourne: 3 seeds weak — likely non-grid geometry (Melbourne CBD diagonal grid)
- honolulu: 1 seed weak — likely OSM gap for waterfront
- boston: 1 seed weak — likely snap distance (auto seed at 2000 m standoff)

---

## Success criteria

After implementing F-DET1–4:
- Zero seeds with nseg < 6 reaching full registration (early-out fires first)
- tel_aviv, melbourne, honolulu Type 2 weak seeds either converted to medium/good
  or correctly diagnosed and documented
- Landing page `weak — no detection` / `weak — mismatch` distinction enables
  rapid triage without opening individual seed pages

---

## Critical review — challenged assumptions (2026-06-23)

Before implementing the pending F-DET4a/b/c work, the assumptions underpinning the
whole plan are worth challenging. F-DET1/2/3/5 are reasonable engineering (early-out
+ better labels) but several premises are weaker than the plan implies.

**A1. The dataset is tiny and validation is circular.** Every threshold
(`nseg<6`, `blobs<6`, `osm_in_fov<3`, `mrate≥65/ncov≥15/nseg≥10` for "good") is
hand-tuned on the *same* 82 panos / 17 regions used to derive them. There is **no
held-out set**, so these are fit-on-train numbers — expect them to be optimistic and
to drift on new cities. The `nseg<6` cutoff in particular rests on a **single** data
point (busan, "7/7 matched, borderline"). *Mitigation to plan: hold out 3–4 regions
and re-measure the good/medium/weak split before trusting the thresholds.*

**A2. The quality label is a proxy for a proxy — never validated against height
accuracy.** "good/medium/weak" is computed from match-rate + coverage + nseg, but the
project's real objective is **building-height error** (retna_pruned ≈ 3.82 m MAE; and
[[project_terrain_segmentation_finding]] warns to use footprint_iou, not dice). A pano
can score "good" on nseg/mrate yet yield poor heights, or be "weak" while its few
matched buildings are dead-on. **F-DET optimises detection quality, not the metric we
care about.** *Highest-value next step: on the 2 curated regions (and any with known
heights), correlate the quality label against actual per-building MAE — confirm "good"
really means accurate before tuning more thresholds.*

**A3. The Type 1 / Type 2 dichotomy is presented as discrete but the data is a
continuum.** At nseg ≥ 20 the split is still 43% good / 40% medium / 17% weak — high
nseg does not imply good. Lumping all non-low-nseg failures into "Type 2 mismatch"
hides other factors the plan never controls for: SegFormer variant (b0 vs b3), input
resolution, lighting/time-of-day of the Street View capture, and sky/glass
mis-segmentation. Some "Type 2" may actually be segmentation-quality problems, not
OSM/heading problems.

**A4. F-DET2 (OSM-in-FOV gate) is in direct tension with F-DET4a (OSM is
incomplete).** F-DET2 *rejects* proposals with < 3 OSM buildings in the cone, while
F-DET4a's premise is that OSM coverage is **sparse** in exactly the regions we want
(tel_aviv, honolulu) and must be supplemented with satellite footprints. So the
proposal gate can reject good viewpoints precisely where OSM is the unreliable signal.
*Resolution: F-DET2 should count OSM **plus** satellite footprints (or be disabled for
regions flagged `use_satellite_footprints`), otherwise it bakes OSM bias into seed
selection.*

**A5. Blob count (F-DET1) conflates buildings with noise.** `cv2.connectedComponents`
on the SegFormer building mask counts *any* component — including glass towers split
into fragments, or vegetation/cloud mislabels. A forest-facing view can clear the
blob≥6 gate; a clean close-range skyline can merge into < 6 blobs (the plan notes this
risk). Blob count is a coarse proxy for "buildings present"; pairing it with the
F-DET2 OSM-in-FOV signal (geometry-based, view-independent) would be more robust than
either alone.

**A6. Early-out trades recall for speed — is the trade even needed?** F-DET1 assumes
Type 1 seeds produce zero useful heights, but a 5-blob view may still height 2–3
buildings correctly. The justification is the ~30–60 s/seed cost — but these are
**offline batch** runs, so wall-clock cost may not be the binding constraint. Worth
confirming the early-out doesn't silently drop buildings that aggregation would have
used (measure: buildings-with-≥1-estimate before vs after F-DET1 on the suspect-zone
seeds).

**A7. The Type 2 "causes" are hypotheses, not diagnoses.** The plan itself hedges
every one — "**likely** OSM gap" (tel_aviv), "**likely** non-grid geometry"
(melbourne), "**likely** snap distance" (boston). Implementing fixes against unconfirmed
causes risks fixing the wrong thing per city, and the fixes are **city-specific hacks**
(toggle a JSON flag, retune a tolerance) that don't generalise and won't scale past 17
regions. *Next step should be **instrumentation, not fixes**: for each Type 2 seed log
`bearing_shift_deg`, resolved-pano-distance-from-proposed, and a SegFormer-segment ↔
OSM-footprint overlap map. That converts "likely" into measured, and tells you whether
4a/4b/4c are even the right levers.*

**A8. F-DET4b (snap 200 m → 100 m) trades drift for coverage.** Street View panos are
sparse; the nearest pano to a proposed standoff may legitimately be 120–180 m away.
Tightening the snap will reject those, losing seeds in low-coverage cities. A
**bearing-deviation penalty** (already floated as the alternative) is safer than a hard
distance cut because it degrades gracefully.

### Recommended next steps (revised order)
1. **Instrument before fixing (replaces blind F-DET4):** add the per-seed diagnostics
   in A7 to `city2stl/skyline/_pano/orchestrator.py` + the pano summary table. Re-run the 4 suspect
   cities and *confirm* each cause.
2. **Validate the quality proxy (A2):** correlate label vs. actual height MAE on
   curated regions. If they don't correlate, re-base the labels on height error.
3. **Fix the F-DET2/4a tension (A4):** make the FOV gate satellite-aware before adding
   more satellite-footprint regions.
4. **Then, and only then,** apply the confirmed 4a/4b/4c fixes — preferring the
   graceful bearing-penalty over the hard snap cut, and adding a held-out region to
   check the thresholds generalise.

---

## F-DET6 — Footprint-first detection for elevated panoramas (Cartagena seed_5), 2026-10-04

User: "Make a plan for how to improve building detection and matching for seed 5 and implement
it"; "we can detect parks and streets from the drone angle and use those to position locations
as well, those will be available in osm".

### What is wrong today (seed_5, a drone Photo Sphere over the Castillogrande bay)

Read from the code (pano path: `_pano/detect.py::_build_and_detect_pano`) and the report
overlays:

- **Missed towers.** The pano path splits only the building mask (`detect_buildings_from_mask`,
  peaks of building pixels per column); a continuous wall of towers gives few peaks, and white
  towers SegFormer labels outside {building, house, skyscraper} are absent from the mask.
- **Front building wins.** `match_segments_to_buildings` takes every candidate within 0.10 of
  the best score and the **nearest wins**; no depth, no roof angle. In shared columns the near
  short building takes the segment.
- **Bottoms in the water.** The hole fill promotes everything under a continuous building band
  to building; the ground cap trims only to the waterline; the bbox base cap is not applied in
  the pano path.
- **Camera height.** 1.7 m is assumed everywhere; the drone is roughly 100 m+ up, so roofs sit
  below the horizon and bases far below it. Sky-based steps (screening, contour) fail.
- **F-DET1 early-out** counts mask fragments, not buildings: a continuous city is one blob per
  view, so the known-good drone seed_4 scored 3 < 6 and was dropped.
- Side findings: `_smooth_pano_matches_against_views` swaps matches with no dedup;
  `_pano/runs/seed_resolution_cache.json` is where the code really caches (README says `runs/`).

### Approach (new module `city2stl/skyline/footprint_detect.py`, demo script per seed)

A. **Offline harness.** Rebuild seed_5's stitched pano from the cached spin views (Photo Sphere
   capture, as the pipeline does): RGB, full SegFormer label map, per-column frame heading,
   pitch (URL tilt 82.71 -> +7.3 deg). No production code changes.
B. **Camera pose from the ground (the user's idea).** From a drone the near waterline sits below
   the horizon by atan(h / d_shore); OSM coastline/water gives d_shore for every bearing.
   Per column: observed elevation of the top of the near water run vs predicted
   -atan(h / d_shore(bearing)). Fit heading offset, camera height h and a pitch correction
   (the open-sea horizon pins pitch). Parks/green (OSM leisure/landuse) and beaches next.
C. **Footprint-first detection.** Every OSM footprint in view (tagged or not): columns from its
   vertex bearings at the fitted pose; base row from atan(h / d_near). Measured nearest first;
   the per-column top of nearer measured buildings is the occluder line for farther ones. A
   building's top = where its own building pixels end going up from its visible bottom: against
   sky, water, or a farther building (depth discontinuity in Depth Anything V2). Height =
   h + d_near * tan(elevation of the top row) — negative angles allowed (roof below horizon).
D. **Compare and check without truth.** Old vs new boxes on seed_5 side by side; predicted vs
   observed base rows (validates h and heading); OSM towers in view measured vs old matches;
   agreement with seed_1 on shared buildings once seed_1 runs the same way; anchors: Hotel
   Estelar 202 m (Wikidata), GBA heights as a weak reference. Cartagena has no survey truth and
   Google 3D Tiles has no buildings there (2026-10-04 probe), so these are the checks.

### Success criteria (seed_5)

- Pose: heading within 2 deg of the manual anchor (320 deg, report correction -16 deg) and a
  camera height consistent across waterline columns (robust spread < 20 %).
- Base rows: median |predicted - observed| < 10 px where the base is visible.
- Detection: >= 80 % of OSM footprints with a visible extent >= 20 px measured (the old path
  matched 35 segments).
- Consistency: seed_1 and seed_5 agree within 25 % on shared buildings (today: seed_1 reads
  36 m higher than seed_5 on 78 % of 9).

### Risks

- Photo Sphere frame vs geographic heading: re-estimated by the waterline fit, not assumed.
- Shoreline in OSM vs the real waterline (seawalls, beaches): a few metres; at 500 m and 100 m
  height that is ~1 px.
- Depth Anything gives relative depth; only discontinuities are used, not values.
- Buildings built after OSM was drawn (or demolished) show as unexplained columns.

### Progress, 2026-10-04

Run: `python -m city2stl.skyline.scripts.18_footprint_detect --region cartagena --seeds seed_5
seed_1 --out <dir>`. It needs the Street View API on a capture-cache miss, a GPU for speed
(SegFormer, Depth Anything V2), and Overpass for the OSM coastline.

- **Pose (B), done.**
  - seed_5: offset 309°, camera 98 m, pitch fix +0.23°, misfit 0.41°.
  - seed_1: offset 135° (the manual anchor), 43 m, misfit 1.13°.
  - Shore table vectorised: 2 s, was 5 min.
- **Detection (C).**
  - seed_5 measures 226 OSM footprints; the old path matched 35 segments. 132 have a visible base.
  - Predicted against observed base row: median 6 px.
- **Measurement v2.** Diagnosed on column profiles of RGB, depth and labels:
  - Depth Anything follows `a / d + b` closely. Implied distance is within 10–25%, saturating
    beyond about 1.5 km in seed_1.
  - The old fixed 0.7 stop only split surfaces 1.4× apart. Bocagrande's rows are 10–30% apart.
  - Nearer surfaces were never rejected: a 620 m building was read as a footprint 887 m away.
  - Fixes:
    - Skip surfaces more than 35% nearer.
    - Stop at the midpoint to the next OSM footprint behind, clipped to 0.70–0.92× of the
      footprint's own level.
    - Accept a base only if its depth agrees with the footprint's distance.
    - Robust depth fit.
    - Drop base-hidden slivers under 25% visible.
  - Results:

    | Check | Before | After |
    |---|---|---|
    | seed_1 / seed_5 median gap | 58.7 m | 30.6 m (12 shared) |
    | Share within 25% | 20% | 33% |
    | seed_1 vs OSM tags (14 buildings) | 38 m | 16 m |

  - Hotel Estelar: 185 m from seed_1, against 202 m (Wikidata).
- **Still wrong, with causes seen in the crops:**
  1. **Bearing offset near the camera.** Nautica (tagged 161 m) reads 40 m at 271 m in seed_5.
     Its columns land on the lower building in front of the glass tower to their right: about
     5°, or 24 m at that range. The cause is the seed position or the footprint geometry. The
     parks-and-streets position fit below is aimed at this.
  2. **Nested footprints.** The Plaza Bocagrande mall (tagged 44.8 m) reads 180 m from seed_1:
     the tower on its podium sits at the same depth. Measure contained or overlapping parts first
     and take their columns out of the container.
  3. **OSM tags are a weak yardstick here.** Allure (tagged 190 m) reads 62 m from seed_5 and
     32 m from seed_1; both views show a mid-rise and a crane on that spot, so the tag may be a
     planned height.
  4. **Far buildings (beyond about 1.5 km).** Depth saturates, and 1 px is about 3.5 m. Pairs at
     1.6–2.5 km disagree (201 m against 11 m).
- **Next:**
  - Fit the camera position from parks and streets. SegFormer road, sidewalk and path (6, 11, 52)
    and grass, tree and field (9, 4, 29) below the horizon, against OSM roads and green projected
    from (lat, lon, h). Search ±100 m around the seed, with heading, height and pitch from the
    waterline.
  - Handle nested footprints.
  - Fuse the seeds into one height per footprint, weighted by visible base, visible fraction and
    1/distance.
  - Then replace the pano path for elevated seeds in the region report, and fix the F-DET1
    fragment count.
