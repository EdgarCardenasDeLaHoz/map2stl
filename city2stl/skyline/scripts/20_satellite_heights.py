#!/usr/bin/env python3
"""Offline satellite heights for a region: ``runs/satellite/<region>/readings.json``.

    python -m city2stl.skyline.scripts.20_satellite_heights --region cartagena \\
        --scene 2021-02-11_GE01=47568 ... --scene 2026-02-10_LG01=32246 --ref 2026-02-10_LG01

Per region (F-SKY26 step 7; methods in ``city2stl.height.satellite``):
1. footprints: the region's cached OSM buildings, same ids as the region run
   (``region_data._osm_to_building_records``);
2. scenes: Esri Wayback releases (``--scene NAME=RELEASE``, or ``--discover`` to identify the
   distinct scenes at the region centre); z18 tiles cached in ``runs/satellite/<region>/tiles/
   <scene>/`` (``--seed-tiles DIR`` copies an existing ``DIR/<scene>/`` cache first); missing
   tiles are fetched only with ``--fetch`` (free Esri imagery, 4 concurrent);
3. geometry per scene: lean and registration (``scene.fit_lean``), sun (``scene.solve_sun``);
   cached in ``scenes.json`` (``--refit`` redoes it); each scene's outline in its release
   (``scene.scene_polygon``: all the scene's polygons in the region bbox, cached in
   ``polygons.json``; ``--outlines`` re-queries them): a release mosaics several captures, so
   outside the outline its tiles are another image with another lean and sun. Tiles are fetched,
   the reference scene measures and the other scenes add shadows and stereo only inside it;
4. measurements: shadow + lean on ``--ref`` (``measure.measure_single``), per-scene shadows and
   the plane sweep (``measure.measure_multi``) for footprints whose centre tile the reference
   scene has cached;
5. readings (``readings.from_measurements``) to ``readings.json``, keyed by footprint id, with
   lat/lon to re-match later runs. Cached by scene names and dates: an unchanged scene set is not
   re-measured unless ``--force``. ``--add`` (same scenes) measures only the footprints not
   measured yet (e.g. after ``--fetch`` extended the tiles) and keeps the others' readings and
   the shadow grey threshold (``_meta.dark``). ``--jobs N`` measures in N processes (whole
   ``measure_multi`` blocks each; Cartagena ~2.5 s per footprint in one); ``--fids FILE`` /
   ``--out FILE`` measure a subset into another file (a quick look before a long run);
   ``--recalibrate`` re-applies ``readings.calibrate`` to the stored file.
6. search hints (``--hints``, 2026-10-09): without a tag the shadow and lean searches stop at 60 m
   and the sweep at 80 m, so an untagged tower was never read tall. ``--hints`` takes a region
   run's ``heights.json`` (or ``{fid: metres}``): an untagged footprint's highest drone or floors
   reading widens its windows (``measure.search_cap``: 1.6x the reading, at least 220 m over
   80 m). Same scenes: only the footprints whose windows change are re-measured, the rest are
   kept. The hints are stored in ``_meta.hints`` and reused by later runs. NOT for publishing
   (refused 2026-10-09, decisions/building-heights.md): untagged on Honolulu LiDAR the 220 m
   window gave leans >= 40 m on 35.6 % of footprints under 30 m (60 m window: 11.1 %), and the
   Cartagena replay published 6 rows over 205 m (the city's tallest is 202 m). A measuring tool.

The region run never measures; it reads ``readings.json`` when the site has
``use_satellite_heights`` (``city2stl/skyline/satellite_fusion.py``).
"""

from __future__ import annotations

from city2stl.resources import scratch_guard

workers = scratch_guard(gpu_gb=None, ram_gb=6.0, max_workers=3)

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from city2stl.height.satellite import footprints_from_records  # noqa: E402
from city2stl.height.satellite import measure as sm  # noqa: E402
from city2stl.height.satellite import readings as sr  # noqa: E402
from city2stl.height.satellite import scene as ss  # noqa: E402

log = logging.getLogger("satellite_heights")
RUNS = Path(__file__).resolve().parents[1] / "runs"


def region_dir(region: str) -> Path:
    return RUNS / "satellite" / region


def readings_path(region: str) -> Path:
    return region_dir(region) / "readings.json"


def load_records(region: str):
    from city2stl.skyline.region_data import (  # noqa: PLC0415
        _load_osm_for_region,
        _load_region_bbox,
        _osm_to_building_records,
    )

    bbox = _load_region_bbox(region)
    osm, src = _load_osm_for_region(bbox)
    log.info("OSM %s", src)
    return _osm_to_building_records(osm), bbox


def discover(cfg: dict, lat: float, lon: float, every: int = 4) -> dict:
    """{scene name: release} of the distinct scenes at a point (every ``every``-th release plus
    the newest, as w1_meta), the latest release that shows each."""
    rels = sorted(cfg, key=lambda r: ss.release_date(cfg, r))
    rels = rels[::every] + [rels[-1]]
    out = {}
    for rel in rels:
        got = ss.identify(cfg, rel, lat, lon)
        if got:
            out[ss.scene_name(got["attrs"])] = rel
    return out


def scene_tiles(footprints, M: float, margin_m: float = 60.0) -> set:
    """z18 tiles around the footprints (+ margin for lean and shadows)."""
    e = margin_m / M
    T = set()
    for f in footprints:
        x0, y0, x1, y1 = f["bb"]
        T |= {(x, y) for x in range(int((x0 - e) // 256), int((x1 + e) // 256) + 1)
              for y in range(int((y0 - e) // 256), int((y1 + e) // 256) + 1)}
    return T


def fit_scene(sc: ss.Scene, fps, by_fid, M, lat0, lon0) -> dict:
    lean = ss.fit_lean(sc.tiles, fps, M)
    if "error" in lean:
        return dict(lean=lean, sun=None)
    date = (sc.date or sc.name[:10]).replace("-", "")
    sun = ss.solve_sun(sc.tiles, by_fid, lean["tower_fids"], np.array([lean["r_E"], lean["r_S"]]),
                       date, lat0, lon0, M)
    return dict(lean=lean, sun=sun)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--scene", action="append", default=[], metavar="NAME=RELEASE")
    ap.add_argument("--ref", default=None, help="reference scene (default: the newest)")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--seed-tiles", default=None, help="copy DIR/<scene>/18_*.jpg into the cache")
    ap.add_argument("--fetch", action="store_true", help="fetch missing tiles (free Esri)")
    ap.add_argument("--refit", action="store_true")
    ap.add_argument("--outlines", action="store_true",
                    help="re-query the scene outlines (polygons.json; network, needs --fetch)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--wayback-config", default=None, help="cached waybackconfig.json to use")
    ap.add_argument("--sun", action="append", default=[], metavar="SCENE=BEARING,EL",
                    help="fix a scene's sun (e.g. visually verified) instead of the time search")
    ap.add_argument("--add", action="store_true",
                    help="same scenes: measure only footprints not measured yet, keep the rest")
    ap.add_argument("--recalibrate", action="store_true",
                    help="only re-apply readings.calibrate to the stored readings (no measuring)")
    ap.add_argument("--hints", default=None,
                    help="region run heights.json (or {fid: m} JSON): untagged footprints' highest "
                         "drone or floors reading widens their search; same scenes: re-measure "
                         "only the footprints whose search window changes. Not for publishing "
                         "(refused 2026-10-09: spurious tall leans)")
    ap.add_argument("--fids", default=None, help="file of footprint ids: measure only these")
    ap.add_argument("--out", default=None,
                    help="write here instead (the region's readings.json is still read for --add)")
    ap.add_argument("--jobs", type=int, default=1,
                    help=f"measuring processes (block groups; at most the guard's {workers})")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if a.recalibrate:
        return recalibrate(readings_path(a.region))
    t0 = time.time()
    rd = region_dir(a.region)
    rd.mkdir(parents=True, exist_ok=True)
    recs, bbox = load_records(a.region)
    fps = footprints_from_records(recs)
    by_fid = {f["fid"]: f for f in fps}
    lat_c, lon_c = (bbox.north + bbox.south) / 2, (bbox.east + bbox.west) / 2
    cfg_path = rd / "waybackconfig.json"
    if a.wayback_config and not cfg_path.exists():
        shutil.copy(a.wayback_config, cfg_path)
    scenes_rel = dict(s.split("=", 1) for s in a.scene)
    if a.discover:
        cfg = ss.wayback_config(cfg_path)
        scenes_rel.update(discover(cfg, lat_c, lon_c))
    if not scenes_rel:
        ap.error("no scenes (--scene or --discover)")
    names = sorted(scenes_rel)                  # by date
    ref = a.ref or names[-1]
    names = [n for n in names if n != ref] + [ref]   # reference last (w4 order)
    stats = {}
    for n in names:
        d = rd / "tiles" / n
        if a.seed_tiles and (Path(a.seed_tiles) / n).is_dir():
            d.mkdir(parents=True, exist_ok=True)
            for p in (Path(a.seed_tiles) / n).glob("18_*.jpg"):
                if not (d / p.name).exists():
                    shutil.copy2(p, d / p.name)
    # footprints measured: centre tile cached in the reference scene, inside its outline
    ref_src = ss.TileSource(rd / "tiles" / ref)
    lat0 = float(np.mean([f["lat"] for f in fps])) if fps else lat_c
    M = ss.mpp(lat0)
    log.info("before: %d of %d footprints on cached %s tiles", len(on_tiles(fps, ref_src)), len(fps), ref)
    polys = scene_polygons(rd / "polygons.json", names, scenes_rel, fps, (lat_c, lon_c),
                           (lambda: ss.wayback_config(cfg_path)) if a.fetch else None,
                           refresh=a.outlines)
    inside = {n: covered_fids(polys.get(n), fps) for n in names}
    for n in names:
        log.info("scene %s outline: %s of %d footprints inside", n,
                 "unknown (all)" if polys.get(n) is None else len(inside[n]), len(fps))
    if a.fetch:
        cfg = ss.wayback_config(cfg_path)
        for n in names:
            need = scene_tiles([f for f in fps if f["fid"] in inside[n]], M)
            stats[n] = ss.fetch_tiles(cfg[scenes_rel[n]]["itemURL"], rd / "tiles" / n, need)
            log.info("tiles %s %s", n, stats[n])
        ref_src.refresh()
    sel = [f for f in on_tiles(fps, ref_src) if f["fid"] in inside[ref]]
    if not sel:
        log.error("no footprint on the reference scene's cached tiles")
        return 1
    lat0 = float(np.mean([f["lat"] for f in sel]))
    lon0 = float(np.mean([f["lon"] for f in sel]))
    M = ss.mpp(lat0)
    log.info("%d footprints, %d on cached %s tiles; M %.4f", len(fps), len(sel), ref, M)
    # ---- scene geometry (cached)
    gpath = rd / "scenes.json"
    geom = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() and not a.refit else {}
    fixed_sun = {k: tuple(map(float, v.split(","))) for k, v in (s.split("=", 1) for s in a.sun)}
    scenes = []
    for n in names:
        src = ss.TileSource(rd / "tiles" / n)
        sc = ss.Scene(name=n, tiles=src, date=n[:10].replace("-", ""), sensor=n[11:])
        if n not in geom:
            log.info("fitting %s", n)
            geom[n] = fit_scene(sc, fps, by_fid, M, lat0, lon0)
            gpath.write_text(json.dumps(geom, indent=1, default=str), encoding="utf-8")
        g = geom[n]
        if "error" in g["lean"]:
            log.warning("scene %s: %s; skipped", n, g["lean"]["error"])
            continue
        sc.lean = np.array([g["lean"]["L_E"], g["lean"]["L_S"]])
        sc.reg_m = np.array([g["lean"]["r_E"], g["lean"]["r_S"]])
        if n in fixed_sun:
            sc.shadow_bearing, sc.sun_el = fixed_sun[n]
            sc.meta["sun"] = "fixed (--sun)"
        elif g.get("sun"):
            sc.shadow_bearing, sc.sun_el = g["sun"]["shadow_bearing"], g["sun"]["el"]
        if len(inside[n]) < len(fps):
            sc.covered = inside[n]
        scenes.append(sc)
    if ref not in {s.name for s in scenes}:
        log.error("reference scene %s has no geometry", ref)
        return 1
    ref_sc = next(s for s in scenes if s.name == ref)
    key = {s.name: dict(date=s.date, L=[round(float(v), 4) for v in s.lean],
                        sun=[s.shadow_bearing, s.sun_el]) for s in scenes}
    out = readings_path(a.region)
    prev_meta, prev = sr.load(out) if (a.add or a.hints or not a.force) else ({}, {})
    same = bool(prev_meta) and prev_meta.get("scenes") == json.loads(json.dumps(key))
    if a.add and prev_meta and not same:
        log.error("--add: the scenes changed since %s; re-measure all with --force", out)
        return 1
    old_hints = (prev_meta.get("hints") or {}) if same else {}
    hints = load_hints(Path(a.hints), fps, a.region) if a.hints else dict(old_hints)
    set_hints(fps, hints)
    rehint = changed_windows(sel, old_hints, hints) if (a.hints and same and not a.force) else set()
    done = measured_before(prev_meta, prev) - rehint if ((a.add or rehint) and same) else set()
    todo = [f for f in sel if f["fid"] not in done]
    if rehint and not a.add:                    # --hints alone: only the changed windows
        todo = [f for f in todo if f["fid"] in rehint]
    if a.hints:
        log.info("hints: %d untagged footprints (%d on the reference tiles); %d windows changed",
                 sum(1 for f in fps if f.get("hint")), sum(1 for f in sel if f.get("hint")),
                 len(rehint))
    if a.fids:
        keep = set(Path(a.fids).read_text(encoding="utf-8").split())
        todo = [f for f in todo if f["fid"] in keep]
    if same and not a.force and not ((a.add or rehint) and todo):
        log.info("readings for these scenes exist: %s (--force to redo, --add after --fetch)", out)
        return 0
    # ---- measure
    if ref_sc.shadow_bearing is not None:
        if done and prev_meta.get("dark") is not None:
            ref_sc.dark = float(prev_meta["dark"])
        else:   # the reference window: the footprints measured before when adding
            win = [f for f in sel if f["fid"] in done] or sel
            gray, hv = ref_sc.tiles.crop(*_window(win))
            ref_sc.dark = float(0.5 * (np.percentile(gray[hv], 3) + np.percentile(gray[hv], 50)))
            del gray, hv
    log.info("measuring %d footprints (%d measured before); dark %.1f", len(todo), len(done),
             ref_sc.dark or float("nan"))
    only = {f["fid"] for f in todo}
    jobs = max(1, min(a.jobs, workers))
    parts = block_groups(todo, jobs) if only else []
    single, multi = {}, {}
    if len(parts) > 1:
        from concurrent.futures import ProcessPoolExecutor  # noqa: PLC0415

        with ProcessPoolExecutor(len(parts)) as ex:
            for s1, m1 in ex.map(_measure_part, [(fps, ref_sc, scenes, ref, M, p) for p in parts]):
                single.update(s1)
                multi.update(m1)
    elif parts:
        single, multi = _measure_part((fps, ref_sc, scenes, ref, M, parts[0]))
    rs = sr.from_measurements(single, multi, ref)
    where = {f["fid"]: dict(lat=round(f["lat"], 7), lon=round(f["lon"], 7)) for f in todo}
    if done:                                    # --add / --hints: keep the others
        for fid, v in prev.items():
            if fid not in rs and fid not in only:
                rs[fid] = v["readings"]
                where[fid] = {k: v[k] for k in ("lat", "lon", "osm_id") if k in v}
    measured = sorted(done | only)
    meta = dict(region=a.region, scenes=key, ref=ref, releases={n: scenes_rel[n] for n in names},
                n_footprints=len(measured), n_with_readings=len(rs), mpp=M, fetch=stats,
                dark=ref_sc.dark, measured=measured, hints=hints,
                outline_footprints={n: len(inside[n]) for n in names},
                built=time.strftime("%Y-%m-%d %H:%M"), seconds=round(time.time() - t0))
    out = Path(a.out) if a.out else out
    sr.save(out, rs, meta, where)
    by_m = {}
    for v in rs.values():
        for r in v:
            by_m[r.method] = by_m.get(r.method, 0) + 1
    log.info("wrote %s: %d footprints with readings %s, %.0f s", out, len(rs), by_m, time.time() - t0)
    return 0


def block_groups(todo, n: int, bs: int = 1024) -> list[list]:
    """``todo``'s footprint ids in ``n`` groups of whole ``measure_multi`` blocks (``bs`` px),
    balanced by count."""
    blocks: dict = {}
    for f in todo:
        k = (int((f["bb"][0] + f["bb"][2]) / 2 // bs), int((f["bb"][1] + f["bb"][3]) / 2 // bs))
        blocks.setdefault(k, []).append(f["fid"])
    groups: list[list] = [[] for _ in range(max(1, n))]
    for ids in sorted(blocks.values(), key=len, reverse=True):
        min(groups, key=len).extend(ids)
    return [g for g in groups if g]


def _measure_part(job) -> tuple[dict, dict]:
    """Shadow + lean on the reference scene and the multi-scene sweep for one group of ids."""
    fps, ref_sc, scenes, ref, M, ids = job
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")   # spawned workers
    single = sm.measure_single(fps, ref_sc, M, dark=ref_sc.dark or 80.0, only=set(ids),
                               progress=log.info) if ref_sc.shadow_bearing is not None else {}
    multi = sm.finish_multi(sm.measure_multi(fps, scenes, ref, list(ids), M, progress=log.info)) \
        if len(scenes) >= 2 else {}
    return single, multi


def recalibrate(path: Path) -> int:
    """Re-apply ``readings.calibrate`` (2026-10-08 confidence corrections) to a stored
    ``readings.json``: it needs only the stored readings, so nothing is re-measured."""
    meta, data = sr.load(path, calibrate=False)
    if not data:
        log.error("no readings at %s", path)
        return 1
    rs = {fid: sr.calibrate(v["readings"]) for fid, v in data.items()}
    rs = {fid: v for fid, v in rs.items() if v}
    n = sum(1 for fid in rs if [r.to_json() for r in rs[fid]] != [r.to_json() for r in data[fid]["readings"]])
    where = {fid: {k: v[k] for k in ("lat", "lon", "osm_id") if k in v} for fid, v in data.items()}
    sr.save(path, rs, dict(meta, calibrated=time.strftime("%Y-%m-%d"), n_with_readings=len(rs)), where)
    log.info("recalibrated %s: %d of %d footprints changed", path, n, len(data))
    return 0


def load_hints(path: Path, fps=(), region: str | None = None) -> dict:
    """``{fid: metres}`` search hints. A region run's ``heights.json``: per untagged row, the
    highest drone reading (the site's ``elevated_seeds`` and the ``drone:<seed>`` methods of the
    rows: ``per_seed_median_m``, those seeds' views, a withheld drone single) or floors height
    (``floors`` x ``storey_m``); matched to ``fps`` by id when the centroids are within 3 m, else
    to the nearest centroid within 3 m. Any other JSON: ``{fid: metres}`` as is."""
    body = json.loads(path.read_text(encoding="utf-8"))
    if "buildings" not in body:
        return {str(k): float(v) for k, v in body.items() if v and not str(k).startswith("_")}
    rows = body["buildings"]
    drones = set()
    site = Path(__file__).resolve().parents[1] / "sites" / f"{region or body.get('region', '')}.json"
    if site.is_file():
        drones |= set(json.loads(site.read_text(encoding="utf-8-sig")).get("elevated_seeds") or ())
    for r in rows:
        drones |= {m.split(":", 1)[1] for m in (r.get("tier_methods") or []) + (r.get("single_methods") or [])
                   if str(m).startswith("drone:")}
    by_fid = {f["fid"]: f for f in fps}
    grid: dict = {}
    for f in fps:
        grid.setdefault((round(f["lat"], 3), round(f["lon"], 3)), []).append(f)
    out = {}
    for r in rows:
        if r.get("height_tag_m"):
            continue
        v = [float(h) for sd, h in (r.get("per_seed_median_m") or {}).items() if sd in drones and h]
        v += [float(w["height_m"]) for w in r.get("views") or ()
              if w.get("height_m") and str(w.get("view_name", "")).rsplit("_", 1)[0] in drones]
        if r.get("single_source") == "withheld:elevated" and r.get("single_reading_m"):
            v.append(float(r["single_reading_m"]))
        if r.get("floors") and r.get("storey_m"):
            v.append(float(r["floors"]) * float(r["storey_m"]))
        if not v:
            continue
        fid = r.get("feature_id")
        lat, lon = r.get("centroid_lat"), r.get("centroid_lon")
        if fps and lat is not None:
            f = by_fid.get(fid)
            if f is None or _dist_m(f, lat, lon) > 3.0:
                cands = [g for dy in (-0.001, 0, 0.001) for dx in (-0.001, 0, 0.001)
                         for g in grid.get((round(lat + dy, 3), round(lon + dx, 3)), [])]
                f = min(cands, key=lambda g: _dist_m(g, lat, lon), default=None)
                if f is None or _dist_m(f, lat, lon) > 3.0:
                    continue
            fid = f["fid"]
        out[fid] = round(max(v), 1)
    return out


def _dist_m(f, lat, lon) -> float:
    return float(np.hypot((f["lat"] - lat) * 111320.0,
                          (f["lon"] - lon) * 111320.0 * np.cos(np.radians(lat))))


def set_hints(fps, hints: dict) -> None:
    """Puts ``hints`` on the untagged footprints (``hint``; a tag sizes the windows itself)."""
    for f in fps:
        f.pop("hint", None)
        if not f["tag"] and hints.get(f["fid"]):
            f["hint"] = float(hints[f["fid"]])


def _windows(f, hint) -> tuple:
    g = dict(f, hint=hint)
    return sm.search_cap(g, 60.0, 320.0), sm.search_cap(g, 80.0, 330.0)


def changed_windows(fps, old: dict, new: dict) -> set:
    """Footprint ids whose search windows differ between the ``old`` and ``new`` hints."""
    return {f["fid"] for f in fps if not f["tag"]
            and _windows(f, old.get(f["fid"])) != _windows(f, new.get(f["fid"]))}


def on_tiles(fps, src: ss.TileSource) -> list:
    """The footprints whose centre tile ``src`` has cached."""
    return [f for f in fps if (int(np.mean(f["bb"][0::2]) // 256), int(np.mean(f["bb"][1::2]) // 256)) in src.tiles]


def scene_polygons(path: Path, names, scenes_rel: dict, fps, centre, cfg_fn=None,
                   refresh: bool = False) -> dict:
    """``{scene: rings or None}``, cached in ``path``; missing ones (all of ``names`` with
    ``refresh``) are looked up only with ``cfg_fn`` (the Wayback config loader; network): every
    polygon of the scene inside the footprints' bbox (``scene.scene_outlines``), else identify at
    the region centre and then at the tallest tagged footprints (one polygon)."""
    polys = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    missing = [n for n in names if refresh or n not in polys]
    if missing and cfg_fn is not None:
        cfg = cfg_fn()
        pts = [centre] + [(f["lat"], f["lon"]) for f in
                          sorted((f for f in fps if f["tag"]), key=lambda f: -f["tag"])[:6]]
        bbox = (min(f["lon"] for f in fps) - 0.001, min(f["lat"] for f in fps) - 0.001,
                max(f["lon"] for f in fps) + 0.001, max(f["lat"] for f in fps) + 0.001) if fps else None
        for n in missing:
            polys[n] = ss.scene_polygon(cfg, scenes_rel[n], n, pts, bbox=bbox)
            if polys[n] is None:
                log.warning("scene %s: outline not found in release %s", n, scenes_rel[n])
        path.write_text(json.dumps(polys), encoding="utf-8")
    return polys


def covered_fids(rings, fps) -> set:
    """Footprint ids whose centroid is inside the scene outline (all when it is unknown)."""
    if not rings or not fps:
        return {f["fid"] for f in fps}
    ok = ss.in_rings(rings, [f["lon"] for f in fps], [f["lat"] for f in fps])
    return {f["fid"] for f, k in zip(fps, ok, strict=True) if k}


def measured_before(meta: dict, data: dict) -> set:
    """Footprint ids an earlier run measured (``_meta.measured``; else those with readings)."""
    return set(meta.get("measured") or data)


def _window(fps, pad: int = 64):
    A = np.array([f["bb"] for f in fps])
    return int(A[:, 0].min()) - pad, int(A[:, 1].min()) - pad, int(A[:, 2].max()) + pad, int(A[:, 3].max()) + pad


if __name__ == "__main__":
    raise SystemExit(main())
