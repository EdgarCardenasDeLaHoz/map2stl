"""Locate a plate from its buildings, for the plates that have no water to locate them by.

`locate.solve_center` finds a plate by sliding its water cut-out over an OSM water raster.
That works because water is sparse and shaped unlike anything else in a city, and it is not
available at all for a plate whose pack ships no water layer -- Philadelphia, and the Boston,
Denver and Paris miniatures, whose `Water_v2.stl` is a flat backing slab rather than a cut
shape. Those plates fall through to the geocoder, which returns a city polygon's centroid;
on Philadelphia that lands 5.5 km from any skyline.

Buildings are the only cue every plate carries. Used the way the water solver uses water they
do not work: a whole-plate building correlation over Philadelphia peaks at z 3.1 against the
z 8.0 the water solver demands, because the strongest match for "a lot of buildings" is
wherever the city happens to be densest. Two changes make them usable, and they are
independent of each other.

**Normalise locally.** Plain cross-correlation rewards density -- a template laid over a
dense district scores well whether or not the pattern matches, because nothing divides out
how much building is there. Normalised cross-correlation divides by the standard deviation
of the window under the template's own footprint, so the score measures agreement rather
than quantity. Measured over the eight located cities this roughly doubles the peak: Paris
7.1 to 17.3, Bilbao 6.7 to 12.7, Valencia 6.9 to 12.8. It also removes the catastrophic
tail -- a plate turned five degrees sends plain correlation 113 px wrong on Barcelona and
161 px wrong on Paris, where the normalised surface stays within 11 px.

**Vote with pieces instead of asking once.** A whole plate gives one answer and one
confidence number. The same plate cut into overlapping crops gives a dozen answers, each
found independently, and confidence becomes how many of them agree -- a far harder statistic
to fake than the height of a single peak. Agreement also carries information a single peak
cannot:

* Crops sit at known offsets inside the plate, so if they agree on a centre while the
  assumed span is wrong they disagree radially, outer crops landing too far out or too far
  in. Sweeping span and keeping the one with the most agreement measures the span rather
  than assuming it, which matters: `PLATE_COVERAGE_M` assumes 2000 m for every plate and
  Philadelphia's covers about 1560.
* A rotated plate displaces each crop perpendicular to its offset from the centre, by an
  amount proportional to how far out it sits. A translation cannot imitate that signature,
  so least squares over the crop displacements recovers the angle -- the degree of freedom
  nothing else in the pipeline searches, and which the stress tests showed destroys a fit at
  two degrees with every gate still reading green.

Crops do not survive a large rotation any better than a whole plate does; past a few degrees
a turned crop simply stops matching. What they provide is a score worth searching on.
Agreement falls from sixteen of sixteen to two or three the moment the angle is wrong, so a
coarse sweep has an unambiguous maximum, and the residual rotation fit then closes the gap
between the coarse steps. Against plates turned by known angles the sweep recovers 0, +2,
+5, +10 and -7 degrees to within 0.32 degrees worst case, with position and agreement back
at their upright values in every case.
"""
from __future__ import annotations

import math

import locate as L
import numpy as np

from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

# Every building, without the tag filtering the semantic masks apply. A plate carries every
# roof its region has, so narrowing the query to a tag family would compare a complete plate
# against a partial city.
BUILDING_SELECTOR = "[building]"

# A crop has to be small enough that a dozen fit across the plate and large enough that its
# street pattern is distinctive. A third of a 2 km plate is about 660 m -- a few city blocks,
# enough structure to match on, and small enough that neighbours share only their overlap.
CROP_FRAC = 0.34
CROP_STRIDE = 0.22

# A crop with almost nothing in it correlates with everything. Below this coverage a crop is
# dropped before it can vote.
MIN_CROP_FILL = 0.06

# Two crops agree when the plate centres they imply sit within this many pixels. Loose enough
# to absorb a crop landing a cell off, tight enough that agreement across a thousand-pixel
# window is not luck.
AGREE_PX = 6.0


def agree_px_for(plate_px: int, span_step: float, rot_step_deg: float) -> float:
    """The agreement tolerance for a search stepping `span_step` and `rot_step_deg`.

    `AGREE_PX` covers a crop landing a cell or so off.  What else moves a crop is that the
    span and rotation on trial are up to half a step from the ones that fit, and neither
    error moves a crop about its own centre: it swings about the plate's, so the displacement
    grows with the lever arm.  The outermost crops sit `(1 - CROP_FRAC) / 2` of a plate width
    from the centre along each axis, so the corner ones reach `sqrt(2)` times that.

    A span half a step long stretches that arm by the same fraction; a rotation half a step
    off swings it through that angle in radians.  Both are added, because the search is a
    product of the two and the worst crop is off in both at once.
    """
    reach = (float(plate_px) * (1.0 - CROP_FRAC) / 2.0) * math.sqrt(2.0)
    return AGREE_PX + reach * (span_step / 2.0 + math.radians(rot_step_deg) / 2.0)


def _step_of(values) -> float:
    """The largest gap between neighbouring values in a sorted sweep, or zero if there is one."""
    ordered = sorted(float(v) for v in values)
    if len(ordered) < 2:
        return 0.0
    return max(b - a for a, b in zip(ordered, ordered[1:]))

# Peaks taken from each crop. The right answer is not always a crop's first choice, and a
# crop holding it in second place should still get to vote for it.
PEAKS_PER_CROP = 6

# Rotations tried once the plate has been found, in degrees, relative to the wide phase's
# answer. Two degrees is the step because the residual fit closes anything finer -- a plate
# turned five degrees is picked up by the +4 candidate and read as +5.01 -- and plus or minus
# twelve is ample around an angle the wide phase has already read to within five.
ROTATIONS = tuple(float(d) for d in range(-12, 13, 2))

# Rotations tried while the plate is still being found. This is the whole circle, because the
# packs are not axis aligned and there is no reason they should be: the Paris miniature is
# turned 44 degrees, and with the search stopping at 12 the right answer was never a candidate
# at all. It scored 1 to 4 crops of 16 at every span, every angle in range, both mirrors, five
# different cues and windows out to 16 km -- the flat noise of a space that does not contain
# the answer -- and 7 of 9 the moment the plate was allowed to turn far enough.
#
# Ten degrees is the step because the vote falls off over about five: the plate reads 11 crops
# of 16 at its true angle, 8 and 9 two degrees to either side, and 3 to 4 by eight degrees
# out. A ten degree grid is never worse than five degrees from the answer, which the wide
# phase's coarse cell blurs enough to survive -- it found Paris 23 m from truth from four
# degrees away -- and the narrow phase re-reads the angle properly afterwards.
WIDE_ROTATIONS = tuple(float(d) for d in range(-180, 180, 10))

# The second rotation pass: how fine it steps, how many of the first pass's angles get one,
# and how far apart those angles have to be to count as different hypotheses.
#
# Ten degrees finds a plate but does not read it. Denver's grid runs about 45 degrees off
# north; the ten degree pass could manage only 5 agreeing crops of 16, one short of the gate,
# where the same scan at two degrees returns 9. The vote falls off over about five degrees, so
# a coarse grid that lands halfway between rungs loses half its crops.
#
# Four angles rather than one because a square street grid reads the same after a quarter
# turn: Denver's -130, -40, +50 and +140 are one hypothesis seen four ways, and which of them
# the coarse pass happens to rank first is close to arbitrary. Refining all four and letting
# them compete at full resolution settles it on the buildings that are not on the grid.
REFINE_ROTATION_DEG = 2.0
REFINE_ANGLES = 4
REFINE_SEPARATION_DEG = 20.0


def _angle_gap(a: float, b: float) -> float:
    """Smallest turn between two angles in degrees, going whichever way round is shorter."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)

# Acceptance. Agreement is the gate the water solver's peak height cannot be: it reads 16/16
# when the answer is right and collapses to 2-7/16 the moment it is not, where a single peak's
# z drifts down smoothly and gives no clean place to cut. The mean correlation is a second,
# weaker check against a run where only a handful of crops survive the fill test.
MIN_AGREE_FRAC = 0.6
MIN_CROPS = 6
MIN_MEAN_R = 0.25

# How far below the typical local variance a position may sit before its correlation is
# treated as undefined rather than computed.  See `ncc_surface`.
VAR_FLOOR_FRAC = 1e-4

# Window searched, as a multiple of the plate's nominal span, in each of the two phases.
#
# The wide phase matches the water solver's 7.0 for the same reason the water solver was
# widened to it: the plate has to fit inside the window, so its centre can only move
# (margin - 1) / 2 plate widths before it hangs over the edge, and a 3x margin gives a 2 km
# plate only 2 km of reach against geocoder seeds that miss by 3.8 km on Lisbon and Miami.
# Both of those, and Barcelona, were railed against the window edge at 3x -- not mis-ranked
# but out of reach, exactly the defect this module's docstring describes for water.
#
# The narrow phase then re-centres on what the wide phase found, where 2.5 is ample: the
# wide answer has never been more than a few of its own coarse cells out.
SEARCH_MARGIN = 7.0

# How many of the wide phase's placements the narrow phase is allowed to try.  Each one past
# the first costs an Overpass request, and the first is right for every pack that was passing
# before this existed, so the ceiling is only reached by a plate that was failing anyway.
WIDE_CANDIDATES = 4

# Two placements are the same place when their centres sit within this fraction of a plate
# span of each other.  The wide phase reports its best result at every angle, so a real answer
# appears several times over at neighbouring rotations; without this the candidate list would
# be one answer repeated rather than the alternatives it is meant to hold.
CANDIDATE_APART_FRAC = 0.5

# The share of surviving crops at which a placed candidate is taken as the answer without the
# rest of the list being tried.  Every candidate past this one costs an Overpass request, and
# every pack that was passing before the list existed clears this on its first candidate.
DECISIVE_AGREE_FRAC = 0.8
FINE_MARGIN = 2.5

# Ground resolution of each phase, in metres per pixel. The wide phase runs coarse because
# `search_grid_for` caps the grid at 2048 and a 14 km window at 8 m would not fit -- and
# because it does not need the resolution. Halving and quartering the bench rasters leaves
# every city at sixteen crops out of sixteen, with the mean correlation slightly *up*: a
# building is a blob at any of these scales and the street pattern between them survives.
#
# The coarse cell also does positive work, which is why it should not be tuned finer for
# its own sake. Prague failed 782 m from truth with the truth already inside its window, so
# reach cannot explain it; at an 8 m cell the surface keeps enough local structure for the
# descent to settle on a wrong optimum and buy agreement with a +9.50 degree rotation that
# does not exist. Smoothed to 16 m the same plate solves 16/16 at +0.00 and 18 m. The wide
# phase is meant to hand the narrow phase a basin, not a point.
#
# The narrow phase then runs at the pipeline's usual cell to place the plate precisely.
#
# The coarse cell is a fraction of the plate's nominal span rather than a fixed number of
# metres, because everything the paragraphs above claim for it is a claim about the ratio
# between the cell and what the plate contains, not about sixteen metres. Sixteen metres is a
# 2 km plate divided by 125, and every pack whose coverage reads as 2 km -- which is all of
# them at the time of writing -- gets exactly the cell it got before.
#
# A monument does not. The Alhambra covers 592 m and its curtain wall is about 5 m thick, so
# at a fixed 16 m the wall is a third of a pixel and the plate is 73 pixels across; the wide
# phase settled 1.7 km from the hill on five crops of ten, while the same plate probed at
# 3.9 m found nine of eleven on it. The fraction gives it 4.6 m and 128 pixels.
#
# The ratio also holds the window at a constant size in pixels, since the margin multiplies
# the span and the cell divides it, so `_grid_for`'s cap -- the reason the wide phase cannot
# simply run at the fine cell -- is respected by construction rather than by the choice of
# any particular constant.
COARSE_CELL_PER_SPAN = 1.0 / 125.0

# A floor, so that a plate small enough to make the fraction meaningless cannot ask for a
# cell finer than the narrow phase's own. Nothing in the collection reaches it: it binds
# below about 375 m of coverage.
COARSE_CELL_FLOOR_M = 3.0

FINE_CELL_M = L.TARGET_CELL_M


def coarse_cell_for(span_m: float) -> float:
    """Metres per pixel for the wide phase over a plate of `span_m` nominal coverage."""
    return max(COARSE_CELL_FLOOR_M, float(span_m) * COARSE_CELL_PER_SPAN)

# Span factors for a pack whose ground coverage is genuinely unknown. `PLATE_COVERAGE_M`
# assumes 2000 m because the micropolitan vendor's own instructions say so, and the sweep
# `locate` uses only reaches 1560 to 2400 m around that. The miniatures are a different
# vendor entirely -- 170.5 by 119.5 mm plates built in Blender, with no coverage stated
# anywhere -- so for those the figure has to be measured rather than nudged. Geometric
# steps, because what matters is the ratio: a span 10% out spoils the vote by the same
# amount whether the plate is one kilometre across or five.
WIDE_SPAN_FACTORS = tuple(round(0.5 * (1.12 ** i), 4) for i in range(17))  # 0.50 .. 3.07

# The wide phase's window has to hold the largest plate the sweep can propose and still
# leave it somewhere to sit. At the 7x margin above that is 14 km against a 6.1 km plate,
# which leaves just under 4 km of reach -- comparable to what a 2 km plate gets, and more
# than any measured seed miss.

# Plate raster resolution, and the erosion width used to find the ground under the buildings.
PLATE_RASTER = 512


def _grid_for(side_m: float, cell_m: float) -> int:
    """Pixels across a window of `side_m` metres at roughly `cell_m` per pixel.

    `locate.search_grid_for` fixes the cell at `TARGET_CELL_M`, which the wide phase cannot
    use: a 14 km window at 8 m is 1750 pixels before rounding and the FFTs that follow are
    charged for every one of them, for resolution the vote does not need.
    """
    grid = int(round(side_m / cell_m / 64.0)) * 64
    return max(512, min(L.MAX_SEARCH_GRID, grid))


def plate_buildings(solid_stl, span_m: float, resolution: int = PLATE_RASTER) -> np.ndarray:
    """The plate's building footprints, as a mask in the plate's own frame.

    The plate render is absolute model z -- terrain and structures together -- so a fixed
    cut-off follows the hillside rather than the buildings on any plate with relief. What is
    wanted is the height each cell rises above its local ground, which is the same physical
    quantity the OSM building raster holds, and `terrain_residual` estimates it by a
    morphological opening sized in metres.

    The split between ground and building is three-class Otsu over a residual clipped at its
    85th percentile, matching `export_align_data._plate_render` so that a plate located here
    is segmented the same way it will be exported. The clip is what makes Otsu safe on a
    skyline: the rule minimises within-class variance, so a long tail of towers buys a large
    reduction by claiming a class of its own and drags both boundaries up behind it.

    The percentile is 85 rather than the 95 that first fixed Miami because the same failure
    returns on plates whose tail is longer still, and it is the vote that shows it: the
    Philadelphia miniature cuts at 1.90 where the other plates cut between 0.54 and 0.85, and
    solves 5.2 km from City Hall. At p85 it cuts at 0.77 and solves 110 m out, and p80 gives
    the identical answer, so this is a plateau rather than a tuned edge. Nothing that already
    solved moves: micropolitan Philadelphia holds its position exactly while its agreement
    rises from 9 of 16 to 13, and the Boston miniature stays 10 m out.
    """
    from numpy2stl.registration.align.segmentation import (
        _adaptive_residual_threshold,
        terrain_residual,
    )

    hm = L._heightmap(solid_stl, resolution)
    resid, valid = terrain_residual(np.asarray(hm, dtype=np.float64),
                                    cell_size_m=float(span_m) / resolution)
    if not valid.any():
        return (np.isfinite(hm) & (hm > 0)).astype(np.float32)
    thr, _ = _adaptive_residual_threshold(resid[valid], "multiotsu_p85")
    return (valid & (resid > thr)).astype(np.float32)


def turn(mask: np.ndarray, deg: float) -> np.ndarray:
    """The mask rotated about its own centre, counter-clockwise by `deg`."""
    if not deg:
        return mask
    import cv2
    h, w = mask.shape
    m = cv2.getRotationMatrix2D((w / 2.0 - 0.5, h / 2.0 - 0.5), deg, 1.0)
    return cv2.warpAffine(mask, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)


# --------------------------------------------------------------------------
# Normalised correlation
# --------------------------------------------------------------------------

def _corr(fixed_fft, shape, moving: np.ndarray) -> np.ndarray:
    """Raw cross-correlation, rolled so zero shift lands at the frame centre.

    Unlike `locate.correlate_with` this does not normalise `moving`, because the
    normalisation here is done afterwards and against the window, not the template.
    """
    m = np.fft.rfft2(moving)
    corr = np.fft.irfft2(fixed_fft * np.conj(m), s=shape)
    h, w = corr.shape
    return np.roll(corr, (h // 2, w // 2), axis=(0, 1))


def local_stats(fixed_fft, fixed_sq_fft, shape, grid: int, origin: int, tile: int):
    """Local sums of the window and of its square, for a crop at the plate's near corner.

    Shifting a template by a whole number of pixels shifts its correlation surface by the
    same amount in the opposite direction, so one pair of transforms serves every crop of a
    given size and `shift_to` rolls these to whichever crop is being scored. That takes the
    cost of a span from three transforms per crop to one, which is the difference between
    this being usable over a wide window and not.
    """
    support = np.zeros((grid, grid), dtype=np.float32)
    support[origin:origin + tile, origin:origin + tile] = 1.0
    return (_corr(fixed_fft, shape, support), _corr(fixed_sq_fft, shape, support))


def shift_to(surface: np.ndarray, ty: int, tx: int) -> np.ndarray:
    """The same surface as it would be for a crop `ty`, `tx` pixels further in."""
    return np.roll(surface, (-ty, -tx), axis=(0, 1))


def ncc_surface(fixed_fft, shape, template: np.ndarray, n: float, fs, fs2):
    """Normalised cross-correlation of `template` against the window behind `fixed_fft`.

    `template` is a full-frame array holding the crop's contents where the crop sits when the
    plate is centred. Keeping it in the frame rather than tracking offsets separately means
    every crop's surface comes out in one shared coordinate system, where a peak is directly
    the shift of the plate's centre and votes can be compared without further bookkeeping.

    The normalisation is the textbook one: subtract the local mean of the window under the
    template's footprint and divide by the local standard deviation there. `fs` and `fs2` are
    those local sums, already rolled to this crop -- they depend only on where the crop's
    rectangle sits, never on what is inside it.
    """
    t_sum = float(template.sum())
    t_var = float((template * template).sum()) - t_sum * t_sum / n
    if t_var <= 1e-9:
        return None

    num = _corr(fixed_fft, shape, template) - fs * (t_sum / n)
    var = fs2 - fs * fs / n
    np.maximum(var, 0.0, out=var)
    # `var` is a difference of two large, nearly equal sums, so where the window is locally
    # flat what is left is cancellation error rather than variance.  An absolute floor does
    # not recognise that; a floor relative to the surface's own typical variance does.  Real
    # peaks sit above the median, so this is far too low to suppress one.
    typical = float(np.median(var[var > 0.0])) if (var > 0.0).any() else 0.0
    out = np.zeros_like(num)
    good = (var > max(VAR_FLOOR_FRAC * typical, 1e-9)) & (var * t_var > 0.0)
    out[good] = num[good] / np.sqrt(var[good] * t_var)
    # A cosine cannot leave [-1, 1]; anything past it is arithmetic, not agreement.
    np.clip(out, -1.0, 1.0, out=out)
    return out


# --------------------------------------------------------------------------
# Crops and their vote
# --------------------------------------------------------------------------

def crops_of(plate_px: np.ndarray, grid: int):
    """Overlapping square crops of a plate, each placed in a centred `grid` frame.

    Returns each crop's contents, the whole-pixel position of its support rectangle, and the
    offset of its own centre from the plate's centre -- the offset being the lever arm a
    rotation acts through, and so what makes the rotation fit possible at all.
    """
    tile = max(8, int(round(plate_px.shape[0] * CROP_FRAC)))
    stride = max(4, int(round(plate_px.shape[0] * CROP_STRIDE)))
    origin = (grid - plate_px.shape[0]) // 2
    half = plate_px.shape[0] / 2.0
    out = []
    for ty in range(0, plate_px.shape[0] - tile + 1, stride):
        for tx in range(0, plate_px.shape[1] - tile + 1, stride):
            piece = plate_px[ty:ty + tile, tx:tx + tile]
            if float(piece.mean()) < MIN_CROP_FILL:
                continue
            template = np.zeros((grid, grid), dtype=np.float32)
            template[origin + ty:origin + ty + tile, origin + tx:origin + tx + tile] = piece
            out.append({"template": template, "ty": ty, "tx": tx,
                        "oy": ty + tile / 2.0 - half,
                        "ox": tx + tile / 2.0 - half})
    return out, tile, origin


def _ground_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Roughly how far apart two coordinates are, in metres."""
    return math.hypot((lat_a - lat_b) * M_PER_DEG_LAT,
                      (lon_a - lon_b) * m_per_deg_lon((lat_a + lat_b) / 2.0))


def vote(votes: list[dict], agree_px: float = AGREE_PX) -> dict | None:
    """The position the most crops agree on.

    Every peak from every crop is a candidate. A candidate's support is the number of
    *distinct* crops holding a peak near it -- counting crops rather than peaks is what stops
    one crop with six peaks strung along a ridge from outvoting six crops that genuinely
    agree with each other.
    """
    best = None
    for cand in votes:
        near: dict[int, dict] = {}
        for v in votes:
            if math.hypot(v["dy"] - cand["dy"], v["dx"] - cand["dx"]) <= agree_px:
                # One vote per crop: its best-scoring peak in this neighbourhood.
                if v["crop"] not in near or v["r"] > near[v["crop"]]["r"]:
                    near[v["crop"]] = v
        members = list(near.values())
        score = (len(members), sum(m["r"] for m in members))
        if best is None or score > best["score"]:
            best = {"score": score, "members": members,
                    "dy": float(np.mean([m["dy"] for m in members])),
                    "dx": float(np.mean([m["dx"] for m in members]))}
    return best


def fit_rotation(members: list[dict]) -> float | None:
    """The rotation implied by where the agreeing crops actually landed.

    A crop at offset r from the plate's centre moves to R(theta)r when the plate turns, so
    its displacement away from the consensus centre is perpendicular to r and proportional to
    |r|. Least squares over the crops recovers theta directly, which is what lets a two-degree
    sweep report an angle to a tenth of a degree.
    """
    if len(members) < 3:
        return None
    num = den = 0.0
    for m in members:
        ry, rx = m["oy"], m["ox"]
        dy, dx = m["dy"] - m["cons_dy"], m["dx"] - m["cons_dx"]
        # Cross and dot products against the lever arm: the cross term is the rotation.
        num += rx * dy - ry * dx
        den += rx * rx + ry * ry
    if den <= 0:
        return None
    return math.degrees(math.atan2(num, den))


def _run_vote(built_ffts, shape, grid: int, plate_px: np.ndarray,
              stats_cache: dict | None = None,
              agree_px: float = AGREE_PX) -> dict | None:
    """One span, one rotation: cut the plate up, score every crop, and count the votes.

    The local statistics depend only on the size of the crop rectangle, never on what is
    inside it, so they are shared across every rotation tried at a given span.
    """
    fixed_fft, fixed_sq_fft = built_ffts
    pieces, tile, origin = crops_of(plate_px, grid)
    if len(pieces) < MIN_CROPS:
        return None
    key = (grid, origin, tile)
    if stats_cache is None or key not in stats_cache:
        stats = local_stats(fixed_fft, fixed_sq_fft, shape, grid, origin, tile)
        if stats_cache is not None:
            stats_cache[key] = stats
    else:
        stats = stats_cache[key]
    fs_ref, fs2_ref = stats
    separation = max(4, int(L.PEAK_SEPARATION * tile))
    n = float(tile * tile)
    votes = []
    for i, piece in enumerate(pieces):
        surf = ncc_surface(fixed_fft, shape, piece["template"], n,
                           shift_to(fs_ref, piece["ty"], piece["tx"]),
                           shift_to(fs2_ref, piece["ty"], piece["tx"]))
        if surf is None:
            continue
        for peak in L.find_peaks(surf, separation, count=PEAKS_PER_CROP):
            votes.append({"crop": i, "oy": piece["oy"], "ox": piece["ox"],
                          "dy": peak["y"] - grid // 2, "dx": peak["x"] - grid // 2,
                          "r": float(surf[peak["y"], peak["x"]])})
    if not votes:
        return None
    win = vote(votes, agree_px)
    if win is None:
        return None
    win["crops"] = len(pieces)
    win["tile"] = tile
    return win


# --------------------------------------------------------------------------
# The solver
# --------------------------------------------------------------------------

def solve_center(
    region: str,
    solid_stl,
    base_span_m: float,
    seed: tuple[float, float] | None = None,
    search_margin: float = SEARCH_MARGIN,
    target_cell_m: float = FINE_CELL_M,
    rotations=ROTATIONS,
    span_factors=L.SPAN_FACTORS,
    passes: int = 2,
    search: str = "descend",
    angle0: float = 0.0,
    refine_deg: float = 0.0,
    verbose: bool = True,
) -> dict:
    """Find a plate's centre, span and rotation from its buildings alone.

    The return has the same shape as `locate.solve_center`'s so that `resolve_center` can
    treat the two interchangeably, with `agree`, `crops` and `rot_deg` added. Unlike the
    water solver this one reports a rotation, and unlike the water solver its span estimate
    is trustworthy enough to feed back: the crops disagree radially when the span is wrong,
    which is a much sharper signal than the shift in a single correlation peak.
    """
    from numpy2stl.applications.cities import tight_bbox_from_extent

    if seed is None:
        seed = L.seed_center(region)
    side_m = base_span_m * search_margin
    grid = _grid_for(side_m, target_cell_m)
    cell_m = side_m / grid
    bbox = tight_bbox_from_extent(seed[0], seed[1], side_m, margin=1.0)
    N, S, E, W = bbox

    result = {
        "source": "solved-buildings",
        "seed": [float(seed[0]), float(seed[1])],
        "base_span_m": float(base_span_m),
        "cell_size_m": float(cell_m),
        "grid": grid,
        "search_bbox_nsew": [float(v) for v in bbox],
        "ok": False,
    }

    plate_hi = plate_buildings(solid_stl, base_span_m)
    result["plate_building_frac"] = float(plate_hi.mean())
    if result["plate_building_frac"] < MIN_CROP_FILL:
        result["reason"] = (f"plate has almost no buildings "
                            f"({result['plate_building_frac']:.4f})")
        return result

    layer = L._layer("building", BUILDING_SELECTOR, bbox, grid, verbose)
    if layer is None:
        result["reason"] = "osm buildings unavailable"
        return result
    built = layer["filled"].astype(np.float32)
    result["osm_building_frac"] = float(built.mean())
    if result["osm_building_frac"] < MIN_CROP_FILL:
        result["reason"] = f"osm buildings too sparse ({result['osm_building_frac']:.4f})"
        return result
    if verbose:
        print(f"  window {side_m:.0f} m, grid {grid}, cell {cell_m:.2f} m, "
              f"osm fill {built.mean():.3f}", flush=True)

    ffts = (np.fft.rfft2(built), np.fft.rfft2(built * built))
    shape = built.shape
    stats_cache: dict = {}

    # The span ladder is geometric, so its step is a ratio; the rotations are a plain grid.
    # Both are read off the sweeps the caller passed rather than assumed, so a phase that is
    # given a finer sweep is held to a tighter tolerance without being told twice.
    ordered_factors = sorted(float(f) for f in span_factors)
    span_step = max((b / a - 1.0 for a, b in zip(ordered_factors, ordered_factors[1:])),
                    default=0.0)
    rot_step = _step_of(rotations)

    def try_at(factor: float, deg: float, rot_step_deg: float = rot_step) -> dict | None:
        span = base_span_m * factor
        px = int(round(span / cell_m))
        if px < 32 or px > grid:
            return None
        turned = turn(plate_hi, -deg) if deg else plate_hi
        win = _run_vote(ffts, shape, grid, L.resample(turned, px), stats_cache,
                        agree_px_for(px, span_step, rot_step_deg))
        if win is None:
            return None
        win.update(factor=factor, span_m=span, angle=deg)
        return win

    # Span and rotation are searched either over their full product or by coordinate descent
    # down each axis in turn. Descent is much the cheaper of the two and it is right whenever
    # the two axes interact weakly -- a plate at the wrong span still reads its rotation, and
    # a plate at the wrong rotation still reads its span, because each spoils the vote in its
    # own direction.
    #
    # That holds only while one axis is nearly right to begin with. Descent opens with a span
    # pass at zero degrees, so a plate turned 44 degrees is scored as noise at every span, and
    # the rotation pass that follows runs at whatever span the noise happened to favour --
    # 1574 m for Paris, where every angle is noise as well. Neither pass has a gradient to
    # follow and the two together never come near the answer. So the wide phase, which is the
    # one that has to find a plate it knows nothing about, scans the product; the narrow
    # phase, which starts from the wide phase's answer, descends.
    def scan(degrees, best=None, rot_step_deg: float = rot_step):
        per_angle: dict[float, dict] = {}
        for deg in degrees:
            turned = turn(plate_hi, -deg) if deg else plate_hi
            for factor in span_factors:
                span = base_span_m * factor
                px = int(round(span / cell_m))
                if px < 32 or px > grid:
                    continue
                win = _run_vote(ffts, shape, grid, L.resample(turned, px), stats_cache,
                                agree_px_for(px, span_step, rot_step_deg))
                if win is None:
                    continue
                win.update(factor=factor, span_m=span, angle=deg)
                held = per_angle.get(deg)
                if held is None or win["score"] > held["score"]:
                    per_angle[deg] = win
                if best is None or win["score"] > best["score"]:
                    best = win
                    if verbose:
                        print(f"    scan: {best['score'][0]}/{best['crops']} crops at "
                              f"{best['span_m']:.0f} m, {best['angle']:+.1f} deg", flush=True)
        return best, per_angle

    # The angles worth a second, finer pass: the best few that are far enough apart to be
    # different answers rather than two readings of the same one. Each is re-scanned across
    # half the coarse step to either side, which is the whole gap between coarse rungs.
    def refine(best, per_angle):
        ordered = sorted(per_angle.items(), key=lambda kv: kv[1]["score"], reverse=True)
        picks: list[float] = []
        for deg, _win in ordered:
            if len(picks) >= REFINE_ANGLES:
                break
            if all(_angle_gap(deg, p) >= REFINE_SEPARATION_DEG for p in picks):
                picks.append(deg)
        rungs = sorted(per_angle)
        step = min((b - a) for a, b in zip(rungs, rungs[1:]))
        reach = int(step / 2.0 / refine_deg)
        already = set(per_angle)
        around = sorted({round(deg + refine_deg * i, 3)
                         for deg in picks for i in range(-reach, reach + 1)} - already)
        if not around:
            return best
        if verbose:
            print(f"    refining {len(picks)} angles at {refine_deg:.0f} deg "
                  f"({len(around)} more)", flush=True)
        return scan(around, best, refine_deg)[0]

    # `angle0` is where the first span pass runs, before any rotation has been tried. North
    # is the wrong default whenever the caller already knows better: the narrow phase opened
    # Paris at zero degrees, scored 2 crops of 16 at every span in the ladder, and only
    # recovered because the rotation pass that followed happened to rescue it from the wrong
    # span. Starting at the angle the wide phase read makes that a result rather than a
    # reprieve.
    def descend(best):
        for factor in span_factors:
            if best is None or factor != best["factor"]:
                cand = try_at(factor, best["angle"] if best else angle0)
                if cand is not None and (best is None or cand["score"] > best["score"]):
                    best = cand
        if best is None:
            return None
        if verbose:
            print(f"    span pass: {best['score'][0]}/{best['crops']} crops at "
                  f"{best['span_m']:.0f} m, {best['angle']:+.0f} deg", flush=True)
        for deg in rotations:
            if deg == best["angle"]:
                continue
            cand = try_at(best["factor"], deg)
            if cand is not None and cand["score"] > best["score"]:
                best = cand
        if verbose:
            print(f"    rotation pass: {best['score'][0]}/{best['crops']} crops at "
                  f"{best['span_m']:.0f} m, {best['angle']:+.0f} deg", flush=True)
        return best

    # A second round, because the first span pass ran at whatever rotation it started from.
    # It stops there: a third round has never moved the answer on any city tried, and when
    # only one rotation is on offer even the second round has nothing new to try.
    best = None
    per_angle: dict[float, dict] = {}
    if search == "scan":
        best, per_angle = scan(rotations)
        if best is not None and refine_deg > 0.0 and len(per_angle) > 1:
            best = refine(best, per_angle)
    else:
        for _ in range(max(1, passes)):
            best = descend(best)
            if best is None:
                break
    if best is None:
        result["reason"] = "no usable span"
        return result

    def describe(win: dict) -> dict:
        """What a vote says about the plate, in the terms the rest of the pipeline speaks."""
        for m in win["members"]:
            m["cons_dy"], m["cons_dx"] = win["dy"], win["dx"]
        # The residual is what is left after undoing the candidate angle, so the plate's own
        # rotation is the two added together.
        residual = fit_rotation(win["members"])
        agree, strength = win["score"]
        return {
            "lat": float(S + ((grid / 2.0 + win["dy"]) / grid) * (N - S)),
            "lon": float(W + ((grid / 2.0 + win["dx"]) / grid) * (E - W)),
            "span_m": float(win["span_m"]), "span_factor": float(win["factor"]),
            "angle_deg": float(win["angle"]),
            "rot_deg": float(win["angle"] + (residual or 0.0)),
            "rot_residual": None if residual is None else float(residual),
            "agree": int(agree), "crops": int(win["crops"]), "tile": int(win["tile"]),
            "mean_r": float(strength / max(agree, 1)),
        }

    result.update(describe(best))

    # The runners-up, for a caller that has a way of telling a good placement from a
    # plausible one and would rather do the telling itself.  `per_angle` holds the best
    # placement found at each rotation, so it already contains whatever the winner beat;
    # what it does not contain is any notion of which of its entries are the same place.
    alternates: list[dict] = []
    for win in sorted(per_angle.values(), key=lambda w: w["score"], reverse=True):
        cand = describe(win)
        apart = CANDIDATE_APART_FRAC * cand["span_m"]
        if any(_ground_m(cand["lat"], cand["lon"], seen["lat"], seen["lon"]) < apart
               for seen in [result] + alternates):
            continue
        alternates.append(cand)
        if len(alternates) >= WIDE_CANDIDATES - 1:
            break
    result["alternates"] = alternates

    agree, crops, mean_r = result["agree"], result["crops"], result["mean_r"]
    need = max(MIN_CROPS, int(math.ceil(MIN_AGREE_FRAC * crops)))
    if agree < need:
        result["reason"] = f"only {agree} of {crops} crops agree; {need} needed"
        return result
    if mean_r < MIN_MEAN_R:
        result["reason"] = f"agreeing crops correlate weakly (r={mean_r:.3f})"
        return result

    result["ok"] = True
    return result


def solve_plate(
    region: str,
    solid_stl,
    span_m: float,
    span_known: bool = True,
    seed: tuple[float, float] | None = None,
    verbose: bool = True,
) -> dict:
    """Locate a plate in two phases: find it coarsely over a wide window, then place it.

    The two phases want opposite things from the window, which is why one pass cannot serve
    both.  *Finding* the plate needs reach.  The seed is a geocoder centroid and vendors
    frame their plates on whatever they found interesting, so the miss is 3.8 km on Lisbon
    and on Miami, and a window that cannot travel that far does not rank the right answer
    badly -- it never considers it.  *Placing* the plate needs a fine cell, and a window wide
    enough to reach 3.8 km cannot have one, because the grid is capped at 2048 pixels.

    So the wide phase runs at 16 m per pixel over a 7x window, which costs no more than the
    3x window at 8 m did and reaches three times as far.  That resolution is affordable
    because the vote barely notices it: halving and quartering the bench rasters holds every
    city at sixteen crops out of sixteen.  The narrow phase then re-centres on that answer at
    the pipeline's usual cell, and it is the one whose gates decide the result.

    When `span_known` is False the wide phase also sweeps span over a factor of six instead
    of the usual 0.78 to 1.20.  Nothing in the miniature packs states the ground they cover,
    and a wrong span is visible to the vote as radial disagreement between the crops, so the
    figure can be measured rather than assumed.

    The wide phase turns the plate through the whole circle and scans span against rotation
    as a product rather than descending each in turn, because a plate that is wrong on both
    axes at once gives the descent no gradient to follow.  That is what the Paris miniature
    is: 44 degrees off north and 3.25 km across against a nominal 2 km.  The narrow phase
    keeps the descent, since by then both axes are close.
    """
    factors = L.SPAN_FACTORS if span_known else WIDE_SPAN_FACTORS
    coarse = solve_center(
        region, solid_stl, span_m, seed=seed,
        search_margin=SEARCH_MARGIN, target_cell_m=coarse_cell_for(span_m),
        rotations=WIDE_ROTATIONS, span_factors=factors, search="scan",
        refine_deg=REFINE_ROTATION_DEG, verbose=verbose,
    )
    if coarse.get("lat") is None:
        coarse["reason"] = f"wide phase failed: {coarse.get('reason', 'no result')}"
        coarse["phase"] = "wide"
        return coarse
    # The wide phase is judged on agreement alone.  Its span and rotation are read at the
    # coarse cell and the narrow phase re-reads both, so the only thing asked of it is that
    # enough crops pointed at the same place to believe it found the city.
    if coarse["agree"] < MIN_CROPS:
        coarse["reason"] = (f"wide phase inconclusive: only {coarse['agree']} of "
                            f"{coarse['crops']} crops agree")
        coarse["phase"] = "wide"
        return coarse
    if verbose:
        print(f"  wide phase: {coarse['lat']:.5f},{coarse['lon']:.5f} "
              f"({coarse['agree']}/{coarse['crops']} crops, r {coarse['mean_r']:.3f}, "
              f"span {coarse['span_m']:.0f} m)", flush=True)

    # The wide phase's winner is the first thing tried and usually the last, but a plate
    # sparse enough for two placements to score alike is a plate whose winner may be the wrong
    # one, and the narrow phase can tell them apart.  So the candidates are placed in score
    # order and the best of them is the answer.
    #
    # The best, not the first that survives: surviving is common.  The Alhambra's first
    # runner-up is confirmed at seven crops of ten on ground two and a half kilometres from
    # the monument, while the true placement, further down the list, reads nine of ten.  What
    # ranks them is the share of surviving crops that agree rather than the number, since a
    # placement over empty ground kills crops before they can disagree and would otherwise be
    # rewarded for having fewer of them to convince.
    #
    # The runners-up are not made to clear the agreement gate above.  That gate exists because
    # a wide answer nobody re-examines has to be right on its own, and these are re-examined,
    # at a finer cell and against a stricter test, by the very next call.  The Alhambra's true
    # placement carries five crops there, one short, which is what struck it off before the
    # phase that would have confirmed it could look.
    candidates = [coarse] + list(coarse.get("alternates", []))
    fine = None
    placed: tuple | None = None
    for rank, cand in enumerate(candidates[:WIDE_CANDIDATES]):
        if rank and verbose:
            print(f"  trying the wide phase's next placement: "
                  f"{cand['lat']:.5f},{cand['lon']:.5f} "
                  f"({cand['agree']}/{cand['crops']} crops, span "
                  f"{cand['span_m']:.0f} m)", flush=True)
        # The narrow phase searches around the angle the wide phase read, not around north.
        # A plate the wide phase found at 44 degrees is still at 44 degrees, and offering it
        # only -12 to +12 would throw the rotation away along with the answer.
        about = float(cand.get("angle_deg", 0.0))
        attempt = solve_center(
            region, solid_stl, cand["span_m"], seed=(cand["lat"], cand["lon"]),
            search_margin=FINE_MARGIN, target_cell_m=FINE_CELL_M,
            rotations=tuple(about + d for d in ROTATIONS), angle0=about,
            span_factors=L.SPAN_FACTORS, verbose=verbose,
        )
        if fine is None:
            fine = attempt
        if not attempt.get("ok"):
            continue
        share = attempt["agree"] / max(attempt["crops"], 1)
        rank_key = (share, attempt["agree"], attempt["mean_r"])
        if placed is None or rank_key > placed[0]:
            placed = (rank_key, attempt, cand)
        if verbose:
            print(f"    placed at {attempt['lat']:.5f},{attempt['lon']:.5f}: "
                  f"{attempt['agree']}/{attempt['crops']} crops ({share:.2f}), "
                  f"r {attempt['mean_r']:.3f}, span {attempt['span_m']:.0f} m", flush=True)
        if share >= DECISIVE_AGREE_FRAC:
            break
    if placed is not None:
        _, fine, coarse = placed
    # A wide answer that has passed the test above is worth keeping even when the narrow
    # phase cannot run. Overpass answered one request in this pair with a 500 and a placement
    # that had just been found 20 m from truth was dropped for a geocoder centroid three
    # kilometres away, which is a worse answer than the one already in hand. The narrow phase
    # refines; it does not decide whether the plate was found.
    #
    # The wide result is marked accepted on the way out because `solve_center` marks its own
    # `ok` by the stricter of the two gates -- six crops *and* three fifths of them -- while
    # this function holds the wide phase to agreement alone, for the reason given above it.
    # The Philadelphia miniature sits between the two at 9 crops of 16, and asking for the
    # strict flag here is asking a question this function has already answered differently.
    if not fine.get("ok"):
        if verbose:
            print(f"  narrow phase failed ({fine.get('reason')}); "
                  f"keeping the wide answer", flush=True)
        coarse["ok"] = True
        coarse["phase"] = "wide"
        coarse["fine_reason"] = fine.get("reason")
        coarse["nominal_span_m"] = float(span_m)
        return coarse

    fine["phase"] = "fine"
    fine["wide_span_m"] = float(coarse["span_m"])
    fine["wide_agree"] = int(coarse["agree"])
    fine["wide_crops"] = int(coarse["crops"])
    fine["wide_center"] = [float(coarse["lat"]), float(coarse["lon"])]
    fine["nominal_span_m"] = float(span_m)
    return fine
