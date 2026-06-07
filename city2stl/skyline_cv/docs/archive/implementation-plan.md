# Implementation Plan — Skyline CV Quality Improvements

Based on audit `cartagena-audit-2026-05.md`.

---

## Issue 1 — Aerial/Drone Image Detection and Exclusion

**Problem:** Google Street View endpoints sometimes serve user-contributed aerial/drone photos.
These have a downward camera pitch (−15° to −25°), which the height model cannot handle.

**Detection strategy — URL pitch only (revised):**

The initial plan proposed using `horizon_fraction = median(contour_y) / image_height` to
detect aerials visually. This was implemented with a threshold of `> 0.65` but caused a
**critical regression** (extracted buildings dropped from 2,985 → 54).

**Root cause:** In Cartagena's coastal scenes, the HSV/blue-dominance sky detector labels
calm ocean water as "sky" because sea surface is bright and blue-tinted. Since water is
spatially connected to the actual sky at the horizon, the top-connected sky mask floods
down through the ocean region, pushing `contour_y` deep into the frame and making
`horizon_frac` artificially large (0.70–0.90) even for perfectly valid ground-level
ocean-facing headings.

**Revised approach:** Use URL pitch alone:

```python
is_aerial = seed.pitch < -8.0
```

- Seeds with significant downward tilt (pitch < −8°) are reliably aerial
- The blue-sky/ocean ambiguity makes visual contour position unreliable for coastal scenes
- The two remaining seeds (seed_1: pitch ≈ −5.8°, seed_2: pitch ≈ −0.6°) do not trigger this

**Lesson learned:** `horizon_frac` is not a valid aerial discriminator for waterfront/coastal
locations. For future multi-city use, aerial detection would need a separate color-agnostic
signal (e.g., counting horizontal building edges in the lower frame half, or checking
Google's `pano_id` metadata for known drone contributors).

**Implementation locations:**
1. `region_pdf.py` → `_screen_score_from_image`: Add horizon_fraction check; return a
   third element `is_aerial: bool` (or fold into the `label` string: `"aerial"`).
2. `region_pdf.py` → `_seed_multiview_registration`: Before calling
   `estimate_heights_from_registration` for a captured view, check aerial flag; if aerial,
   skip height extraction and annotate the PDF overlay with "aerial — skipped".
3. `region_pdf.py` → `_render_pdf` height table: Mark aerial-skipped views clearly.

**No change to `pipeline.py`** — detection lives in the caller since it only affects the
reporting/screening layer, not the core CV algorithms.

---

## Issue 2 — Remove Seed_3 (sunset marina aerial)

**Problem:** `seed_urls[2]` in `cartagena.json` is a sunset drone photo. Orange sky defeats
the blue-sky detector entirely. Should be replaced with a ground-level north-approach view.

**Action:** Remove the third entry from `seed_urls` in `sites/cartagena.json`.

**Replacement guidance (not automated — requires human Street View browse):**
A good candidate: lat ≈ 10.42, lon ≈ −75.55, heading ≈ 180° (north causeway looking south
toward Bocagrande towers). Alternatively lat ≈ 10.40, lon ≈ −75.57, heading ≈ 90° (western
approach from Manga/Pie de la Popa looking east).

---

## Issue 3 — Auto-Location Screening False Positives

**Problem:** `_screen_score_from_image` scores tree canopy and low-rooftop street scenes as
"good" because std+span rewards any irregular boundary.

**Two additional filters in `_screen_score_from_image`:**

### Filter A — Sky fraction in top strip
Sky pixels should dominate the top 20 % of the frame for a true skyline view:

```python
top_strip = sky_mask[:int(h * 0.20), :]
sky_frac_top = top_strip.mean() / 255.0  # fraction of top strip that is sky
```

Require `sky_frac_top >= 0.35`. Below this threshold the image has no clear sky overhead
(rooftop close-up, wall, tree canopy pressed against top), which means the contour is
tracing something that is not a building-sky boundary.

### Filter B — Contour vertical range
Building silhouettes produce sharp peaks. Flat terrain or trees produce a nearly flat
contour. Require the peak-to-trough range of the contour to span at least 6 % of frame
height:

```python
contour_range_frac = (np.nanmax(contour) - np.nanmin(contour)) / h
```

Require `contour_range_frac >= 0.06`.

**Score formula change:** Apply both filters as hard gates before computing the float score.
If either gate fails, return `(0.0, "rejected")`. The existing std+span formula continues
for images that pass the gates.

---

## Issue 4 — Pitch-Aware Height Model

**Problem:** The current height estimator in `pipeline.py →
estimate_heights_from_registration` assumes camera pitch = 0 (horizontal). Even legitimate
ground-level seeds with a few degrees of tilt (e.g. seed_2 at `pitch ≈ −0.6°` from
metadata but potentially captured with camera angled downward) will accumulate scale error.

**Model correction using detected horizon row:**

The detected `contour_y` median gives us the apparent horizon row `y_h` in the image. For a
level camera, the vanishing point is at the vertical image center (`h/2`). Any deviation
tells us the effective pitch:

```
eff_pitch_rad = arctan((h/2 - y_h) / (h / (2 * tan(vfov/2))))
```

where `vfov = 2 * arctan(tan(hfov/2) * h/w)` (vertical FOV from horizontal FOV + aspect).

This `eff_pitch_rad` can then be used to scale the angular height estimate. For now the
implementation will:

1. Compute `eff_pitch_rad` in `estimate_heights_from_registration` if a `contour_y` array
   is passed in.
2. Apply it as a multiplicative correction to the pixel-angle calculation for each building.
3. Flag the view with a `pitch_correction_deg` field in `RegisteredBuildingEstimate` for
   diagnostic display in the PDF.

**This is a partial fix** — it corrects horizontal-camera-but-slightly-tilted views. True
aerial (pitch < −10°) views should still be excluded by Issue 1, not corrected here.

---

## Implementation Order

| Step | File | Change |
|------|------|--------|
| 1 | `sites/cartagena.json` | Remove `seed_urls[2]` (sunset aerial) |
| 2 | `region_pdf.py` | Add `is_aerial` detection to `_screen_score_from_image` |
| 3 | `region_pdf.py` | Gate aerial views in `_seed_multiview_registration` |
| 4 | `region_pdf.py` | Add sky-fraction + contour-range gates to `_screen_score_from_image` |
| 5 | `pipeline.py` | Add `eff_pitch_rad` computation and correction in `estimate_heights_from_registration` |
| 6 | `region_pdf.py` | Expose `pitch_correction_deg` in PDF overlay and height table |

---

## Deferred (Future Work)

- **Scale calibration regression:** Once 3+ ground-level seeds exist, fit a linear model
  over OSM-tagged buildings to correct systematic bias. Expected to reduce MAE from ~13 %
  to ~5 %.
- **Additional seeds:** West approach (lat≈10.40, lon≈−75.57, hdg≈90°) and southeast
  approach (lat≈10.38, lon≈−75.54, hdg≈330°) — require human Street View browse to find
  coverage, then add to `cartagena.json`.
- **Canny edge enhancement:** Use Canny edges as a secondary signal to sharpen the contour
  at building edges where HSV segmentation bleeds into facade gradients.
