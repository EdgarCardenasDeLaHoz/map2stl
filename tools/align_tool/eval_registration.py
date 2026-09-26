"""Score the registration pipeline against the Layer 0 hand-aligned ground truth.

This is the measuring stick for the learned-registration plan
(`docs/registration-learning-plan.md`): nothing advances a layer without a number
from here.  It reports three independent things, deliberately kept apart so a mask
win and a registration win can be told apart:

1. **Geometric error** — how far the predicted transform is from ground truth, in
   metres of corner displacement and degrees of rotation.  This is the headline.
2. **Registration quality** — `score_alignment()` (the pipeline's own metrics,
   aggregated rather than recomputed) evaluated at BOTH the predicted and the
   ground-truth transform.  The ground-truth row is the ceiling: it shows how good
   the metrics can get on this city given the masks we currently produce, so a low
   predicted score next to a low ground-truth score means the masks are the problem,
   not the search.
3. **Segmentation quality** — IoU of the STL building mask against the OSM building
   mask warped into STL pixel space by the ground-truth transform.  This is the
   number Layer 1 (the segmentation network) has to move, and it is measured with
   the search taken entirely out of the picture.

Inputs come from the align tool's export: `data/<slug>/` supplies the
rasters and `meta.json`, and `_ground_truth/<slug>.json` supplies the hand placement.
Cities without a ground-truth file are skipped, so this runs usefully before all
eight have been aligned.

The baseline prediction is the transform recorded in the export
(`pipeline_guess.matrix`), so a baseline run needs no re-registration and is exactly
reproducible.  To score a different strategy, write `{"<slug>": [[...],[...]]}` to a
JSON file and pass `--transforms`.

Ground truth is rebased onto the export's own OSM window before anything is
measured -- see `truth_in_export_frame`.  A saved alignment belongs to the window
it was dragged in, and scoring it against a window that has since moved measures
the move, not the placement.

Usage
-----
    ~/.venvs/strm2stl/Scripts/python.exe tools/align_tool/eval_registration.py
    ... --transforms my_solves.json --json out/eval.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

# The repo root, so `numpy2stl` imports work from any cwd (same trick as
# export_align_data.py).
import paths  # noqa: E402
from numpy2stl.registration.align.metrics import score_alignment  # noqa: E402
from numpy2stl.registration.align.segmentation import building_mask  # noqa: E402
from numpy2stl.registration.align.transform import apply_transform  # noqa: E402

# Follows the same override the exporter reads, so a set exported into a scratch
# directory can be scored without disturbing the one the align tool serves.
DATA_DIR = paths.DATA
TRUTH_DIR = paths.GROUND_TRUTH

# score_alignment keys worth printing, in the order they appear in the table.
# 'overlap_iou' is the pipeline's own gate metric.
SCORE_KEYS = ("overlap_iou", "footprint_iou", "edge_iou", "edge_lift", "height_corr")


def invert_affine(M: np.ndarray) -> np.ndarray:
    """Inverse of a 2x3 affine, in the same cv2 convention."""
    full = np.vstack([M, [0.0, 0.0, 1.0]])
    return np.linalg.inv(full)[:2, :]


def decompose(M: np.ndarray) -> tuple[float, float]:
    """(uniform scale, rotation in degrees) of a 2x3 affine."""
    scale = math.sqrt(M[0, 0] ** 2 + M[1, 0] ** 2)
    rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
    return scale, rot


def corner_error_px(pred: np.ndarray, truth: np.ndarray, shape: tuple[int, int]) -> tuple[float, float]:
    """Mean and max displacement, in target pixels, between two transforms.

    Compares where each transform sends the four corners of the source frame.  This
    is preferred over comparing tx/ty directly because a rotation or scale error
    shows up as translation at the corners but not at the origin, and because it
    needs no decomposition of the matrix.
    """
    h, w = shape
    corners = np.array([[0.0, 0.0], [w, 0.0], [0.0, h], [w, h]])
    homog = np.hstack([corners, np.ones((4, 1))])
    dp = homog @ pred.T
    dt = homog @ truth.T
    dist = np.linalg.norm(dp - dt, axis=1)
    return float(dist.mean()), float(dist.max())


def segmentation_scores(stl: np.ndarray, osm: np.ndarray, truth: np.ndarray,
                        cell_size_m: float | None) -> dict:
    """Compare the STL building mask to the OSM one, both in STL pixel space.

    The transform maps STL px -> OSM px, so its inverse brings the OSM footprints into the
    STL frame.  `building_mask(source='osm')` is exactly `~isnan`, so what crosses the warp
    is the building indicator itself rather than the heights.

    Both rasters arrive already segmented.  `stl_heightmap.npy` holds the plate's height
    above its own local ground with NaN where there is no building -- the export does
    that work, because the raw render is absolute model z and a threshold on it follows
    the terrain rather than the buildings.  Running `building_mask` over it again would
    estimate a ground surface for a raster whose ground is already gone, so both sides
    are read the same way, as the cells that are not NaN.
    """
    inv = invert_affine(truth)

    stl_mask = building_mask(stl, source="osm")

    # Warp the building indicator rather than the heights.  `apply_transform` interpolates
    # linearly and then discards any output pixel that drew more than half its weight from
    # NaN, which for a mask is an erosion: the plate frame is 1.5x finer than the OSM one
    # here, so almost every footprint edge pixel borrows from a no-building neighbour, and
    # about a third of the OSM mask disappears before it is ever scored.  Warping a 0/1
    # indicator and cutting at a half keeps the same area-majority rule without the NaN
    # asymmetry.  The comparison against NaN is deliberate: out-of-domain pixels stay NaN
    # and fail the test, which is what `covered` then accounts for.
    osm_ind = apply_transform((~np.isnan(osm)).astype(np.float64), inv,
                              output_shape=stl.shape)
    with np.errstate(invalid="ignore"):
        osm_mask = osm_ind >= 0.5

    # Only score where the warped OSM frame actually covers the STL frame; outside it
    # every pixel is NaN and the STL mask would collect false positives for free.
    # NaN means two different things after the warp — "outside the OSM raster" and
    # "inside it but no building" — so recover the domain by warping a constant image,
    # where the only NaNs left are the out-of-domain ones.
    covered = ~np.isnan(apply_transform(np.zeros_like(osm), inv, output_shape=stl.shape))

    s = stl_mask & covered
    o = osm_mask & covered
    inter = float(np.logical_and(s, o).sum())
    union = float(np.logical_or(s, o).sum())
    return {
        "seg_iou": inter / union if union else 0.0,
        "seg_precision": inter / float(s.sum()) if s.sum() else 0.0,
        "seg_recall": inter / float(o.sum()) if o.sum() else 0.0,
        "stl_coverage": float(s.sum()) / float(covered.sum()) if covered.sum() else 0.0,
        "osm_coverage": float(o.sum()) / float(covered.sum()) if covered.sum() else 0.0,
    }


def truth_is_unverified(truth_doc: dict) -> bool:
    """Was this ground truth ever actually moved by hand?

    The align tool writes the file whether or not the operator dragged anything, and it
    records the pipeline guess it started from alongside the saved placement.  When the
    two are the same matrix the file is not ground truth at all: it is a snapshot of what
    some earlier version of the pipeline happened to predict, and scoring against it
    measures agreement with that old pipeline rather than agreement with the city.

    Such a city is still scored -- the numbers are informative next to the others -- but
    it is marked in the table and left out of the means, because a stale guess can sit
    kilometres from the truth and drag the headline error with it.
    """
    guess = (truth_doc.get("pipeline_guess") or {}).get("matrix")
    if guess is None:
        return False
    return bool(np.allclose(np.asarray(truth_doc["transform"], dtype=np.float64),
                            np.asarray(guess, dtype=np.float64), atol=1e-6))


def truth_in_export_frame(truth_doc: dict, meta: dict) -> np.ndarray:
    """The hand alignment, rebased onto the window the export actually wrote.

    A saved alignment is a transform from STL pixels into *the OSM window it was
    dragged in*, and that window is not this one.  The drag tool fetches at
    whatever resolution its margin asks for -- 1024 at 3x where the export
    writes 512 -- and, more importantly, the window moves whenever the plate's
    centre or span is corrected.  Miami's alignment was recorded before its span
    went from 2000 m to 2016 m, so its window sits a few hundred metres from the
    current one.

    Comparing the two matrices directly therefore measures the difference
    between two coordinate systems, not the difference between two placements.
    It read 3.6 km of "error" for Miami while every mask metric said the
    prediction fitted better than the truth it was being scored against, which
    is the signature of a frame mismatch rather than a bad solve.

    Both windows are axis-aligned lat/lon boxes with row 0 = south, the same
    convention `locate.center_from_ground_truth` reads, so the map from one to
    the other is a diagonal affine and the rebase is exact.

    The resolution correction has to be applied on both sides.  A saved transform maps
    STL pixels to OSM pixels, and a saved alignment records one resolution because the
    export it was dragged against wrote both rasters at that width.  Rebasing only the
    OSM side leaves the transform expecting STL pixels in the old frame while the caller
    hands it corners in the new one, which is invisible while every export is 512 and
    catastrophic the moment one is not: Paris exported at 1024 scored 1659 m of corner
    error against a truth that was fitting it perfectly well.
    """
    bbox_gt = truth_doc.get("osm_bbox_nsew")
    bbox_cur = meta.get("osm_bbox_nsew")
    truth = np.asarray(truth_doc["transform"], dtype=np.float64)
    if not bbox_gt or not bbox_cur:
        return truth

    res_gt = float(truth_doc.get("resolution", meta["resolution"]))
    res_cur = float(meta["resolution"])
    n_g, s_g, e_g, w_g = (float(v) for v in bbox_gt)
    n_c, s_c, e_c, w_c = (float(v) for v in bbox_cur)

    ax = (e_g - w_g) * res_cur / (res_gt * (e_c - w_c))
    bx = (w_g - w_c) * res_cur / (e_c - w_c)
    ay = (n_g - s_g) * res_cur / (res_gt * (n_c - s_c))
    by = (s_g - s_c) * res_cur / (n_c - s_c)

    rebase = np.array([[ax, 0.0, bx], [0.0, ay, by], [0.0, 0.0, 1.0]])
    # Right-multiplied, so it converts a corner given in the current STL frame into the
    # STL frame the alignment was dragged in before the saved transform is applied.
    src = res_gt / res_cur
    unscale = np.array([[src, 0.0, 0.0], [0.0, src, 0.0], [0.0, 0.0, 1.0]])
    full = np.vstack([truth, [0.0, 0.0, 1.0]])
    return (rebase @ full @ unscale)[:2]


def evaluate_city(slug: str, pred_override: np.ndarray | None = None) -> dict | None:
    """Score one city, or return None if its export or ground truth is missing."""
    city_dir = DATA_DIR / slug
    truth_path = TRUTH_DIR / f"{slug}.json"
    meta_path = city_dir / "meta.json"
    stl_path = city_dir / "stl_heightmap.npy"
    osm_path = city_dir / "osm_buildings.npy"

    missing = [p.name for p in (truth_path, meta_path, stl_path, osm_path) if not p.exists()]
    if missing:
        return {"slug": slug, "skipped": f"missing {', '.join(missing)}"}

    meta = json.loads(meta_path.read_text())
    truth_doc = json.loads(truth_path.read_text())

    stl = np.load(stl_path)
    osm = np.load(osm_path)
    cell_size_m = meta.get("cell_size_m")

    truth = truth_in_export_frame(truth_doc, meta)
    if pred_override is not None:
        pred = np.asarray(pred_override, dtype=np.float64)
        source = "override"
    else:
        pred = np.asarray(meta["pipeline_guess"]["matrix"], dtype=np.float64)
        source = "pipeline_guess"

    mean_px, max_px = corner_error_px(pred, truth, stl.shape)
    pred_scale, pred_rot = decompose(pred)
    truth_scale, truth_rot = decompose(truth)
    rot_err = (pred_rot - truth_rot + 180.0) % 360.0 - 180.0

    result = {
        "slug": slug,
        "city": meta.get("city", slug),
        "prediction_source": source,
        "identical_to_truth": bool(np.allclose(pred, truth)),
        "truth_unverified": truth_is_unverified(truth_doc),
        "cell_size_m": cell_size_m,
        "corner_err_px_mean": mean_px,
        "corner_err_px_max": max_px,
        "corner_err_m_mean": mean_px * cell_size_m if cell_size_m else None,
        "corner_err_m_max": max_px * cell_size_m if cell_size_m else None,
        "rot_err_deg": rot_err,
        "scale_err_frac": pred_scale / truth_scale - 1.0 if truth_scale else None,
        "pred": {"scale": pred_scale, "rot_deg": pred_rot},
        "truth": {"scale": truth_scale, "rot_deg": truth_rot},
    }

    # source_kind="osm" because the exported plate raster already holds height above its
    # own ground with NaN for no building; the default would estimate a ground surface for
    # a raster whose ground is already gone and throw most of the buildings away.
    result["score_pred"] = {k: float(v) for k, v in
                            score_alignment(stl, osm, pred, cell_size_m=cell_size_m,
                                            source_kind="osm").items()
                            if k in SCORE_KEYS}
    result["score_truth"] = {k: float(v) for k, v in
                             score_alignment(stl, osm, truth, cell_size_m=cell_size_m,
                                             source_kind="osm").items()
                             if k in SCORE_KEYS}
    # Scored twice, under each placement.  Warping OSM by the ground truth is the right
    # thing only while the ground truth is the better placement; where the refinement beats
    # it, the truth-warped numbers measure the truth's error and say nothing about how well
    # the two rasters were segmented.  The prediction-warped pair is the one to read when
    # `overlap_iou` above shows pred ahead of truth.
    result["segmentation"] = segmentation_scores(stl, osm, truth, cell_size_m)
    result["segmentation_pred"] = segmentation_scores(stl, osm, pred, cell_size_m)
    return result


def format_table(rows: list[dict]) -> str:
    """Fixed-width report; three blocks matching the three things measured."""
    scored = [r for r in rows if "skipped" not in r]
    lines = []

    lines.append("GEOMETRIC ERROR (prediction vs hand-aligned ground truth)")
    lines.append(f"{'city':<22}{'corner err m':>14}{'max m':>10}{'rot deg':>10}{'scale %':>10}")
    for r in scored:
        scale_pct = "" if r["scale_err_frac"] is None else f"{r['scale_err_frac'] * 100:>10.2f}"
        lines.append(
            f"{r['city']:<22}{r['corner_err_m_mean']:>14.1f}{r['corner_err_m_max']:>10.1f}"
            f"{r['rot_err_deg']:>10.2f}{scale_pct}"
            + ("   [prediction == ground truth]" if r["identical_to_truth"] else "")
            + ("   [UNVERIFIED truth: never hand-corrected]" if r["truth_unverified"] else "")
        )

    lines.append("")
    # ASCII only in the table: the Windows console is cp1252 and mangles em dashes.
    lines.append("REGISTRATION QUALITY  (pred / truth; truth is the ceiling for these masks)")
    header = f"{'city':<22}" + "".join(f"{k:>16}" for k in SCORE_KEYS)
    lines.append(header)
    for r in scored:
        cells = "".join(
            f"{r['score_pred'][k]:>7.3f}/{r['score_truth'][k]:<8.3f}" for k in SCORE_KEYS
        )
        lines.append(f"{r['city']:<22}{cells}")

    lines.append("")
    lines.append("SEGMENTATION QUALITY  (STL mask vs OSM mask warped by each placement; Layer 1 target)")
    lines.append("  read the pred columns wherever overlap_iou above has pred ahead of truth")
    lines.append(f"{'city':<22}{'IoU':>8}{'precision':>11}{'recall':>9}{'STL cov':>9}{'OSM cov':>9}"
                 f"{'| pred IoU':>12}{'precision':>11}{'recall':>9}{'OSM cov':>9}")
    for r in scored:
        s = r["segmentation"]
        q = r["segmentation_pred"]
        lines.append(
            f"{r['city']:<22}{s['seg_iou']:>8.3f}{s['seg_precision']:>11.3f}"
            f"{s['seg_recall']:>9.3f}{s['stl_coverage']:>9.3f}{s['osm_coverage']:>9.3f}"
            f"{q['seg_iou']:>12.3f}{q['seg_precision']:>11.3f}"
            f"{q['seg_recall']:>9.3f}{q['osm_coverage']:>9.3f}"
        )

    skipped = [r for r in rows if "skipped" in r]
    if skipped:
        lines.append("")
        lines.append("SKIPPED")
        for r in skipped:
            lines.append(f"  {r['slug']}: {r['skipped']}")

    trusted = [r for r in scored if not r["truth_unverified"]]
    if trusted:
        lines.append("")
        n = len(trusted)
        lines.append(
            f"MEAN over {n} city(ies): corner {sum(r['corner_err_m_mean'] for r in trusted) / n:.1f} m, "
            f"|rot| {sum(abs(r['rot_err_deg']) for r in trusted) / n:.2f} deg, "
            f"overlap_iou {sum(r['score_pred']['overlap_iou'] for r in trusted) / n:.3f}, "
            f"seg IoU {sum(r['segmentation']['seg_iou'] for r in trusted) / n:.3f}"
            f" (pred-warped {sum(r['segmentation_pred']['seg_iou'] for r in trusted) / n:.3f})"
        )
        excluded = [r["city"] for r in scored if r["truth_unverified"]]
        if excluded:
            lines.append("  excluded from the mean, ground truth never hand-corrected: "
                         + ", ".join(excluded))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cities", nargs="*", default=None,
                    help="slugs to evaluate (default: every city with a ground-truth file)")
    ap.add_argument("--transforms", type=Path, default=None,
                    help='JSON of {"<slug>": 2x3 matrix} to score instead of the pipeline guess')
    ap.add_argument("--json", type=Path, default=None, help="also write the full results here")
    args = ap.parse_args()

    overrides = json.loads(args.transforms.read_text()) if args.transforms else {}

    if args.cities:
        slugs = args.cities
    else:
        slugs = sorted(p.stem for p in TRUTH_DIR.glob("*.json"))

    if not slugs:
        print(f"No ground-truth files in {TRUTH_DIR}. Align cities in the align tool first.")
        return 1

    rows = [r for r in (evaluate_city(s, overrides.get(s)) for s in slugs) if r]
    print(format_table(rows))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2))
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
