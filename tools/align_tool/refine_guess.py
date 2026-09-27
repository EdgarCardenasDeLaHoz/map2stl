"""Polish the drag tool's opening transform against the images it will be shown over.

The export places a plate by construction: the OSM window is `osm_margin` times the plate's
footprint, so the plate fills `1 / osm_margin` of the frame and sits in the middle of it.
That placement is exactly as good as the two numbers it is built from -- the centre, which
`locate` solves against OSM water, and the span, which is only measured when a human has
hand-aligned the plate.  Everything else about it is arithmetic.

This module measures the two rasters against each other and corrects what is left.  It reads
only what the export already wrote to disk, so a pass over all eight cities takes seconds and
touches no network.

What it corrects, and why only this:

* **Translation, always.**  A residual centre error of a few pixels survives the solve, and
  the building masks pin it down well: from seven starting points up to twelve pixels apart
  the optimum lands within a pixel of itself on a healthy city.

* **Span, only when nothing has measured it.**  Sweeping span on a plate whose span is known
  from a hand alignment reproduces the human to within two or three percent and always scores
  slightly better doing it -- which is the fit absorbing the plate's decorations and its
  simplified footprints, not a real correction.  Where no hand alignment exists the span is
  the nominal 2000 m and is often wrong by a lot: this sweep reads Valencia 9.5% and Prague
  7.5% over nominal, and `locate`'s own span search, which uses water rather than buildings
  and a different window, independently reads 2160 m for both.  Two methods that share no
  input agreeing to within a percent is the reason this is trusted at all.

* **Nothing else.**  Rotation is zero by construction.  Anisotropic scale was tried and makes
  every city worse; so does fitting span and translation together on a plate whose span is
  already known.

The building channel is the two height rasters themselves, not masks cut from them.  The plate
is a printable model, so it has no courtyards and no alleys narrower than a nozzle: threshold
its relief and a city block becomes one lump covering 80% of the plate.  Thresholding the OSM
side too then correlates that lump against per-building lace.  Correlating the heights instead
keeps the structure both rasters carry inside their blocks, and roughly doubles the peak height
on most cities -- Paris z 8.7 to 16.3, Miami 9.8 to 16.6, Prague 6.6 to 10.9 -- while moving the
spans it reads by under half a percent.  Lisbon, which the mask channel rejected on a weak peak,
passes on heights.  Salzburg is the one city the change costs, because its relief is terrain
rather than buildings; it still clears every gate.

Rejected outright: a satellite-edge channel.  It scores an order of magnitude weaker than the
building masks (r 0.10-0.30, no peak margin) and puts Paris 246 px out, because the imagery is
not registered to OSM to the accuracy this needs.

Every result carries the evidence for accepting it -- peak height, margin over the runner-up,
the spread of the optima from a fan of starting points, and the change in water overlap, which
for a buildings-driven fit is a cue the fit never saw.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import locate  # noqa: E402
import numpy as np

# Promoted to city2stl.registration.correlate (2026-09-27); re-exported so
# `refine_guess.ncc_surface` / `.height_channel` keep working for the other tools.
from city2stl.registration.correlate import height_channel, ncc_surface  # noqa: E402,F401

DATA = Path(__file__).resolve().parent / "data"

# Span sweep for a plate nobody has measured.  `locate`'s own search uses 0.78-1.20 of
# nominal; this is the same range with the bottom trimmed, because a plate that reads 20%
# small here has something wrong with it that a scale factor should not paper over.
SPAN_SWEEP = np.arange(0.85, 1.2001, 0.005)

# How far the fit is allowed to move the plate before the result is treated as a re-solve
# rather than a polish.  Twenty-five pixels is about 150 m at the usual cell size, and the
# solver's own validation never misses a hand alignment by more than 72 m.
MAX_SHIFT_PX = 25.0

# Acceptance gates.  `z` and `margin` are the coarse peak's height and its lead over the
# runner-up, both in standard deviations of the correlation surface; `spread` is how far the
# continuous optima from a fan of starting points scatter, in pixels.
MIN_PEAK_Z = 4.0
MIN_MARGIN = 1.0
MAX_SPREAD_PX = 4.0

# How close a start's score must come to the best one before its answer counts as a rival
# basin rather than a stalled search.  The absolute tolerance alone is too tight to be read
# as "a competing answer": across the eight cities every start that lands away from the
# winner scores between 22 and 49 per cent worse than it, while every start that lands on
# the winner is within a thousandth.  Nothing in between has ever been observed, so the
# relative test is the one that carries the meaning and the absolute one is only a floor
# for cities whose correlation is weak in absolute terms.
AGREE_TOL = 0.01
RIVAL_TOL_FRAC = 0.05

# At least this many of the eight must reach the winning basin.  The fan seeds two starts
# on placements the export already believes in, so three means one of the six blind offsets
# found the same answer independently.  Asking for more measures how many descents happened
# not to stall, which is not evidence about the fit: Lisbon's four agreeing starts sit
# within 0.2 px of each other under a peak of z 11.2 with a margin of 7.7 over the runner-up,
# and the four that miss are stalled simplices scoring 0.43 to 0.50 against the winner's
# 0.636.  There is no second basin there to be protected from.
MIN_AGREE = 3

# What it takes for the span sweep to overrule a span somebody measured by hand.  A hand
# alignment is evidence and the sweep does not get to disagree with it on a whim, so the
# sweep has to win on both the correlation and the confidence of its peak, not on either
# alone.  Salzburg is the case this exists for: its saved alignment is the only one that
# ever moved the scale, and it moved it about three and a half per cent too far, which the
# sweep answers with r 0.34 against the measurement's 0.15 and a peak of z 12.0 against
# 5.5 -- and, warping the footprints into the plate frame to check, mask IoU 0.359 against
# 0.256 with precision and recall both up.
SPAN_OVERRIDE_R = 1.10
SPAN_OVERRIDE_Z = 2.0

# A water channel is only used when both sides actually have water in the window.
MIN_WATER_FRAC = 0.02

# How much water overlap the fit is allowed to give up.  Where buildings drive the fit this
# is a genuine second opinion, so a real loss here vetoes the result.
MAX_WATER_LOSS = 0.01


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _warp(plate, scale, tx, ty, res, nearest=False):
    import cv2
    M = np.array([[scale, 0.0, tx], [0.0, scale, ty]], dtype=np.float64)
    flag = cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR
    warped = cv2.warpAffine(plate, M, (res, res), flags=flag, borderValue=0.0)
    support = cv2.warpAffine(np.ones_like(plate), M, (res, res),
                             flags=cv2.INTER_NEAREST, borderValue=0.0) > 0.5
    return warped, support


def pearson_at(plate, osm, scale, tx, ty):
    """Correlation between the plate feature placed at (scale, tx, ty) and the OSM feature."""
    res = osm.shape[0]
    warped, support = _warp(plate, scale, tx, ty, res)
    if support.sum() < 100:
        return -1.0
    a = warped[support]
    b = osm[support]
    a = a - a.mean()
    b = b - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else -1.0


def iou_at(plate, osm, scale, tx, ty):
    """Intersection over union of two masks, inside the plate footprint."""
    res = osm.shape[0]
    warped, support = _warp(plate, scale, tx, ty, res, nearest=True)
    a = warped > 0.5
    b = (osm > 0.5) & support
    union = int((a | b).sum())
    return float((a & b).sum() / union) if union else float("nan")


def coarse_peak(plate, osm, scale, fixed_ffts=None):
    """Best whole-pixel translation of the plate feature at a fixed scale."""
    res = osm.shape[0]
    size = int(round(res * scale))
    small = locate.resample(plate, size)
    template, _, _ = locate._pad_centered(small, res)
    support, _, _ = locate._pad_centered(np.ones_like(small), res)
    surface = ncc_surface(osm, template, support, fixed_ffts)
    peaks = locate.find_peaks(surface, separation=max(8, size // 12), count=3)
    p = peaks[0]
    base = (res - size) / 2.0
    return {"tx": base + (p["x"] - res // 2), "ty": base + (p["y"] - res // 2),
            "r": float(surface[p["y"], p["x"]]), "z": p["z"],
            "margin": p["z"] - peaks[1]["z"]}


def solve_span(plate_b, osm_b, scale, sweep=SPAN_SWEEP):
    """The span factor whose best translation fits the building masks best.

    Only the buildings are used.  Water is a ridge -- a river scores almost the same at
    every span once it is allowed to slide along itself -- so it settles nothing here and is
    kept back as an independent check on the answer.
    """
    res = osm_b.shape[0]
    ffts = (np.fft.rfft2(osm_b), np.fft.rfft2(osm_b * osm_b))
    best = None
    for f in sweep:
        s = scale * float(f)
        if int(round(res * s)) > res:
            continue
        cand = coarse_peak(plate_b, osm_b, s, ffts)
        cand.update({"f": float(f), "scale": s})
        if best is None or cand["r"] > best["r"]:
            best = cand
    return best


# --------------------------------------------------------------------------
# Refinement
# --------------------------------------------------------------------------

def refine_arrays(plate_b, osm_b, plate_w, osm_w, scale, span_locked: bool,
                  cell_size_m: float = 1.0) -> dict:
    """Refine one plate's opening transform from its exported rasters.

    `scale` is the geometric anchor the export computed.  `span_locked` says whether the
    plate's span came from a hand alignment; when it did, the sweep is skipped and only
    translation moves.
    """
    from scipy.optimize import minimize

    res = osm_b.shape[0]
    base = (res - res * scale) / 2.0
    use_water = (plate_w is not None and osm_w is not None
                 and float(plate_w.mean()) > MIN_WATER_FRAC
                 and float(osm_w.mean()) > MIN_WATER_FRAC)

    # A measured span is the starting position, not the last word.  The export has already
    # built the OSM window at whatever the hand alignment said, so `f = 1` is that
    # measurement; sweeping anyway costs one extra correlation pass and is the only way to
    # find out that a measurement was wrong.  The sweep only replaces it when it wins on
    # both counts, so an ordinary bit of sweep noise cannot overturn a person's work.
    span = solve_span(plate_b, osm_b, scale)
    if span_locked:
        measured = {"f": 1.0, "scale": scale}
        measured.update(coarse_peak(plate_b, osm_b, scale))
        better = (span["r"] >= measured["r"] * SPAN_OVERRIDE_R
                  and span["z"] >= measured["z"] + SPAN_OVERRIDE_Z)
        span["overruled_measured_span"] = bool(better)
        if not better:
            measured["overruled_measured_span"] = False
            span = measured
    s = span["scale"]

    def cost(v):
        r = pearson_at(plate_b, osm_b, s, v[0], v[1])
        if use_water:
            r = 0.5 * (r + pearson_at(plate_w, osm_w, s, v[0], v[1]))
        return -r

    # A fan of starting points, not one.  Where the landscape has a single basin they all
    # arrive at the same place and the scatter is the confidence; where it does not, the
    # scatter says so, which is how Prague and Valencia were caught fitting a span that was
    # never theirs.
    span_base = (res - res * s) / 2.0
    starts = [(span["tx"], span["ty"]), (span_base, span_base)]
    starts += [(span_base + dx, span_base + dy)
               for dx, dy in ((8, 0), (-8, 0), (0, 8), (0, -8), (12, 12), (-12, -12))]
    optima = [minimize(cost, np.array(x0, dtype=float), method="Nelder-Mead",
                       options={"xatol": 0.02, "fatol": 1e-6, "maxiter": 400})
              for x0 in starts]
    best = min(optima, key=lambda o: o.fun)

    # Count only the starts that actually arrived somewhere competitive.  A downhill search
    # occasionally stalls with its simplex collapsed -- Valencia has one start that stops at
    # r 0.11 against the other seven's 0.35 -- and scoring that as a rival basin would fail a
    # city whose optimum every serious start agrees on.  An answer a quarter as good is a
    # failed descent, not a second opinion.  The tolerance is therefore proportional to the
    # winning score, so that a city correlating at 0.34 is judged on the same terms as one
    # correlating at 0.80.
    scores = np.array([-o.fun for o in optima])
    top = float(scores.max())
    agree = scores >= top - max(AGREE_TOL, RIVAL_TOL_FRAC * abs(top))
    pts = np.array([o.x for o in optima])[agree]
    spread_px = float(np.hypot(*(pts - pts.mean(axis=0)).T).max())
    n_agree = int(agree.sum())

    tx, ty = (float(v) for v in best.x)
    shift_px = math.hypot(tx - base, ty - base)

    water_geom = iou_at(plate_w, osm_w, scale, base, base) if use_water else None
    water_fit = iou_at(plate_w, osm_w, s, tx, ty) if use_water else None

    reasons = []
    if span["z"] < MIN_PEAK_Z:
        reasons.append(f"peak z {span['z']:.1f} below {MIN_PEAK_Z}")
    if span["margin"] < MIN_MARGIN:
        reasons.append(f"peak margin {span['margin']:.1f} below {MIN_MARGIN}")
    if n_agree < MIN_AGREE:
        reasons.append(f"only {n_agree} of {len(optima)} starts agree")
    if spread_px > MAX_SPREAD_PX:
        reasons.append(f"optima scatter {spread_px:.1f} px over {MAX_SPREAD_PX}")
    if shift_px > MAX_SHIFT_PX:
        reasons.append(f"shift {shift_px:.1f} px over {MAX_SHIFT_PX}")
    if use_water and water_fit < water_geom - MAX_WATER_LOSS:
        reasons.append(f"water overlap drops {water_geom - water_fit:.3f}")

    out = {
        "accepted": not reasons,
        "reasons": reasons,
        "scale": s,
        "span_factor": float(s / scale),
        "span_locked": bool(span_locked),
        "overruled_measured_span": bool(span.get("overruled_measured_span", False)),
        "tx": tx,
        "ty": ty,
        "geometric": {"scale": scale, "tx": base, "ty": base},
        "r": float(-best.fun),
        "peak_z": float(span["z"]),
        "peak_margin": float(span["margin"]),
        "spread_px": spread_px,
        "starts_agreeing": n_agree,
        "starts": len(optima),
        "shift_px": shift_px,
        "shift_m": shift_px * float(cell_size_m),
        "water": bool(use_water),
        "water_iou_geometric": water_geom,
        "water_iou_refined": water_fit,
    }
    if out["accepted"]:
        if not span_locked or out["overruled_measured_span"]:
            # The span this fit reads the plate as covering, recorded whenever the fit was
            # free to move it -- including the case where a measured span was overruled,
            # since that is exactly the reading a later export most needs.  A later export
            # can pick this up and build the OSM window at that size instead of the nominal
            # one, which would let the plate sit at the geometric placement again with
            # nothing left to correct.
            out["span_m"] = float(cell_size_m) * res * out["scale"]
    else:
        out.update({"scale": scale, "tx": base, "ty": base, "span_factor": 1.0})
    out["matrix"] = [[out["scale"], 0.0, out["tx"]], [0.0, out["scale"], out["ty"]]]
    return out


# --------------------------------------------------------------------------
# On-disk exports
# --------------------------------------------------------------------------

def load_export(slug: str, data_dir: Path = DATA):
    """The four masks the refinement needs, plus the export's own metadata."""
    from PIL import Image
    d = data_dir / slug
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    res = int(meta["resolution"])

    plate_b = height_channel(np.load(d / "stl_heightmap.npy"))
    osm_b = height_channel(np.load(d / "osm_buildings.npy"))

    plate_w = (np.asarray(Image.open(d / "stl_water.png").convert("L")) > 127).astype(np.float32)
    if plate_w.shape[0] != plate_b.shape[0]:
        plate_w = locate.resample(plate_w, plate_b.shape[0])
    osm_w = (np.asarray(Image.open(d / "osm_water.png").convert("L").resize((res, res)))
             > 127).astype(np.float32)
    return meta, plate_b, osm_b, plate_w, osm_w


def span_is_measured(meta: dict) -> bool:
    """Whether anybody has actually measured this plate's span.

    `locate.measured_span_m` answers a coarser question: it returns a span whenever a hand
    alignment exists.  But the drag tool opens on the geometric placement, and a person who
    drags the plate into position without resizing it saves that opening scale straight back
    out.  The span that comes back is then the nominal one wearing a measurement's clothes.

    So the test is whether the saved alignment moved the scale off the placement it opened
    on -- both numbers live in the ground truth record, which makes this independent of what
    the current export happens to have been built at.  Of the six saved alignments only
    Salzburg's moved it, from 0.6667 to 0.5488; the other five sit on the opening value to
    six decimal places, which is why Bilbao, Lisbon, Miami and Paris all "measure" within
    half a percent of the nominal 2000 m.
    """
    path = locate.GROUND_TRUTH / f"{locate.slug(meta['region'])}.json"
    if not path.exists():
        return False
    gt = json.loads(path.read_text(encoding="utf-8"))
    hand = float(gt.get("transform_params", {}).get("scale", 0.0))
    guess = (gt.get("pipeline_guess") or {}).get("matrix")
    if not hand or guess is None:
        return False
    return abs(hand / float(guess[0][0]) - 1.0) > 0.01


def refine_slug(slug: str, data_dir: Path = DATA) -> tuple[dict, dict]:
    """Refine one already-exported city, deciding for itself whether its span is known.

    The geometric placement is the anchor, not whatever `pipeline_guess` currently holds,
    so re-running over a city that has already been refined starts from the same place and
    lands in the same one rather than compounding.
    """
    meta, plate_b, osm_b, plate_w, osm_w = load_export(slug, data_dir)
    geometric = meta.get("geometric_guess", meta["pipeline_guess"])
    fit = refine_arrays(plate_b, osm_b, plate_w, osm_w, float(geometric["scale"]),
                        span_is_measured(meta), cell_size_m=float(meta["cell_size_m"]))
    return fit, meta


def as_guess(fit: dict) -> dict:
    """The refinement in the shape the drag tool expects a `pipeline_guess` to be."""
    return {
        "matrix": fit["matrix"],
        "scale": fit["scale"],
        "rot_deg": 0.0,
        "tx": fit["tx"],
        "ty": fit["ty"],
        "source": "refined" if fit["accepted"] else "geometric",
    }


def apply_to_disk(results: dict[str, dict], data_dir: Path = DATA) -> None:
    """Write the accepted refinements into meta.json and into align_data.js.

    The geometric placement is kept alongside as `geometric_guess`, so the arithmetic the
    export started from is never lost and a later run can always fall back to it.
    """
    for slug, fit in results.items():
        if not fit["accepted"]:
            continue
        path = data_dir / slug / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta.setdefault("geometric_guess", meta["pipeline_guess"])
        meta["pipeline_guess"] = as_guess(fit)
        meta["refinement"] = {k: v for k, v in fit.items() if k != "matrix"}
        if fit.get("span_m"):
            meta["refined_span_m"] = fit["span_m"]
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    js = data_dir / "align_data.js"
    if not js.exists():
        return
    text = open(js, encoding="utf-8").read()
    head, tail = text[: text.index("{")], text[text.rindex("}") + 1:]
    payload = json.loads(text[text.index("{"): text.rindex("}") + 1])
    for slug, fit in results.items():
        if not fit["accepted"] or slug not in payload:
            continue
        meta = payload[slug]["meta"]
        meta.setdefault("geometric_guess", meta["pipeline_guess"])
        meta["pipeline_guess"] = as_guess(fit)
        meta["refinement"] = {k: v for k, v in fit.items() if k != "matrix"}
        if fit.get("span_m"):
            meta["refined_span_m"] = fit["span_m"]
    open(js, "w", encoding="utf-8").write(head + json.dumps(payload) + tail)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cities", nargs="*", help="slugs to refine; default all exported")
    ap.add_argument("--apply", action="store_true",
                    help="write accepted refinements into meta.json and align_data.js")
    ap.add_argument("--data", type=Path, default=DATA)
    args = ap.parse_args(argv)

    slugs = args.cities or sorted(p.parent.name for p in args.data.glob("*/meta.json"))
    results = {}
    print("%-24s %6s %7s %7s %6s %6s %6s  %s"
          % ("city", "span", "shift m", "r", "z", "marg", "sprd", "water IoU / verdict"))
    for slug in slugs:
        fit, meta = refine_slug(slug, args.data)
        results[slug] = fit
        water = ("%.3f->%.3f" % (fit["water_iou_geometric"], fit["water_iou_refined"])
                 if fit["water"] else "no water")
        verdict = "accepted" if fit["accepted"] else "kept geometric: " + "; ".join(fit["reasons"])
        print("%-24s %6.3f %7.0f %7.3f %6.1f %6.1f %6.1f  %s  %s"
              % (slug, fit["span_factor"], fit["shift_m"], fit["r"], fit["peak_z"],
                 fit["peak_margin"], fit["spread_px"], water, verdict))
        if fit.get("span_m"):
            print("%-24s   span reads %.0f m against the nominal %.0f m"
                  % ("", fit["span_m"], fit["span_m"] / fit["span_factor"]))

    if args.apply:
        apply_to_disk(results, args.data)
        n = sum(1 for f in results.values() if f["accepted"])
        print(f"applied {n} of {len(results)} refinements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
