"""Part of ``city2stl.skyline._pano.elevated`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from .. import footprint_detect as fd
from .._core.types import BuildingRecord
from ..region_types import SkylinePoint
from . import stage_cache as _sc  # not ``sc``: overhead_pose has a local one

logger = logging.getLogger('city2stl.skyline._pano.elevated')


#: Below this waterline-fitted camera height there is no near water under the camera: either
#: a street view, or a drone high over land (its near shore below the frame, seed_6), which
#: :func:`overhead_pose` places from the ground and the building bases instead.
MIN_ELEVATED_H_M = 15.0


#: Above this waterline-fitted height the drone is placed like an overhead one (recorded
#: position, ground heading, height from building bases): the waterline + outline fit
#: overfits a few towers (seed_7, 2026-10-06).
OVERHEAD_FROM_H_M = 130.0


#: Camera height search of the tower-outline refinement, as factors of the waterline height.
HEIGHT_FACTORS = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25)


#: Ground map around the seed: the position search (+-600 m) plus 4 km of shore rays.
GROUND_HALF_M, GROUND_RES_M = 4800.0, 3.0


#: High-resolution capture of a drone seed (:func:`capture_sphere_pano`): 30-deg views (21 px
#: per degree, against 8.5 at the default 75 deg; the Photo Spheres hold ~23) in pitch rows
#: that together cover -33..+23 deg: tower tops from 36-100 m up and the near waterline. A drone
#: high over land needs rows pointed down (seed_6: -10, -36, -62 covers +5..-77 deg).
HIRES_FOV_DEG = 30.0


HIRES_PITCHES_DEG = (8.0, -18.0)


HIRES_SIZE_PX = 640


#: Stage-cache versions (``stage_cache``: ``runs/stage_cache/<stage>/v<N>``). Bump one when its
#: stage's code changes in a way its key does not see. Depth and instances are keyed by the
#: pano's pixels only (the labels follow from them, and are cached per view so they stay
#: stable); Depth Anything and MobileSAM took 2-9 min a seed and ran again on every review round,
#: and labels, stitching and the pose fit another ~90-125 s (2026-10-06). The pano, pose and
#: measurement keys also hash their code (``stage_cache.source_hash``). Instances v4: T42
#: building_instances_fast (memory-sized batches, 7x faster; full_cover prompts the last
#: half-window the original skipped), 2026-10-07.
DEPTH_CACHE_VERSION = 1


INSTANCES_CACHE_VERSION = 4


LABELS_CACHE_VERSION = 1


STITCH_CACHE_VERSION = 1


POSE_CACHE_VERSION = 1


MEASURED_CACHE_VERSION = 1


FLOORS_CACHE_VERSION = 1


#: The old raw-.npy cache of depth and instances: read (and converted) on a miss only.
PANO_CACHE_DIR = _sc.LEGACY_PANO_CACHE_DIR


def _waterline_camera(seed: SkylinePoint, pano: fd.Pano, pose0: fd.PanoPose, gmap, towers):
    """Camera of a drone over water: waterline position and height, then the bearing (and
    height) from the OSM towers' outline; the recorded position is kept when the towers fit it
    clearly better. Returns (moved pano, pose, PositionFit, OutlineFit or None without towers)."""
    pf = fd.fit_camera_position(pano, pose0, gmap)
    logger.info("[elevated] %s: camera %s, %+.0f m E %+.0f m N of the recorded position, %.0f m "
                "up, heading offset %.1f deg; waterline misfit %.2f -> %.2f deg, ground %.3f -> %.3f",
                seed.name, pf.source, pf.dx_m, pf.dy_m, pf.pose.camera_h_m, pf.pose.offset_deg,
                pf.waterline_at_seed_deg, pf.waterline_deg, pf.ground_at_waterline, pf.ground)
    recorded = pano
    pano = fd.moved(pano, pf.dx_m, pf.dy_m)
    pose = pf.pose
    if towers is None:
        return pano, pose, pf, None
    of = fd.refine_on_outline(pano, pose, towers, height_factors=HEIGHT_FACTORS)
    if (pf.dx_m, pf.dy_m) != (0.0, 0.0):
        # a high drone's waterline can pull the camera hundreds of metres (seed_7: 340 m);
        # keep the recorded position when the towers fit it better -- and on as many tower
        # columns: from 180 m up, 87 columns fitted 0.06 deg by turning to the edge of the
        # search (seed_7, 2026-10-06)
        at_rec = fd.refine_on_outline(recorded, pf.pose, towers, height_factors=HEIGHT_FACTORS)
        if at_rec.misfit_deg < of.misfit_deg - 0.3 and at_rec.n_cols >= max(150, 0.8 * of.n_cols):
            logger.info("[elevated] %s: tower outline fits the recorded position better "
                        "(%.2f vs %.2f deg)", seed.name, at_rec.misfit_deg, of.misfit_deg)
            pano, of = recorded, at_rec
            pf = replace(pf, dx_m=0.0, dy_m=0.0, source=pf.source + ", recorded by outline")
    logger.info("[elevated] %s: tower outline moves the bearing %+.1f deg and the camera "
                "%+.0f m E %+.0f m N, %.0f m up; misfit %.2f -> %.2f deg over %d cols",
                seed.name, of.shift_deg, of.dx_m, of.dy_m, of.pose.camera_h_m,
                of.misfit_before_deg, of.misfit_deg, of.n_cols)
    return fd.moved(pano, of.dx_m, of.dy_m), of.pose, pf, of


#: A drone over water fits the OSM towers' outline (``refine_on_outline``) within ~1.3 deg
#: (Cartagena seeds 1/4/5 spin 0.4-0.9, seed_4 hi-res 1.24; Miami seed_4 0.61). Panos from sea
#: level also pass ``MIN_ELEVATED_H_M`` (a boat deck in Miami fitted 24-30 m up, a Chicago street
#: pano 36-45 m from lake polygons) but their outline never fits: 3.96-4.63 deg after the
#: outline step dragged the camera to the edge of its search, 184-228 m (2026-10-07).
MAX_OUTLINE_MISFIT_DEG = 2.5


#: ... and the drone seeds' outline step moved the camera at most 110 m (seed_4 hi-res); the
#: search reaches 150 m (+50 m fine), so a move past this is the fit running off the edge.
MAX_OUTLINE_MOVE_M = 150.0


def outline_gate(of) -> str | None:
    """Why a waterline-fitted camera is not a drone over water (None: it is).

    Not the distance from the recorded position: a Photo Sphere's recorded position can be
    where the pilot stood (Cartagena seed_4, 340 m off, fitted by the waterline and the
    ground). Not a coastline requirement either: a lake shore comes as water polygons only
    (Chicago), and the boat-deck spheres had the coastline. The tower outline decides: only
    when at least ``refine_on_outline``'s ``min_cols`` tower columns were fitted (no towers in
    view: no verdict)."""
    if of is None or of.n_cols < 50 or not math.isfinite(of.misfit_deg):
        return None
    move = math.hypot(of.dx_m, of.dy_m)
    if of.misfit_deg > MAX_OUTLINE_MISFIT_DEG:
        return (f"the OSM tower outline fits {of.misfit_deg:.2f} deg off over {of.n_cols} "
                f"columns (> {MAX_OUTLINE_MISFIT_DEG})")
    if move > MAX_OUTLINE_MOVE_M:
        return f"the tower outline moves the camera {move:.0f} m (> {MAX_OUTLINE_MOVE_M:.0f})"
    return None


#: Roof-fit confidence from which an overhead reading is trusted (:func:`trusted`).
ROOF_TRUST = 0.5


#: ... and only this close (m).
ROOF_TRUST_MAX_DIST_M = 1000.0


#: Share of a sky-topped building that must show when its base is hidden (:func:`trusted`).
SKY_SEEN_TRUST = 0.5


#: Camera heights the overhead heading search tries before the bases refine it.
OVERHEAD_H_GRID_M = (60.0, 90.0, 130.0, 180.0, 260.0)


#: Below this ground score (mean IoU of water, road and green) an overhead fit is not trusted.
OVERHEAD_MIN_GROUND = 0.2


def overhead_pose(pano: fd.Pano, gmap, footprints, depth, instances, iters: int = 6):
    """Camera of a drone high over land (its near shore below the frame, so no waterline).

    Heading: full circle x a few heights, the ground cast from the recorded position (water,
    roads and parks against OSM; ``_GroundScorer``), then +-3 deg. Height: iterated from the
    building bases (``roof_fit.check_pose_from_bases``, no building height used) until it
    settles. The position stays where the sphere was recorded: letting the ground fit move it too
    put seed_6 260 m off, footprints on open water and heights 46 % off; at the recorded
    position, the heading from the ground and the height from the bases (184 m, bases agree
    within 1 %), its tagged towers read within 10 % and the 7 published heights 11 % median
    (2026-10-06). Returns (pose, ground score, last PoseCheck) or None."""
    from .roof_fit import check_pose_from_bases

    sc = fd._GroundScorer(pano, fd.PanoPose(0.0, 100.0, 0.0, 0.0, pano.width), gmap,
                          (30.0, 1500.0), 3)

    def ground(off, h):
        ex, ny, obs = sc.project(off, h)
        return sc.score(ex, ny, obs, 0.0, 0.0)

    s, off, h = max((ground(o, hh), float(o), hh) for hh in OVERHEAD_H_GRID_M
                    for o in np.arange(0.0, 360.0, 5.0))
    if s < OVERHEAD_MIN_GROUND:
        return None
    def horizon_fix(h_cam):
        """Pitch fix putting the measured sea horizon at its true dip (refraction ~8 %)."""
        if not math.isfinite(pano.horizon_deg):
            return 0.0
        dip = 0.92 * math.degrees(math.sqrt(2.0 * h_cam / 6.371e6))
        return -dip - pano.horizon_deg

    pose = fd.PanoPose(off % 360.0, h, horizon_fix(h), 0.0, pano.width)
    check = None
    for _ in range(iters):
        check = check_pose_from_bases(pano, pose, footprints, depth=depth, instances=instances)
        h_new = check.h_bases_m
        if not (h_new and math.isfinite(h_new)):
            break
        done = abs(h_new - pose.camera_h_m) <= 0.01 * pose.camera_h_m
        s, off = max((ground(o, h_new), float(o)) for o in pose.offset_deg + np.arange(-3.0, 3.01, 0.5))
        pose = replace(pose, offset_deg=off % 360.0, camera_h_m=h_new,
                       pitch_fix_deg=horizon_fix(h_new))
        if done:
            break
    return pose, float(s), check


def _towers(buildings: list[BuildingRecord]):
    """``skyline_match.Towers`` of the OSM-tagged buildings, for the outline fit; None if none."""
    from shapely.geometry import mapping

    from ..skyline_match import tower_table

    feats = [{"geometry": mapping(b.geometry),
              "properties": {"height_m": b.height_tag_m, "height_source": b.height_source,
                             "name": b.name}}
             for b in buildings if b.geometry is not None and b.height_tag_m]
    try:
        return tower_table(feats)
    except ValueError:
        return None


#: Road widths by OSM class when the fetch gave none (m).
ROAD_WIDTH_M = {"trunk": 16.0, "primary": 14.0, "secondary": 12.0, "tertiary": 10.0,
                "residential": 7.0, "unclassified": 7.0}


_LAYER_CACHE = Path(__file__).resolve().parents[1] / "runs" / "osm_layers"


def _bbox_key(bbox) -> list[float]:
    return [round(float(v), 6) for v in (bbox.north, bbox.south, bbox.east, bbox.west)]


def _cached_layers(bbox, names: list[str], region_name: str) -> dict:
    """OSM ``names`` layers for ``bbox``: from ``runs/osm_layers/<region>_<layers>.json.gz`` when
    saved for this bbox, else fetched (``fetch_osm_data``) and saved when anything came back.
    A failed fetch returns ``{}``."""
    import gzip
    import json

    path = _LAYER_CACHE / f"{region_name.lower()}_{'-'.join(sorted(names))}.json.gz"
    if path.exists():
        try:
            doc = json.loads(gzip.decompress(path.read_bytes()))
            if doc.get("bbox_nsew") == _bbox_key(bbox):
                return doc["layers"]
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("[elevated] ignoring unreadable %s: %s", path.name, exc)
    try:
        from city2stl.fetch import fetch_osm_data

        got = fetch_osm_data(bbox.north, bbox.south, bbox.east, bbox.west, list(names))
    except Exception as exc:
        logger.warning("[elevated] OSM %s fetch failed: %s", names, exc)
        return {}
    if any((got.get(k) or {}).get("features") for k in names):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(
            {"bbox_nsew": _bbox_key(bbox), "layers": got}).encode("utf-8")))
    return got


def ground_layers(osm: dict, bbox, region_name: str) -> dict:
    """``elevated_state`` for the orchestrator: OSM coastline lines, water polygons, roads
    (line, width_m) and green areas, shapely in lon/lat. Waterways, roads and green are optional
    in the region's building cache (Cartagena's held an empty waterways layer, 2026-10-05), so
    the missing ones are fetched on their own and cached per region (:func:`_cached_layers`)."""
    from shapely.geometry import shape

    from ..osm_water import extract_coastline_features, extract_water_features

    layers = dict(osm)
    missing = [k for k in ("waterways", "roads", "green")
               if not (layers.get(k) or {}).get("features")]
    if missing:
        layers.update(_cached_layers(bbox, missing, region_name))
    roads = []
    for f in (layers.get("roads") or {}).get("features") or []:
        p = f.get("properties") or {}
        hw = p.get("highway")
        hw = str(hw[0] if isinstance(hw, list) else hw).replace("_link", "")
        try:
            roads.append((shape(f["geometry"]),
                          float(p.get("road_width_m") or ROAD_WIDTH_M.get(hw, 6.0))))
        except Exception:
            continue

    def shapes(feats):
        out = []
        for f in feats:
            try:
                out.append(shape(f["geometry"]))
            except Exception:
                continue
        return out

    state = {"coast_lines": shapes(extract_coastline_features(layers)),
             "water_polys": shapes(extract_water_features(layers)),
             "roads": roads,
             "green": shapes((layers.get("green") or {}).get("features") or [])}
    logger.info("[elevated] ground layers: %d coastline, %d water, %d roads, %d green",
                *(len(state[k]) for k in ("coast_lines", "water_polys", "roads", "green")))
    return state


def estimate_confidence(m) -> float:
    """``aggregate_building_heights`` weight: seen share x (300 m / distance)^2, in 0.05..1."""
    seen = 1.0 if m.base_visible else float(m.visible_frac)
    return float(np.clip(seen * (300.0 / max(float(m.dist_m), 300.0)) ** 2, 0.05, 1.0))


def trusted(m) -> bool:
    """A drone reading whose column climbed to the sky with the building's base in view.

    Calibrated on Cartagena's OSM-tagged buildings (2026-10-05, cameras aligned on the tower
    outline): such readings were within 25 % of the tag in 88 % of cases (n 8); sky-topped ones
    in 73 % (11); runs that stopped at a depth step in 21 % (19), base hidden in 10 % (10).
    A roof fit from above (``roof_fit``, top edge ``roof``) counts when its confidence is 0.5 or
    more (seed_6, 2026-10-06: Estelar 199/202, Portomarine 189/188, Gran Bay 170/170 published or
    tagged; none of its confident readings was left out before).
    A sky-topped reading with its base hidden counts when at least half the building shows
    (``visible_frac`` >= :data:`SKY_SEEN_TRUST`): on Cartagena seeds 1/4/5/6 that took the fused
    heights from 215 to 276 buildings, the OSM-tag error from 58 % (n 7) to 12 % (n 14) median
    and the published towers from 3 to 7 (median 7 %, 6 of 7 within 25 %), 2026-10-06.
    """
    if m.top_edge == "roof":
        # and within ROOF_TRUST_MAX_DIST_M: past 1 km low buildings read the towers behind
        # them with high confidence (seed_6 b0372 104/55 m at 1.0 km, seed_7 b0265 234/50 m at
        # 1.6 km); every confident roof fit within 810 m was right (2026-10-06)
        return (float(getattr(m, "confidence", 0.0)) >= ROOF_TRUST
                and m.dist_m <= ROOF_TRUST_MAX_DIST_M)
    return m.top_edge == "sky" and (bool(m.base_visible) or m.visible_frac >= SKY_SEEN_TRUST)


def untrusted_reason(m) -> str | None:
    """Why :func:`trusted` refuses a reading (None when it is trusted), for the report:
    ``roof_conf_low`` / ``roof_beyond_<N>m`` (an overhead roof fit), ``tag_behind`` (its top is a
    farther tagged tower's, :func:`_untrust_behind`), ``depth_edge`` (the run stopped at a depth
    step), ``top_<edge>`` (another top edge), ``base_hidden`` (sky-topped, base hidden, under
    :data:`SKY_SEEN_TRUST` seen). ``tower_behind`` is set later, by :func:`elevated_estimates`."""
    if trusted(m):
        return None
    if m.top_edge == "roof":
        if float(getattr(m, "confidence", 0.0)) < ROOF_TRUST:
            return "roof_conf_low"
        return f"roof_beyond_{ROOF_TRUST_MAX_DIST_M:.0f}m"
    if m.top_edge == "behind":
        return "tag_behind"
    if m.top_edge == "depth":
        return "depth_edge"
    if m.top_edge != "sky":
        return f"top_{m.top_edge}"
    return "base_hidden"


def _mark_tower_behind(seeds, behind: dict) -> None:
    """Set ``trusted`` False and ``untrusted_reason`` ``"tower_behind"`` on the report segments
    of the readings :func:`tower_behind` left out."""
    for s in seeds:
        for seg in getattr(s.pano_result, "matched_segments", None) or ():
            fid = (seg.get("matched_projection") or {}).get("feature_id")
            if (s.seed_name, fid) in behind:
                seg["trusted"] = False
                seg["untrusted_reason"] = "tower_behind"
