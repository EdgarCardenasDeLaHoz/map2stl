# F-DET — Detection Quality & Early-Out

**Status**: in progress  
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
**File**: `pano_registration.py:_seed_multiview_registration`  
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
**Files**: `html_report.py`, `pano_registration.py`

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
- File: `pano_registration.py:_seed_multiview_registration` (snap loop).

**4c. Non-grid street geometry**:
- Signal: consistently low match rate across multiple seeds in a city regardless
  of heading, but high nseg.
- Fix: enable F-SKY7 (local-maxima peaks) more aggressively for non-rectangular
  building layouts. May also need OSM polygon expansion (buildings at non-90° angles
  project to narrower x-ranges).
- Measurement: compare match rate before/after F-SKY7 on affected cities.

---

### F-DET5 — Landing page detection column + sub-label filter
**File**: `scripts/build_landing_page.py`

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
   in A7 to `pano_registration.py` + the pano summary table. Re-run the 4 suspect
   cities and *confirm* each cause.
2. **Validate the quality proxy (A2):** correlate label vs. actual height MAE on
   curated regions. If they don't correlate, re-base the labels on height error.
3. **Fix the F-DET2/4a tension (A4):** make the FOV gate satellite-aware before adding
   more satellite-footprint regions.
4. **Then, and only then,** apply the confirmed 4a/4b/4c fixes — preferring the
   graceful bearing-penalty over the hard snap cut, and adding a held-out region to
   check the thresholds generalise.
