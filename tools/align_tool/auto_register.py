"""Register every plate we have, and say which registrations can be believed.

`export_align_data.py` already places a plate end to end: `locate` solves the centre, the
span and -- since the rotation was wired through -- the angle, the OSM window is fetched
around that answer, and `refine_guess` corrects the last few pixels. What it does not do is
say whether the result is right. Its gates are gates on the *refinement*: they decide whether
the correction is trustworthy enough to apply, not whether the placement underneath it is the
correct part of the city. A plate can sail through them while sitting a kilometre away, which
is exactly what the Paris miniature does.

Up to now that gap was filled by the hand alignments. Where ground truth existed we knew; where
it did not we guessed, and the guess was reported with the same confidence as the knowledge.
That does not survive contact with a new pack, so this module supplies the missing half:

  discover_packs()      every plate on disk, including ones nobody has listed yet
  register()            run the existing export over them
  crop_consensus()      an independent check that needs no ground truth
  verdict()             pass, review or fail, with the reason attached

The check is the part worth explaining. Correlation reports how well two rasters agree at the
position it likes best, which says nothing about whether a different position would have done
as well -- and in a uniformly dense city, one usually does. So instead of asking the whole map
one question, this asks separate pieces of it the same question and sees whether they give the
same answer. The OSM window is cut into tiles and each tile is correlated against the placed
plate on its own. A placement that is genuinely right puts every tile back where it came from.
A placement that is a saturation artifact has each tile drifting somewhere different, because
there was never a real correspondence holding them together -- only an average density that
looks alike everywhere.

That is a much harder test to pass by accident than a correlation peak, and it does not care
whether the plate has water, which is the property the four miniatures needed.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import export_align_data as ex  # noqa: E402
import locate  # noqa: E402
import numpy as np
import refine_guess  # noqa: E402

DATA = ex.DATA


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
# Discovery
# --------------------------------------------------------------------------

def _region_from_folder(folder: str) -> str:
    """A guess at what city a pack folder is for, from its name alone.

    Vendors name folders for humans, not for geocoders: "Barcelona,_Spain_-_S,_M,_L,_&_XL",
    "Philadelphia  PA - L   XL", "Denver Colorado 3D Miniature - 7029832". What survives all
    three is the front of the name, up to the point where the pack starts describing itself
    rather than the place. Everything from the first size list, catalogue number, or the word
    "miniature" onwards is dropped, and the separators are normalised back to commas.

    This is a starting point for a geocoder, not an answer. A pack whose folder name does not
    contain its city cannot be placed from its folder name, and `discover_packs` reports the
    guess so that a wrong one is visible before it is used.
    """
    s = folder.replace("_", " ")
    s = re.split(r"\b(?:3D\s+)?[Mm]iniature\b|\s-\s|\s+-\s+", s)[0]
    s = re.sub(r"\b[SMLX]{1,2}L?\b(?:\s*[,&]\s*)?", " ", s)     # S, M, L, XL size lists
    s = re.sub(r"\b\d{4,}\b", " ", s)                            # catalogue numbers
    s = re.sub(r"[,&]+", ",", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,-")
    # "Philadelphia  PA" collapses to "Philadelphia PA"; a bare state or country code reads
    # better to a geocoder with a comma in front of it.
    s = re.sub(r"^(.*?[^,\s])\s+([A-Z]{2})$", r"\1, \2", s)
    return s


def discover_packs() -> list[dict]:
    """Every plate directory under the pack roots, listed whether or not anyone declared it.

    `CITIES` is a hand-maintained list, so a pack dropped into the collection is invisible to
    the export until somebody edits the file. This walks the roots instead and returns both:
    the packs that are declared, with the region and span rules they were declared with, and
    the packs that are not, with a region guessed from the folder name and a note saying so.
    """
    declared = {folder: name for name, (_r, folder, _t) in ex.CITIES.items()}
    out = []
    for root in ex.PACK_ROOTS:
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            name = declared.get(d.name)
            if name is not None:
                region = ex.CITIES[name][0]
                known = True
            else:
                name = d.name
                region = _region_from_folder(d.name)
                known = False
            stl = ex.find_stl(d.name)
            out.append({
                "name": name,
                "folder": d.name,
                "root": str(root),
                "region": region,
                "declared": known,
                "stl": None if stl is None else str(stl),
                "water_stl": None if ex.find_water_stl(d.name) is None
                             else str(ex.find_water_stl(d.name)),
                "plate_key": ex.SLUGS.get(name),
                "slug": ex.SLUGS.get(name) or ex.slug(region),
            })
    return out


# --------------------------------------------------------------------------
# The ground-truth-free check
# --------------------------------------------------------------------------

def _place(stl_raw: np.ndarray, matrix, res: int):
    """The plate as the export ships it: warped into the OSM frame by the shipped matrix."""
    import cv2
    M = np.asarray(matrix, dtype=np.float64)
    height = refine_guess.height_channel(stl_raw)
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
    osm = refine_guess.height_channel(osm_raw)
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

            surface = refine_guess.ncc_surface(placed, tile, support, ffts)
            peaks = locate.find_peaks(surface, separation=max(8, min(ystep, xstep) // 4),
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


def score_export(slug: str, data_dir: Path = DATA, *,
                 fix: bool = True) -> dict | None:
    """Score a plate the export has already written out."""
    d = data_dir / slug
    meta_path = d / "meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    stl_raw = np.load(d / "stl_heightmap.npy")
    osm_raw = np.load(d / "osm_buildings.npy")
    cell = float(meta["cell_size_m"])
    span = meta.get("refined_span_m")
    M0 = meta["pipeline_guess"]["matrix"]
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


# A plate's height scale, for a pack nobody has measured. `tallest_m` sets how the STL's z
# axis is read in metres, which the renderer uses and registration does not: `height_channel`
# normalises to unit maximum before anything is correlated, so the placement a discovered
# pack gets is the placement it would have got with the right number. It is a display default,
# not a guess that can move a plate.
DEFAULT_TALLEST_M = 200.0


def adopt(packs: list[dict]) -> list[str]:
    """Make undeclared packs exportable, and return the names that were adopted.

    `export_align_data.main` walks `CITIES`, so a pack that nobody has added to that dict is
    invisible to it however plainly it sits on disk. Rather than duplicate the export for
    discovered packs, the discovered pack is written into `CITIES` for the life of the
    process, with the region guessed from its folder name. That is the whole difference
    between the two kinds of pack: everything downstream -- the solve, the window, the
    refinement, the check -- is the same code either way.

    The entry is deliberately not written back to the source file. A guessed region is a
    starting point, and it should be confirmed by the plate registering against it before
    anyone commits it.
    """
    adopted = []
    for pack in packs:
        if pack["declared"] or pack["stl"] is None:
            continue
        ex.CITIES[pack["name"]] = (pack["region"], pack["folder"], DEFAULT_TALLEST_M)
        adopted.append(pack["name"])
    return adopted


def _record(slug: str, out: dict, data_dir: Path = DATA) -> None:
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


def register(names: list[str] | None = None, *, export: bool = True,
             adopt_undeclared: bool = True, fix: bool = True,
             data_dir: Path = DATA) -> list[dict]:
    """Place the named plates and score every placement.

    With `export=False` nothing is fetched or solved and the plates already on disk are
    scored as they stand, which is what a re-check after a threshold change wants.
    """
    packs = {p["name"]: p for p in discover_packs()}
    if adopt_undeclared:
        taken = adopt([p for p in packs.values()
                       if names is None or p["name"] in names])
        for name in taken:
            print(f"{name}: not declared in CITIES; registering against the guessed region "
                  f"{packs[name]['region']!r}", flush=True)
    if export:
        ex.main(names)

    wanted = names or list(packs)
    results = []
    for name in wanted:
        pack = packs.get(name)
        slug = pack["slug"] if pack else ex.slug(name)
        scored = score_export(slug, data_dir, fix=fix)
        if scored is None:
            results.append({"slug": slug, "city": name, "status": "missing",
                            "reasons": ["no export on disk"]})
        else:
            if pack is not None and not pack["declared"]:
                scored["reasons"] = list(scored["reasons"]) + [
                    f"region {pack['region']!r} was guessed from the folder name"]
            _record(slug, scored, data_dir)
            results.append(scored)
    return results


def format_table(results: list[dict]) -> str:
    mark = {"pass": "PASS", "review": "REVIEW", "fail": "FAIL", "missing": "MISSING"}
    lines = [f"{'city':<26s} {'verdict':<8s} {'lead':>6s} {'tiles':>7s} {'fixed m':>7s} "
             f"{'rot':>7s} {'refine r':>9s}"]
    for r in sorted(results, key=lambda r: (r["status"] != "fail", r["status"] != "review",
                                            r.get("city", ""))):
        tiles = (f"{r.get('endorsing', 0)}/{r.get('voting', 0)}"
                 if r["status"] != "missing" else "-")
        lead = r.get("lead")
        miss = r.get("corrected_m")
        rot = r.get("rot_deg")
        rr = r.get("refine_r")
        lines.append(f"{r.get('city', r['slug']):<26s} {mark[r['status']]:<8s} "
                     f"{'' if lead is None else f'{lead:6.2f}'} {tiles:>7s} "
                     f"{'' if miss is None else f'{miss:7.0f}'} "
                     f"{'' if rot is None else f'{rot:+7.2f}'} "
                     f"{'' if rr is None else f'{rr:9.3f}'}")
        for why in r.get("reasons", []):
            lines.append(f"{'':<26s}   {why}")
    return "\n".join(lines)


USAGE = """auto_register.py [options] [city ...]

Register every plate on disk and report which placements can be believed.
With no city names, all of them.

  --discover     list every pack found, declared or not, and stop
  --score-only   do not export; score the plates already on disk
  --no-fix       report a bad placement without moving it onto the
                 position the tiles agree on
  --no-adopt     ignore packs that are not declared in CITIES

Exit status is 1 if any plate failed or is missing an export."""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if {"-h", "--help"} & set(argv):
        print(USAGE)
        return 0
    do_export = "--score-only" not in argv
    argv = [a for a in argv if a != "--score-only"]

    if "--discover" in argv:
        for p in discover_packs():
            flag = "" if p["declared"] else "   NOT DECLARED, region guessed"
            water = "water" if p["water_stl"] else "no water"
            print(f"{p['name']:<28s} {p['region']:<34s} {water:<9s} {p['slug']}{flag}")
        return 0

    adopt_undeclared = "--no-adopt" not in argv
    argv = [a for a in argv if a != "--no-adopt"]
    fix = "--no-fix" not in argv
    argv = [a for a in argv if a != "--no-fix"]
    results = register(argv or None, export=do_export,
                       adopt_undeclared=adopt_undeclared, fix=fix)
    print(format_table(results))
    bad = [r for r in results if r["status"] in ("fail", "missing")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
