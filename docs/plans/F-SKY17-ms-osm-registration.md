# F-SKY17 — Register MS footprints to OSM before dedup

Status: **implemented + measured (2026-05-28)** — synthetic test passes, but
Cartagena shows the premise does NOT hold there (see Measured results).

## Measured results (Cartagena, 2026-05-28)
- Estimated global offset: **(-0.6, -1.0) m** — negligible.
- Dedup: 3714 of 58296 MS polygons removed → 83219 total (24923 OSM).
- **Conclusion: the OSM/MS misalignment on Cartagena is NOT a uniform global
  translation.** Registration is effectively a no-op here; the visible
  grey/brown twins persist.
- Likely real cause: **shape/size difference** (MS roof outline vs OSM
  footprint) keeps area-IoU < 0.5 even when the polygons are co-located —
  *and* the offset estimator self-selects already-aligned, similar-area pairs
  (the 30 m + area-ratio gate excludes the very twins we care about), biasing
  the estimate toward ~0.
- **Pivot recommendation:** use the **centroid-distance + area-ratio dedup**
  (originally offered option 1) — drop an MS polygon when its centroid is
  within ~8 m of a similar-area OSM polygon, regardless of IoU. That catches
  co-located twins that IoU misses. Keep F-SKY17 registration as a cheap,
  harmless pre-step for regions that DO have a real global offset.

## Why
- OSM and Microsoft ML footprints are **independently georeferenced**.
  - OSM = hand-traced, often building outline.
  - MS = model-extracted from Bing imagery, often roof outline + carries imagery geolocation error.
  - → the same building appears **positionally offset** between the two sources.
- F-SKY8 dedup is **area-IoU ≥ 0.5** (`satellite_footprints.py:277`).
  - Offset twin → IoU drops below 0.5 → **not merged, both kept**.
  - Symptom: offset grey (OSM) + light-brown (MS) duplicates in the minimap.
  - Matcher hazard: can lock onto the offset MS twin → wrong bearing / forward distance → wrong height.
- **Fix premise:** the offset is roughly uniform across the region → estimate it once, correct it, then dedup works.

## Approach
- In `merge_satellite_into_osm`, **before** the dedup loop:
  1. For each MS polygon, find the nearest OSM centroid (reuse the STRtree).
  2. Keep a pair only if it's plausibly the same building:
     - centroid distance ≤ `pair_max_dist_m` (default 30 m)
     - area ratio within [0.5, 2.0]
  3. Offset = **median** (dx, dy) of the surviving pairs (lon/lat degrees) — robust to outliers.
  4. If `len(pairs) < min_pairs` (default 8) → **skip registration** (offset unreliable), proceed as today.
  5. Translate every MS polygon by `-offset` (shapely `affinity.translate`).
- Then run the existing IoU dedup unchanged on the shifted polygons.
- Log the estimated offset in metres + pair count.

## Target files
- `city2stl/skyline/satellite_footprints.py` — `merge_satellite_into_osm` only.

## Success criteria
- Cartagena: offset-twin duplicates collapse (dedup count rises; minimap shows grey/brown aligned).
- Logged offset is a small, stable metre value (sanity: not hundreds of m).
- `< min_pairs` regions degrade gracefully to current behaviour (no crash, no shift).
- No new dependency (shapely already imported).

## Known risks
- **Non-uniform offset** — a single translation only corrects the global component; local residuals remain. Acceptable first cut; escalate to per-cell only if measured residuals stay high.
- **False pairs** inflating the median — mitigated by the distance + area-ratio gate and the median (vs mean).
- **Over-merge** after shifting — genuinely distinct adjacent buildings could now exceed IoU 0.5. Low risk at 0.5; revisit threshold only if observed.
