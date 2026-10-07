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
   cached in ``scenes.json`` (``--refit`` redoes it);
4. measurements: shadow + lean on ``--ref`` (``measure.measure_single``), per-scene shadows and
   the plane sweep (``measure.measure_multi``) for footprints whose centre tile the reference
   scene has cached;
5. readings (``readings.from_measurements``) to ``readings.json``, keyed by footprint id, with
   lat/lon to re-match later runs. Cached by scene names and dates: an unchanged scene set is not
   re-measured unless ``--force``.

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
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--wayback-config", default=None, help="cached waybackconfig.json to use")
    ap.add_argument("--sun", action="append", default=[], metavar="SCENE=BEARING,EL",
                    help="fix a scene's sun (e.g. visually verified) instead of the time search")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
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
    # footprints measured: centre tile cached in the reference scene
    ref_src = ss.TileSource(rd / "tiles" / ref)
    lat0 = float(np.mean([f["lat"] for f in fps])) if fps else lat_c
    M = ss.mpp(lat0)
    if a.fetch:
        cfg = ss.wayback_config(cfg_path)
        need = scene_tiles(fps, M)
        for n in names:
            stats[n] = ss.fetch_tiles(cfg[scenes_rel[n]]["itemURL"], rd / "tiles" / n, need)
            log.info("tiles %s %s", n, stats[n])
        ref_src.refresh()
    sel = [f for f in fps if (int(np.mean(f["bb"][0::2]) // 256), int(np.mean(f["bb"][1::2]) // 256)) in ref_src.tiles]
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
        scenes.append(sc)
    if ref not in {s.name for s in scenes}:
        log.error("reference scene %s has no geometry", ref)
        return 1
    ref_sc = next(s for s in scenes if s.name == ref)
    key = {s.name: dict(date=s.date, L=[round(float(v), 4) for v in s.lean],
                        sun=[s.shadow_bearing, s.sun_el]) for s in scenes}
    out = readings_path(a.region)
    if out.exists() and not a.force:
        meta, _ = sr.load(out)
        if meta.get("scenes") == json.loads(json.dumps(key)):
            log.info("readings for these scenes exist: %s (--force to redo)", out)
            return 0
    # ---- measure
    if ref_sc.shadow_bearing is not None:
        gray, hv = ref_sc.tiles.crop(*_window(sel))
        ref_sc.dark = float(0.5 * (np.percentile(gray[hv], 3) + np.percentile(gray[hv], 50)))
        del gray, hv
    only = {f["fid"] for f in sel}
    single = sm.measure_single(fps, ref_sc, M, dark=ref_sc.dark or 80.0, only=only, progress=log.info) \
        if ref_sc.shadow_bearing is not None else {}
    multi = sm.finish_multi(sm.measure_multi(fps, scenes, ref, [f["fid"] for f in sel], M, progress=log.info)) \
        if len(scenes) >= 2 else {}
    rs = sr.from_measurements(single, multi, ref)
    where = {f["fid"]: dict(lat=round(f["lat"], 7), lon=round(f["lon"], 7)) for f in sel}
    meta = dict(region=a.region, scenes=key, ref=ref, releases={n: scenes_rel[n] for n in names},
                n_footprints=len(sel), n_with_readings=len(rs), mpp=M, fetch=stats,
                built=time.strftime("%Y-%m-%d %H:%M"), seconds=round(time.time() - t0))
    sr.save(out, rs, meta, where)
    by_m = {}
    for v in rs.values():
        for r in v:
            by_m[r.method] = by_m.get(r.method, 0) + 1
    log.info("wrote %s: %d footprints with readings %s, %.0f s", out, len(rs), by_m, time.time() - t0)
    return 0


def _window(fps, pad: int = 64):
    A = np.array([f["bb"] for f in fps])
    return int(A[:, 0].min()) - pad, int(A[:, 1].min()) - pad, int(A[:, 2].max()) + pad, int(A[:, 3].max()) + pad


if __name__ == "__main__":
    raise SystemExit(main())
