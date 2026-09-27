"""The tile-consensus check on a plate placement: pass, review or fail, with reasons.

Promoted 2026-09-27 from ``tools/align_tool/auto_register.py`` (whose docstring explains the
method) so the web app's plate-registration panel can run it in-process.  The tool keeps
pack discovery, adoption and the batch export, and imports everything below from here.

  crop_consensus()   an independent check that needs no ground truth
  verdict()          pass, review or fail, with the reason attached
  correct()          move a placement onto the position the tiles agree on
  score_export()     all three over a pack already on disk (``data/<slug>/``)
  record_verdict()   write the verdict into the pack's meta.json and the drag tool's bundle

Rasters are the export's: ``stl_heightmap.npy`` (plate) and ``osm_buildings.npy`` (map), both
row 0 = south; the matrix maps plate pixels to map pixels.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from city2stl.registration.align_paths import data_dir as _data_dir
from city2stl.registration.correlate import find_peaks, height_channel, ncc_surface

# --------------------------------------------------------------------------
# Thresholds
# --------------------------------------------------------------------------

# How many tiles the plate's footprint is cut into per side. The grid is laid over the
# footprint rather than over the whole window: the plate fills about two thirds of the frame,
# so a grid over the frame puts most of its tiles on ground the plate does not cover, and
# they abstain. Over the footprint, four per side gives sixteen tiles of roughly 400 m, large
# enough that each still contains several blocks -- the structure a correlation locks onto --
# and small enough that sixteen of them are not all reading the same few landmarks.
TILES = 4

# A tile with almost no buildings in it has nothing to say, and a tile that barely overlaps
# the plate is being asked about ground the plate does not cover. Both abstain rather than
# voting noise.
MIN_TILE_FILL = 0.08
MIN_TILE_OVERLAP = 0.35

# A tile counts as agreeing when it lands within this fraction of the plate's span of where
# the placement says it should. At 2 % of a 1700 m plate that is 34 m, which is finer than
# the block structure the tiles are matching on, so agreement means the same blocks and not
# merely the same neighbourhood.
AGREE_FRAC = 0.02

# `MIN_TILE_R` only stops a tile that matched nothing at all from being counted as a vote
# either way; it is not a confidence gate, since the votes are weighted by r afterwards.
MIN_TILE_R = 0.10

# What it takes to believe a placement, expressed as the shipped position's lead over the best
# alternative the map offers. See `crop_consensus` for why this is the deciding number rather
# than the fraction of tiles that agree: scattered dissent is noise, and a plate that is
# genuinely misplaced produces dissent that agrees with itself.
MIN_LEAD = 0.65
FAIL_LEAD = 0.45

# However decisive the lead, a handful of tiles is not a survey. Below this many voting tiles
# the answer is reported as unverified rather than confirmed.
MIN_VOTERS = 4

# When the tiles agree on a rival position, that agreement is a correction and not merely a
# complaint, so the placement is moved there and checked again. The retry is bounded: a
# correction beyond this fraction of the plate's span means the window itself is wrong -- the
# plate is not in the map it was given -- and shuffling the matrix cannot fix that.
MAX_CORRECTION_FRAC = 0.35
MAX_CORRECTIONS = 2


# --------------------------------------------------------------------------
# The ground-truth-free check
# --------------------------------------------------------------------------

def _place(stl_raw: np.ndarray, matrix, res: int):
    """The plate as the export ships it: warped into the OSM frame by the shipped matrix."""
    import cv2
    M = np.asarray(matrix, dtype=np.float64)
    height = height_channel(stl_raw)
    placed = cv2.warpAffine(height, M, (res, res), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    foot = cv2.warpAffine(np.isfinite(np.asarray(stl_raw, dtype=np.float32)).astype(np.float32),
                          M, (res, res), flags=cv2.INTER_NEAREST,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0.0) > 0.5
    return placed, foot


def crop_consensus(stl_raw, osm_raw, matrix, cell_size_m: float, span_m: float | None = None,
                   tiles: int = TILES) -> dict:
    """Do independent pieces of the map agree about where the plate is?

    Each tile of the OSM window is correlated against the placed plate on its own. Because
    both are already in the same frame, a correct placement puts every tile's peak at zero
    shift; the displacement of the peak is therefore a direct reading, in metres, of how far
    that piece of the city thinks the plate should move.

    Roles are deliberately this way round -- tile as template, plate as the fixed image --
    rather than cropping the reference and re-searching. Cropping the reference biases the
    result: a plate scored against a window with its edges blanked is rewarded for moving
    inward, so every crop pulls toward its own centre and the disagreement that produces says
    nothing about the placement. A tile used as the template is fully valid everywhere it is
    defined, so its vote is unbiased and the votes are comparable to each other.

    Returns the agreeing fraction, the median displacement in metres, and the tiles
    themselves, so a caller that wants to draw the disagreement can.
    """
    osm = height_channel(osm_raw)
    res = osm.shape[0]
    placed, foot = _place(stl_raw, matrix, res)
    if span_m is None:
        span_m = float(foot.sum() ** 0.5) * cell_size_m
    tol_px = max(2.0, AGREE_FRAC * span_m / cell_size_m)

    ffts = (np.fft.rfft2(placed), np.fft.rfft2(placed * placed))

    # The grid goes over the plate, not over the window. Asking a tile of map the plate does
    # not reach where the plate is has no answer, and a grid over the frame is mostly such
    # tiles: at the export's scale the plate covers two thirds of the frame in each direction,
    # so only the middle ninth of a three-by-three grid lies inside it.
    rows = np.flatnonzero(foot.any(axis=1))
    cols = np.flatnonzero(foot.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return {"tiles": tiles, "voting": 0, "agreeing": 0, "consensus": float("nan"),
                "lead": float("nan"), "biggest_cluster": 0, "endorsing": 0,
                "rival_votes": 0,
                "rival_dist_m": float("nan"), "median_dist_m": float("nan"),
                "median_r": float("nan"), "tol_m": tol_px * cell_size_m, "votes": []}
    ry0, ry1 = int(rows[0]), int(rows[-1]) + 1
    rx0, rx1 = int(cols[0]), int(cols[-1]) + 1
    ystep = max(1, (ry1 - ry0) // tiles)
    xstep = max(1, (rx1 - rx0) // tiles)

    # What share of its own bounding box the plate occupies, so that the overlap a tile is
    # asked for is a share of the plate present rather than of the tile's area.  A rectangular
    # plate square to the frame occupies all of its box and is held to `MIN_TILE_OVERLAP`
    # exactly as before; a plate turned in the frame, or cut to an outline as a monument is,
    # is held to the same share of what it has.
    plate_share = float(foot[ry0:ry1, rx0:rx1].mean())
    need_overlap = MIN_TILE_OVERLAP * max(plate_share, 1e-6)

    votes = []
    for iy in range(tiles):
        for ix in range(tiles):
            y0, x0 = ry0 + iy * ystep, rx0 + ix * xstep
            y1 = ry1 if iy == tiles - 1 else y0 + ystep
            x1 = rx1 if ix == tiles - 1 else x0 + xstep

            support = np.zeros((res, res), dtype=np.float32)
            support[y0:y1, x0:x1] = 1.0
            tile = np.zeros((res, res), dtype=np.float32)
            tile[y0:y1, x0:x1] = osm[y0:y1, x0:x1]

            fill = float((tile[y0:y1, x0:x1] > 0).mean())
            overlap = float(foot[y0:y1, x0:x1].mean())
            if fill < MIN_TILE_FILL or overlap < need_overlap:
                votes.append({"y": iy, "x": ix, "fill": fill, "overlap": overlap,
                              "abstained": True})
                continue

            surface = ncc_surface(placed, tile, support, ffts)
            peaks = find_peaks(surface, separation=max(8, min(ystep, xstep) // 4),
                                      count=2)
            p = peaks[0]
            dy = float(p["y"] - res // 2)
            dx = float(p["x"] - res // 2)
            dist_px = math.hypot(dy, dx)
            r = float(surface[p["y"], p["x"]])
            votes.append({
                "y": iy, "x": ix, "fill": fill, "overlap": overlap, "abstained": False,
                "r": r, "z": float(p["z"]),
                "dy_px": dy, "dx_px": dx,
                "dist_m": dist_px * cell_size_m,
                "agrees": bool(dist_px <= tol_px and r >= MIN_TILE_R),
            })

    voting = [v for v in votes if not v["abstained"]]
    agreeing = [v for v in voting if v["agrees"]]

    # Counting the tiles that agree is not enough on its own, because it treats a tile that
    # located nothing and a tile that located a rival the same way. They are not the same. A
    # plate that is genuinely in the wrong part of the city has every tile pulling it the same
    # way, towards where it really belongs; a plate that is correctly placed on a map with
    # some featureless quarters has a few tiles wandering off in unrelated directions. Both
    # look like dissent to a counter, and only the first is evidence.
    #
    # So the votes are grouped by where they point, and the question becomes whether any
    # group outweighs the group that endorses the shipped placement. Weight is r rather than
    # a head count: a tile that matched at 0.78 has seen more of the city than one that
    # matched at 0.36, and should not be outvoted by it.
    clusters = []
    for v in sorted(voting, key=lambda v: -v["r"]):
        for c in clusters:
            if math.hypot(v["dy_px"] - c["dy_px"], v["dx_px"] - c["dx_px"]) <= tol_px:
                c["votes"].append(v)
                c["weight"] += v["r"]
                break
        else:
            clusters.append({"dy_px": v["dy_px"], "dx_px": v["dx_px"],
                             "votes": [v], "weight": v["r"]})
    clusters.sort(key=lambda c: -c["weight"])
    biggest = max((len(c["votes"]) for c in clusters), default=0)

    here = next((c for c in clusters
                 if math.hypot(c["dy_px"], c["dx_px"]) <= tol_px), None)
    rival = next((c for c in clusters if c is not here), None)
    w_here = here["weight"] if here else 0.0
    w_rival = rival["weight"] if rival else 0.0
    lead = (w_here / (w_here + w_rival)) if (w_here or w_rival) else float("nan")

    return {
        "tiles": tiles,
        "voting": len(voting),
        "agreeing": len(agreeing),
        "consensus": (len(agreeing) / len(voting)) if voting else float("nan"),
        "lead": lead,
        "biggest_cluster": biggest,
        "endorsing": len(here["votes"]) if here else 0,
        "rival_votes": len(rival["votes"]) if rival else 0,
        "rival_dist_m": (math.hypot(rival["dy_px"], rival["dx_px"]) * cell_size_m)
                        if rival else float("nan"),
        "median_dist_m": float(np.median([v["dist_m"] for v in voting])) if voting else float("nan"),
        "median_r": float(np.median([v["r"] for v in voting])) if voting else float("nan"),
        "tol_m": tol_px * cell_size_m,
        "votes": votes,
    }


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------

def verdict(meta: dict, consensus: dict) -> dict:
    """Pass, review or fail, with every reason that contributed.

    The consensus decides, and the number it decides on is the lead: how much of the map's
    weight of opinion sits on the shipped placement rather than on the best alternative the
    tiles offer. A high lead means the tiles that located anything located the plate where it
    already is, and nothing else was in contention. A low lead means the map is genuinely
    ambiguous, or genuinely says the plate belongs elsewhere.

    Everything else on the export -- the refinement's r, its peak z, the solver's own crop
    agreement -- is recorded alongside because it is useful when reading a failure, but none
    of it is allowed to overturn a map that disagrees with itself. Those numbers all come from
    the same correlation that produced the placement, so treating them as corroboration would
    be counting one opinion several times.
    """
    reasons = []
    lead = consensus["lead"]
    ref = meta.get("refinement") or {}
    common = {
        "consensus": consensus["consensus"], "lead": lead,
        "agreeing": consensus["agreeing"], "voting": consensus["voting"],
        "endorsing": consensus["endorsing"],
        "median_dist_m": consensus["median_dist_m"],
        "rival_votes": consensus["rival_votes"],
        "biggest_cluster": consensus["biggest_cluster"],
        "rival_dist_m": consensus["rival_dist_m"],
        "refine_r": ref.get("r"), "refine_z": ref.get("peak_z"),
        "rot_deg": (meta.get("pipeline_guess") or {}).get("rot_deg"),
    }
    if not np.isfinite(lead):
        return dict(common, status="fail",
                    reasons=["no tile had enough buildings to vote"])

    if lead < FAIL_LEAD:
        status = "fail"
        # Two different things fail a plate and they need different repairs, so they are
        # reported as different things. If the tiles that reject the placement agree with each
        # other, the placement is simply in the wrong spot and they have said where the right
        # one is. If they agree with nothing, including each other, then no part of this map
        # recognises this plate -- which is not a placement fault at all, it is the window
        # being centred somewhere the plate does not depict, and nudging the matrix will never
        # fix it. Both of the plates that fail today are the second kind, both because their
        # solve gave up and fell back to the geocoded centroid.
        if consensus["biggest_cluster"] <= 2 and consensus["voting"] >= 6:
            status = "fail"
            reasons.append(
                f"no part of the map recognises this plate: all {consensus['voting']} tiles "
                f"that could vote point somewhere different, median {consensus['median_dist_m']:.0f} m "
                f"away. The window is probably centred on the wrong ground -- re-solve rather "
                f"than nudge")
        else:
            reasons.append(
                f"the map puts the plate somewhere else: {consensus['rival_votes']} tiles agree "
                f"on a position {consensus['rival_dist_m']:.0f} m away, against "
                f"{consensus['endorsing']} on the shipped one (lead {lead:.2f})")
    elif lead < MIN_LEAD:
        status = "review"
        reasons.append(
            f"the shipped position leads by only {lead:.2f}; {consensus['rival_votes']} "
            f"tiles prefer a position {consensus['rival_dist_m']:.0f} m away")
    else:
        status = "pass"

    # A decisive lead over one rival is still a small survey if only a few tiles spoke. Say so
    # rather than reporting it with the same confidence as a plate the whole map endorsed.
    if consensus["voting"] < MIN_VOTERS:
        reasons.append(f"only {consensus['voting']} tiles could vote")
        if status == "pass":
            status = "review"

    if ref and not ref.get("accepted", True):
        reasons.append("refinement was rejected; the placement is the geometric one")
    return dict(common, status=status, reasons=reasons)


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------

def correct(stl_raw, osm_raw, matrix, cell_size_m: float, span_m: float | None = None,
            max_rounds: int = MAX_CORRECTIONS) -> tuple[np.ndarray, dict, float]:
    """Move a placement to where the tiles say it belongs, for as long as that helps.

    A rival cluster is not just evidence against the placement, it is a measurement of how
    wrong it is: the tiles report the offset in the same frame the matrix translates in, so
    subtracting it moves the plate onto the position they endorse. Measured on a placement
    spoiled by a known amount, the reported offset came back equal to the spoiling to the
    pixel, and subtracting it restored every tile's vote.

    The correction is applied only when it makes things better, and only while the tiles keep
    agreeing on where to go. A plate whose window does not contain it produces a rival on the
    far side of the frame and no amount of translating will help, so a correction larger than
    a third of the plate is refused rather than applied and re-refused.

    Returns the matrix to ship, its consensus, and how far the placement was moved in metres.
    """
    M = np.asarray(matrix, dtype=np.float64).copy()
    con = crop_consensus(stl_raw, osm_raw, M, cell_size_m, span_m=span_m)
    moved = 0.0
    if span_m is None:
        return M, con, moved
    limit_px = MAX_CORRECTION_FRAC * span_m / cell_size_m

    for _ in range(max_rounds):
        if con["lead"] >= MIN_LEAD or not np.isfinite(con["lead"]):
            break
        votes = [v for v in con["votes"] if not v["abstained"]]
        if not votes:
            break
        # The rival the tiles actually agree on, weighted the way the lead weighs them, rather
        # than the average of everything -- an average over scattered dissent points nowhere.
        rival = _rival_offset(con, cell_size_m)
        if rival is None or con["biggest_cluster"] <= 2:
            break                      # scatter is not a destination
        dy, dx = rival
        if math.hypot(dy, dx) > limit_px:
            con = dict(con, refused_correction_m=math.hypot(dy, dx) * cell_size_m)
            break
        trial = M.copy()
        trial[0, 2] -= dx
        trial[1, 2] -= dy
        trial_con = crop_consensus(stl_raw, osm_raw, trial, cell_size_m, span_m=span_m)
        if not (trial_con["lead"] > con["lead"]):
            break
        M, con = trial, trial_con
        moved += math.hypot(dy, dx) * cell_size_m
    return M, con, moved


def _rival_offset(con: dict, cell_size_m: float):
    """Where the tiles that reject the placement say it should go, or None if they disagree."""
    votes = [v for v in con["votes"] if not v["abstained"]]
    tol_px = con["tol_m"] / cell_size_m
    away = [v for v in votes if math.hypot(v["dy_px"], v["dx_px"]) > tol_px]
    if not away:
        return None
    clusters = []
    for v in sorted(away, key=lambda v: -v["r"]):
        for c in clusters:
            if math.hypot(v["dy_px"] - c["dy_px"], v["dx_px"] - c["dx_px"]) <= tol_px:
                c["votes"].append(v)
                c["weight"] += v["r"]
                break
        else:
            clusters.append({"dy_px": v["dy_px"], "dx_px": v["dx_px"],
                             "votes": [v], "weight": v["r"]})
    best = max(clusters, key=lambda c: c["weight"])
    w = sum(v["r"] for v in best["votes"])
    return (sum(v["dy_px"] * v["r"] for v in best["votes"]) / w,
            sum(v["dx_px"] * v["r"] for v in best["votes"]) / w)


def score_export(slug: str, data_dir: Path | None = None, *,
                 fix: bool = True, matrix=None) -> dict | None:
    """Score a plate the export has already written out.

    `matrix` (plate px -> map px) scores that placement instead of the pack's
    `pipeline_guess`, e.g. a fresh street placement through `street_place.as_guess`.
    """
    d = (data_dir if data_dir is not None else _data_dir()) / slug
    meta_path = d / "meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    stl_raw = np.load(d / "stl_heightmap.npy")
    osm_raw = np.load(d / "osm_buildings.npy")
    cell = float(meta["cell_size_m"])
    span = meta.get("refined_span_m")
    M0 = matrix if matrix is not None else meta["pipeline_guess"]["matrix"]
    if fix:
        M, con, moved = correct(stl_raw, osm_raw, M0, cell, span_m=span)
    else:
        M, con, moved = np.asarray(M0, dtype=np.float64), crop_consensus(
            stl_raw, osm_raw, M0, cell, span_m=span), 0.0
    out = verdict(meta, con)
    out.update(slug=slug, city=meta.get("city", slug), consensus_detail=con,
               corrected_m=moved, matrix=M.tolist())
    if moved:
        out["reasons"] = [f"the tiles moved the placement {moved:.0f} m onto the position "
                          f"they agree on"] + list(out["reasons"])
    return out


def record_verdict(slug: str, out: dict, data_dir: Path | None = None) -> None:
    """Write the verdict back where the rest of the pipeline can see it.

    A check whose answer is only ever printed is the same fault this module was written to
    fix: the solved rotation was computed, printed and discarded for months. So the verdict
    goes into the plate's `meta.json` and into the bundle the drag tool loads, which means a
    plate that failed can be shown as failed instead of being offered for hand alignment as
    though it were as good as the rest.
    """
    # "no rival was found" comes out of the arithmetic as NaN, which both JSON and JavaScript
    # will happily carry and neither can compare. It means absent, so it is written as null.
    def _plain(v):
        return None if isinstance(v, float) and not math.isfinite(v) else v

    block = {k: _plain(out.get(k)) for k in
             ("status", "reasons", "lead", "consensus", "endorsing", "voting", "agreeing",
              "median_dist_m", "rival_votes", "rival_dist_m", "biggest_cluster",
              "corrected_m")}
    # A correction the check made is only real if it reaches the matrix the tool opens with.
    # The pre-correction matrix is kept alongside so the move stays auditable.
    fixed = out.get("matrix") if out.get("corrected_m") else None

    data_dir = data_dir if data_dir is not None else _data_dir()
    meta_path = data_dir / slug / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["registration"] = block
        if fixed is not None:
            meta.setdefault("uncorrected_guess", dict(meta["pipeline_guess"]))
            meta["pipeline_guess"] = dict(
                meta["pipeline_guess"], matrix=fixed,
                tx=float(fixed[0][2]), ty=float(fixed[1][2]),
                source="consensus-corrected")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    js_path = data_dir / "align_data.js"
    if not js_path.exists():
        return
    try:
        bundle = json.loads(js_path.read_text().split("=", 1)[1].rstrip().rstrip(";"))
    except Exception as exc:                       # a half-written bundle is not worth losing
        print(f"could not read align_data.js to record the verdict ({exc})", flush=True)
        return
    entry = bundle.get(slug)
    if entry is None:
        return
    # A bundle entry is {images, meta}; the matrix the tool opens with is `meta.pipeline_guess`,
    # so that is the only place a correction has to land.
    meta_entry = entry.setdefault("meta", {})
    meta_entry["registration"] = block
    if fixed is not None and isinstance(meta_entry.get("pipeline_guess"), dict):
        meta_entry.setdefault("uncorrected_guess", dict(meta_entry["pipeline_guess"]))
        meta_entry["pipeline_guess"] = dict(
            meta_entry["pipeline_guess"], matrix=fixed,
            tx=float(fixed[0][2]), ty=float(fixed[1][2]),
            source="consensus-corrected")
    js_path.write_text("window.ALIGN_DATA = " + json.dumps(bundle) + ";\n")
