# Plan — per-city scale and window sizing

_Last updated: 2026-09-28_

> **Status: done (2026-08/09).** Hypothesis (b) won — constant coverage, per-plate scale.
> - Scale = plate XY extent against a nominal ground frame:
>   `tools/align_tool/export_align_data.py::plate_scale_m_per_unit`, using `PLATE_COVERAGE_M`
>   (2 km) or a per-city `PLATE_COVERAGE_OVERRIDE_M` entry.
> - A hand alignment's measured span overrides the nominal coverage:
>   `tools/align_tool/locate.py::measured_span_m` (Salzburg measured 1651 m);
>   `tools/align_tool/locate.py::plate_span_m` converts extent to metres.
> - The `tallest_m / stl_z_max` estimator is bypassed: the align tool passes scale in.
> - The "fourth tuple slot in `CITIES`" below was superseded by the extent-derived scale.
> - Ground truth: 6 hand placements in `map2stl/tools/align_tool/ground_truth/` (`barcelona_spain.json`
>   redone by hand).
> - Leftover: `tools/align_tool/_fix_sat_flip.py` still exists (the plan said delete it) — tracked
>   in the roadmap ([plans/README.md](../README.md)).
> - Why: [registration-plate-location](../../decisions/registration-plate-location.md). Follow-on
>   work: [registration-learning-plan.md](../active/registration-learning-plan.md).

The text below is the plan as written, kept for its reasoning.

Scope: why the exported alignment regions are the wrong ground size, and what to change so the
overlay scale is known rather than assumed. This page covers only the scale/extent problem; the
learned-registration work is in [registration-learning-plan.md](../active/registration-learning-plan.md)
and must not start until this is settled.

Original status: not started. Blocking Layer 1 of the learning plan ("chase this before training anything").

## Goal

Every exported city has a *known* metres-per-STL-unit figure, so the OSM/satellite rasters cover
the same ground as the STL and the alignment tool starts near the answer instead of at an
arbitrary zoom. Success is measured, not eyeballed: the tool's own scale readout, with scale
unlocked, must agree with the value the exporter assumed.

## The two competing hypotheses

The exporter currently derives ground extent from a single global assumption. Two readings of the
vendor data are consistent with what has been seen so far, and they predict different things:

* **(a) Constant scale, varying coverage.** Every city was printed at roughly 19.7 m per STL unit,
  and the plates therefore cover different amounts of ground depending on how large the plate is.
  Under this reading the exporter's fixed scale is right and the *bbox* per city is wrong.
* **(b) Constant coverage, varying scale.** Every plate covers the vendor's 2 km LARGE-size frame,
  and the scale therefore varies per city over roughly 17.8–25.1 m per unit. Under this reading
  the bbox is right and the exporter's fixed scale is wrong.

**Distinguishing test.** Align a city in `map2stl/tools/align_tool` with the scale checkbox *unlocked*. The
on-screen scale then measures the true metres-per-unit for that city, independent of any exporter
assumption. Do this for three or four cities of different plate sizes. If the measured values
cluster near 19.7, hypothesis (a) holds; if they spread across 17.8–25.1 while ground coverage
stays near 2 km, hypothesis (b) holds.

Do not apply a fix before running this test. Both fixes are cheap; picking the wrong one is not.

## Approach

* Run the distinguishing test on Salzburg first (smallest plate, largest predicted deviation),
  then on two or three others. Record the measured metres-per-unit per city.
* Add a per-city `scale_m_per_unit` as a fourth tuple slot in `CITIES`, unpack it at the export
  entry, and thread it into `register_city_stl(...)`. Under hypothesis (a) the values are all the
  same and the field documents that; under (b) they differ and the field is the fix.
* Replace or bypass the `tallest_m / stl_z_max` scale estimator. It is invalid regardless of which
  hypothesis wins: `stl_z_max` is base plate plus terrain plus buildings, so dividing the tallest
  building height by it does not yield a vertical scale, let alone a horizontal one.
* Re-export all eight cities with the corrected scale. **Do not run `_fix_sat_flip.py` after a full
  re-export** — the exporter now writes satellite rasters row-0-south itself, and the script would
  re-introduce the flip it was written to remove.
* Re-align every city in the tool with scale *locked* to the new per-city value, and re-record
  ground truth. The existing `map2stl/tools/align_tool/ground_truth/barcelona_spain.json` is the unmodified pipeline guess,
  not a human placement, and must be redone.
* Re-check the water masks after re-export. Five of eight are currently degenerate; if the extent
  was wrong, some of that may resolve on its own, and what remains is a separate mask bug.

## Target files

As planned (line numbers dropped; the `CITIES` slot was never added):
* `tools/align_tool/export_align_data.py` — `CITIES` and the `register_city_stl(...)` call; landed
  as `plate_scale_m_per_unit` instead.
* `numpy2stl/src/numpy2stl/registration/pipeline.py::register_city_stl` — the `tallest_m / stl_z_max`
  estimator (bypassed, still present).
* `city2stl/osm_raster.py::estimate_bbox_from_stl` — the consumer of that estimate.

## Success criteria

* For each of the eight cities, the tool's measured scale with the checkbox unlocked is within a
  few percent of the value the exporter passed in.
* Ground coverage per city is consistent with the vendor's stated frame size for that plate.
* Ground truth exists for all eight cities and none of it is a pipeline guess.
* The `tallest_m / stl_z_max` path is gone or has a documented replacement — not left in place with
  a comment saying it is wrong.

## Risks

* **Re-export is expensive** (minutes per city plus a network fetch). Settle the hypothesis on one
  city before committing to all eight.
* **The flip script is a trap.** It is destructive-by-repetition and its docstring says so. It
  should be deleted once the re-export lands rather than kept as a tempting one-liner.
* **A wrong scale is invisible at a glance.** The overlay can look plausible while being 20% off,
  which is exactly how the current state arose. Trust the numeric readout, not the picture.
* **Vendor framing is inferred**, not documented in the data. The 2 km LARGE-size anchor comes from
  the Micropolitan framing PDF and applies to that product line; a city from another line may not
  follow it.
