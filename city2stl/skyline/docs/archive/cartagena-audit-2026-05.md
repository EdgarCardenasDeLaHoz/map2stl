# Cartagena Skyline CV — Audit Report (May 2026)

Run statistics at audit time:
- 21 pages / 3 seeds × 7 headings = 21 registration views
- Buildings fetched: 3,017  |  High-rises: 28  |  Building records: 3,015
- Locations screened: 9  |  coverage=good: 8, medium: 0, weak: 1
- Seed-extracted buildings: 2,985  |  Cross-seed validated: 2 (tiny overlap)

---

## What Is Working

**End-to-end run is clean.** 21-page PDF produced without errors. OSM Overpass fetch,
Street View capture, heading sweep search, registration, height extraction, and artifact
layout all function reliably.

**Seed_1 (bay approach, lat≈10.407, heading≈321°) is genuinely good.**
The skyline contour correctly traces the Bocagrande twin towers and glass high-rises.
Best heading offset=0°, best_score=1.41. The contour locks onto building tops, which
is the core proof-of-concept.

**Height estimates for ground-level views are ballpark-correct.**
Building 2248: tagged 47.6 m → predicted 41.2 m (≈13 % error). The angular model
is fundamentally sound for horizontal street-level imagery.

**Infrastructure is solid:** .env API-key loading, `seed_urls` persistence in city JSON,
heading sweep, PDF layout, OSM footprint overlay on map page — all functioning.

---

## What Is Not Working

### 1. Height estimates catastrophically wrong (MAE ≈ 151 m, RMSE ≈ 234 m)

**Root cause:** Seeds 2 and 3 are aerial/drone photos (contributors: Pedro Pablo Barbosa D.,
Kelvin Quintero Peña). Camera pitch is ≈ −15° to −25° (looking down). The height model
assumes pitch=0 (horizontal). Pitch compression makes apparent building elevations look
much larger than they are. Example: building 1969 tagged 57.8 m → predicted 461 m (8×).

These two seeds dominate the height statistics and destroy the validation score.

### 2. Seed_3 (marina sunset aerial) contour is at the wrong boundary

Orange sunset sky confuses the blue-sky detector. The contour ends up tracing the upper
sky/cloud boundary while the actual city sits in the lower-left of the frame. Registration
score of 0.81 is misleadingly "good" — it is matching empty sky to OSM projections.

### 3. Auto-location screening has false positives

Several auto-selected locations scored "good" (0.91–1.00) while showing residential streets,
tree canopy, and low rooftops — not skyline. The `std+span` heuristic rewards any irregular
sky boundary regardless of what causes it.

### 4. Bay-horizon contamination in Seed_2

At headings facing the bay, the left/right half of the frame is ocean horizon, not buildings.
Contour runs along the water line instead of building tops in many of the 7 heading sweep
positions.

### 5. Cross-seed agreement is near-zero

Only 2 buildings received estimates from more than one seed. Seeds 1 and 2 view the city
from nearly opposite directions, so angular coverage barely overlaps. With 1 good seed and
2 broken seeds, cross-seed validation is not meaningful.

---

## Next Steps

See `implementation-plan.md` for the full implementation details.

Priority order:

1. **Remove seed_3; detect and skip aerial images** — highest ROI, immediately fixes height stats.
2. **Improve auto-location screening** — sky-fraction + contour-range filters eliminate false positives.
3. **Add more ground-level seeds** — west and southeast approaches to enable real cross-seed validation.
4. **Pitch-aware height model** — use detected horizon row to correct for camera tilt.
5. **(Later) Scale calibration** — linear regression over OSM-tagged buildings to correct systematic bias.
