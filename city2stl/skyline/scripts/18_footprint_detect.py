#!/usr/bin/env python3
"""F-DET6: building heights from elevated (drone) Photo Sphere seeds, footprint first.

    python -m city2stl.skyline.scripts.18_footprint_detect --region cartagena --seeds seed_5 seed_1 --out <dir>

Per seed (``footprint_detect``): rebuild the stitched pano from the cached spin views (Street
View API on a cache miss), fit the heading offset, camera height and pitch from the near
waterline against the OSM shore, run Depth Anything V2 on the pano, and measure every OSM
footprint in view. Writes ``<seed>_pose.json``, ``<seed>_measured.json``,
``<seed>_summary.json``, ``<seed>_waterline.png`` and ``<seed>_footprint_boxes.png``; the pano,
shore table and depth map are cached in ``--out`` (``<seed>_pano.pkl``, ``_shore.npy``,
``_depth.npy``), so a re-run only measures.

Truth-free checks (Cartagena has no 3D Tiles buildings and few OSM heights): predicted against
observed base rows, OSM height tags, and with two or more seeds the agreement on footprints
both measured (``compare.json``). ``--position-check`` also fits the camera position from the
ground (parks, streets, water against OSM; ``fit_position_from_ground``) and scans the waterline
misfit around the seed (``waterline_position_scan``), into ``<seed>_position.json``. It reports
and does not move the camera: on the Cartagena seeds the waterline already pins the position
(2026-10-04, see the F-DET plan).
"""

from __future__ import annotations

import argparse
import gzip
import json
import pickle
import time
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline import osm_water
from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox


def _ground_layers(osm: dict, bbox, ground_osm: str | None) -> dict:
    """OSM waterways, roads and green for the region. Read from ``ground_osm`` (a ``.json.gz``
    OSM cache file) when given; else taken from the region's OSM and fetched when missing
    (the building cache often lacks these optional layers)."""
    from city2stl.fetch import fetch_osm_data

    if ground_osm:
        return json.loads(gzip.decompress(Path(ground_osm).read_bytes()))
    missing = [k for k in ("waterways", "roads", "green") if not (osm.get(k) or {}).get("features")]
    layers = dict(osm)
    if missing:
        layers.update(fetch_osm_data(bbox.north, bbox.south, bbox.east, bbox.west, missing))
    return layers


def _coast_lines(layers: dict):
    """OSM coastline plus water-polygon boundaries as shapely lines."""
    from shapely.geometry import shape

    coast = [shape(f["geometry"]) for f in osm_water.extract_coastline_features(layers)]
    water = [shape(f["geometry"]).boundary for f in osm_water.extract_water_features(layers)]
    print(f"OSM coastline features {len(coast)}, water polygons {len(water)}")
    if not coast and not water:
        raise SystemExit("no OSM coastline or water: pass --ground-osm")
    return coast + water


#: Road widths by OSM class when the fetch gave none (m).
ROAD_WIDTH_M = {"trunk": 16.0, "primary": 14.0, "secondary": 12.0, "tertiary": 10.0,
                "residential": 7.0, "unclassified": 7.0}


def _ground_map(pano, layers: dict, osm: dict) -> fd.GroundMap:
    from shapely.geometry import shape

    roads = []
    for f in (layers.get("roads") or {}).get("features") or []:
        p = f.get("properties") or {}
        hw = p.get("highway")
        hw = str(hw[0] if isinstance(hw, list) else hw).replace("_link", "")
        roads.append((shape(f["geometry"]), float(p.get("road_width_m") or ROAD_WIDTH_M.get(hw, 6.0))))
    return fd.ground_map(
        pano.lat, pano.lon,
        coast_lines=[shape(f["geometry"]) for f in osm_water.extract_coastline_features(layers)],
        water_polys=[shape(f["geometry"]) for f in osm_water.extract_water_features(layers)],
        roads=roads,
        green=[shape(f["geometry"]) for f in (layers.get("green") or {}).get("features") or []],
        buildings=[shape(f["geometry"]) for f in osm["buildings"]["features"]])


def _position_check(seed: str, out: Path, pano, pose, layers: dict, osm: dict) -> dict:
    t = time.time()
    gmap = _ground_map(pano, layers, osm)
    fit = fd.fit_position_from_ground(pano, pose, gmap)
    grid, mis, (bx, by, bp) = fd.waterline_position_scan(pano, pose, gmap)
    c = grid.size // 2
    j = int(np.argmin(np.abs(grid - fit.dx_m)))
    i = int(np.argmin(np.abs(grid - fit.dy_m)))
    rep = {"ground_fit": fit.__dict__, "waterline_misfit_at_seed_deg": float(mis[c, c]),
           "waterline_best": {"dx_m": bx, "dy_m": by, "misfit_deg": bp.misfit_deg,
                              "camera_h_m": bp.camera_h_m, "offset_deg": bp.offset_deg},
           "waterline_misfit_at_ground_fit_deg": float(mis[i, j]),
           "grid_m": grid.tolist(), "waterline_misfit_deg": mis.round(3).tolist()}
    (out / f"{seed}_position.json").write_text(json.dumps(rep, indent=1))
    print(f"  position check ({time.time() - t:.0f}s): ground fit {fit.dx_m:+.0f} m E, {fit.dy_m:+.0f} m N "
          f"(score {fit.score:.3f} vs {fit.score_at_seed:.3f} at the seed); waterline misfit at the seed "
          f"{mis[c, c]:.3f} deg, best {bp.misfit_deg:.3f} deg at {bx:+.0f} m E, {by:+.0f} m N, "
          f"{mis[i, j]:.3f} deg at the ground fit")
    return rep


def _footprints(osm: dict) -> list[fd.Footprint]:
    out = []
    for f in osm["buildings"]["features"]:
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            ring = np.asarray(g["coordinates"][0], float)
        elif g.get("type") == "MultiPolygon":
            ring = np.asarray(max(g["coordinates"], key=lambda p: len(p[0]))[0], float)
        else:
            continue
        p = f.get("properties") or {}
        tagged = p.get("height_source") in ("osm_tag", "osm_levels")
        out.append(fd.Footprint(str(p.get("name") or ""), ring[:, :2],
                                p.get("height_m") if tagged else None))
    return out


def _pose(region: str, seed: str, out: Path, lines) -> tuple[fd.Pano, fd.PanoPose]:
    pk = out / f"{seed}_pano.pkl"
    if pk.exists():
        pano = pickle.loads(pk.read_bytes())
    else:
        pano = fd.load_seed_pano(region, seed)
        pk.write_bytes(pickle.dumps(pano))
    sp = out / f"{seed}_shore.npy"
    if sp.exists():
        shore = np.load(sp)
    else:
        shore = fd.shore_distance_table(pano.lat, pano.lon, lines)
        np.save(sp, shore)
    t = time.time()
    pose = fd.fit_pose_from_waterline(pano, shore)
    print(f"{seed}: pose offset {pose.offset_deg:.1f} deg, camera {pose.camera_h_m:.0f} m, pitch fix "
          f"{pose.pitch_fix_deg:+.2f} deg, misfit {pose.misfit_deg:.3f} deg over {pose.n_cols} cols "
          f"({time.time() - t:.0f}s)")
    (out / f"{seed}_pose.json").write_text(json.dumps(pose.__dict__, indent=1))
    _plot_waterline(pano, pose, shore, out / f"{seed}_waterline.png")
    return pano, pose


def _plot_waterline(pano, pose, shore, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y = fd.near_water_top(pano)
    cols = np.flatnonzero(np.isfinite(y))
    bear = (pano.frame_heading + pose.offset_deg) % 360
    k = np.round(bear[cols] / fd.BEARING_BIN_DEG).astype(int) % fd.N_BEARINGS
    d = np.where(shore[k] <= 4000, shore[k], np.inf)
    fig, ax = plt.subplots(2, 1, figsize=(13, 7), dpi=100)
    ax[0].scatter(bear[cols], pano.elevation_deg(y[cols]) + pose.pitch_fix_deg, s=2,
                  label="observed near-water top", color="#2e86ab")
    ax[0].scatter(bear[cols], -np.degrees(np.arctan(pose.camera_h_m / d)), s=2,
                  label=f"OSM shore, camera {pose.camera_h_m:.0f} m", color="#d1495b")
    ax[0].set_ylabel("elevation, deg")
    ax[0].legend()
    ax[0].set_xlim(0, 360)
    ax[0].grid(alpha=.3)
    ax[0].set_title(f"{pano.name}: waterline fit, offset {pose.offset_deg:.1f} deg, "
                    f"misfit {pose.misfit_deg:.3f} deg")
    ax[1].imshow(np.roll(pano.rgb, -int(np.argmin(bear)), axis=1), aspect="auto",
                 extent=(0, 360, pano.height, 0))
    ax[1].set_xlabel("bearing, deg (north = 0)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _detect(seed: str, out: Path, pano, pose, fps, device: str) -> list[fd.Measured]:
    dp = out / f"{seed}_depth.npy"
    if dp.exists():
        depth = np.load(dp)
    else:
        from city2stl.resources import free_gpu_cache, wait_for_gpu
        from city2stl.skyline.depth_estimation import predict_pano_depth_tiled

        if device == "cuda":
            wait_for_gpu(1.0)                   # Depth Anything on 518 px tiles
        depth = predict_pano_depth_tiled(pano.rgb, device=device)
        free_gpu_cache()
        np.save(dp, depth)
    t = time.time()
    ms = fd.measure_footprints(pano, pose, fps, depth=depth)
    summary = {"seed": seed, "measured": len(ms), "base_visible": sum(m.base_visible for m in ms),
               "run_ends": dict(Counter(m.top_edge for m in ms)),
               "visible_frac_median": float(np.median([m.visible_frac for m in ms])) if ms else None}
    print(f"{seed}: measured {len(ms)} footprints in {time.time() - t:.0f}s; "
          f"base visible {summary['base_visible']}; run ends {summary['run_ends']}")
    # predicted vs observed base row where the base is visible
    H = pano.height
    res = []
    for m in ms:
        if not m.base_visible:
            continue
        yb = int(round(m.base_row))
        for x in range(m.x0, m.x1 + 1, 2):
            col = np.isin(pano.labels[:, x], fd.BUILDING_CLASSES)
            trans = [y for y in range(max(1, yb - 25), min(H - 1, yb + 25)) if col[y - 1] and not col[y]]
            if trans:
                res.append(min(trans, key=lambda y: abs(y - yb)) - yb)
    if res:
        summary["base_check_px"] = {"columns": len(res), "median_abs": float(np.median(np.abs(res))),
                                    "median_signed": float(np.median(res))}
        print(f"  base check: {len(res)} columns, median |observed - predicted| "
              f"{np.median(np.abs(res)):.1f} px, signed {np.median(res):+.1f} px")
    tag = [(m.height_m, m.osm_height_m) for m in ms if m.osm_height_m]
    if tag:
        e = np.array([a - b for a, b in tag])
        summary["osm_tags"] = {"n": len(tag), "median_abs_m": float(np.median(np.abs(e))),
                               "bias_m": float(np.median(e))}
        print(f"  OSM-tagged {len(tag)}: median |photo - OSM| {np.median(np.abs(e)):.0f} m, "
              f"bias {np.median(e):+.0f} m")
    (out / f"{seed}_measured.json").write_text(
        json.dumps([m.__dict__ for m in ms], indent=0, default=float))
    (out / f"{seed}_summary.json").write_text(json.dumps(summary, indent=1))
    _plot_boxes(pano, pose, ms, out / f"{seed}_footprint_boxes.png")
    return ms


def _plot_boxes(pano, pose, ms, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    H, W = pano.labels.shape
    shift = int(np.argmin((pano.frame_heading + pose.offset_deg) % 360))
    rgb = np.roll(pano.rgb, -shift, axis=1)
    fig, axes = plt.subplots(2, 1, figsize=(26, 11), dpi=80)
    for ax, (a, b) in zip(axes, ((0, W // 2), (W // 2, W)), strict=True):
        ax.imshow(rgb[:, a:b], extent=(a, b, H, 0))
        for m in ms:
            x0 = (m.x0 - shift) % W
            if not (a <= x0 < b):
                continue
            c = "#ffd23f" if m.base_visible else "#ff6b6b"
            ax.add_patch(Rectangle((x0, m.top_row), m.x1 - m.x0, m.bottom_row - m.top_row,
                                   fill=False, edgecolor=c, lw=1.2))
            if m.height_m >= 40:
                ax.text(x0, m.top_row - 3, f"{m.height_m:.0f}", color=c, fontsize=8)
        ax.set_xlim(a, b)
        ax.set_ylim(H, 0)
    fig.suptitle(f"{pano.name}: footprint-first detection, {len(ms)} OSM footprints measured "
                 "(yellow: base visible, red: base hidden by a nearer building); labels = height m")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def compare(out: Path, seeds: list[str]) -> dict:
    """Agreement of every seed pair on the footprints both measured (``compare.json``, a
    side-by-side sheet per pair ``compare_<a>_<b>.png``) and one height per footprint from all
    seeds (``fused.json``, :func:`footprint_detect.fuse_heights`)."""
    by = {s: {m["footprint"]: m for m in json.loads((out / f"{s}_measured.json").read_text())}
          for s in seeds}
    report = {}
    for s, t in combinations(seeds, 2):
        shared = sorted(set(by[s]) & set(by[t]))
        if not shared:
            continue
        ha = np.array([by[s][k]["height_m"] for k in shared])
        hb = np.array([by[t][k]["height_m"] for k in shared])
        d = ha - hb
        rel = np.abs(d) / np.maximum(1.0, (ha + hb) / 2)
        both = [k for k in shared if by[s][k]["base_visible"] and by[t][k]["base_visible"]]
        near = [j for j, k in enumerate(shared) if max(by[s][k]["dist_m"], by[t][k]["dist_m"]) <= 1000]
        r = {"shared": len(shared), "median_abs_m": float(np.median(np.abs(d))),
             "median_signed_m": float(np.median(d)), "within_25pct": float(np.mean(rel <= 0.25)),
             "base_visible_both": len(both), "both_within_1km": len(near),
             "within_25pct_both_within_1km": float(np.mean(rel[near] <= 0.25)) if near else None}
        report[f"{s}|{t}"] = r
        print(f"{s} vs {t}: {len(shared)} shared, median |diff| {r['median_abs_m']:.1f} m, "
              f"within 25 % on {r['within_25pct']:.0%} (base visible in both: {len(both)}; "
              f"both within 1 km: {len(near)})")
        _plot_compare(out, s, t, shared, by)
    fused = fd.fuse_heights({s: list(by[s].values()) for s in seeds})
    multi = [f for f in fused.values() if len(f["seeds"]) > 1]
    tag = [f["height_m"] - f["osm_height_m"] for f in fused.values() if f["osm_height_m"]]
    print(f"fused: {len(fused)} footprints, {len(multi)} seen by 2+ seeds, "
          f"{sum(f['disputed'] for f in multi)} disputed"
          + (f"; OSM-tagged {len(tag)}: median |fused - OSM| {np.median(np.abs(tag)):.0f} m" if tag else ""))
    (out / "fused.json").write_text(json.dumps(fused, indent=0))
    (out / "compare.json").write_text(json.dumps(report, indent=1))
    return report


def _plot_compare(out: Path, s: str, t: str, shared: list[int], by: dict) -> None:
    """Scatter of the two seeds' heights, then each shared footprint in both panos."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    panos = {k: pickle.loads((out / f"{k}_pano.pkl").read_bytes()) for k in (s, t)}
    order = sorted(shared, key=lambda k: abs(by[s][k]["height_m"] - by[t][k]["height_m"])
                   / max(1.0, (by[s][k]["height_m"] + by[t][k]["height_m"]) / 2))
    fig = plt.figure(figsize=(12, 4.2 + 2.6 * len(order)), dpi=80)
    gs = fig.add_gridspec(len(order) + 1, 2, height_ratios=[1.6] + [1] * len(order))
    ax = fig.add_subplot(gs[0, :])
    ha = np.array([by[s][k]["height_m"] for k in order])
    hb = np.array([by[t][k]["height_m"] for k in order])
    ratio = np.array([by[t][k]["dist_m"] / by[s][k]["dist_m"] for k in order])
    top = max(ha.max(), hb.max()) * 1.1
    xs = np.linspace(0, top, 50)
    ax.fill_between(xs, xs * 0.75, xs * 1.25, color="#2e86ab", alpha=0.12, label="within 25 %")
    ax.plot(xs, xs, color="#2e86ab", lw=1)
    sc = ax.scatter(ha, hb, c=np.log2(ratio), cmap="coolwarm", vmin=-2, vmax=2, s=60, edgecolor="k")
    for k, x, y in zip(order, ha, hb, strict=True):
        ax.annotate(str(k), (x, y), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel(f"{s} height, m")
    ax.set_ylabel(f"{t} height, m")
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.legend(loc="upper left")
    fig.colorbar(sc, ax=ax, label=f"log2 distance ratio {t} / {s} (red: {t} farther)")
    for row, k in enumerate(order, start=1):
        for col, seed in enumerate((s, t)):
            m, pano = by[seed][k], panos[seed]
            H, W = pano.labels.shape
            a = (m["x0"] + m["x1"]) // 2 - 110
            idx = np.arange(a, a + 220) % W
            y0 = int(max(0, min(m["top_row"], m["base_row"] - 40) - 50))
            y1 = int(min(H, m["base_row"] + 25))
            axk = fig.add_subplot(gs[row, col])
            axk.imshow(pano.rgb[y0:y1][:, idx], extent=(a, a + 220, y1, y0), aspect="auto")
            for o in by[seed].values():
                if o["x1"] < a or o["x0"] > a + 220:
                    continue
                me = o["footprint"] == k
                axk.add_patch(Rectangle((o["x0"], o["top_row"]), o["x1"] - o["x0"],
                                        o["bottom_row"] - o["top_row"], fill=False,
                                        edgecolor="magenta" if me else ("#ffd23f" if o["base_visible"] else "#ff6b6b"),
                                        lw=2.2 if me else 0.6))
            axk.axhline(m["base_row"], color="cyan", lw=0.6, ls="--")
            axk.set_xticks([])
            axk.set_title(f"{seed} fp {k} {m['name'][:18]}: {m['height_m']:.0f} m at {m['dist_m']:.0f} m, "
                          f"{'base visible' if m['base_visible'] else 'base hidden'}", fontsize=8)
    fig.suptitle(f"{s} vs {t}: {len(order)} footprints both measured (magenta: the footprint; "
                 "cyan: its predicted base; best agreement first)", y=0.999)
    fig.tight_layout(rect=(0, 0, 1, 0.99))
    fig.savefig(out / f"compare_{s}_{t}.png")
    plt.close(fig)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--seeds", nargs="+", required=True, help="seed names, e.g. seed_5 seed_1")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ground-osm", "--coast-osm", dest="ground_osm",
                    help="OSM cache .json.gz with the waterways, roads and green layers")
    ap.add_argument("--position-check", action="store_true",
                    help="also fit the camera position from parks, streets and the waterline (report only)")
    ap.add_argument("--device", default=None, help="cuda or cpu (default: cuda when available)")
    a = ap.parse_args(argv)
    if a.device is None:
        import torch
        a.device = "cuda" if torch.cuda.is_available() else "cpu"
    a.out.mkdir(parents=True, exist_ok=True)
    bbox = _load_region_bbox(a.region)
    osm, src = _load_osm_for_region(bbox)
    fps = _footprints(osm)
    print(f"OSM buildings {len(fps)} ({src})")
    layers = None
    for seed in a.seeds:
        if layers is None and (a.position_check or not (a.out / f"{seed}_shore.npy").exists()):
            layers = _ground_layers(osm, bbox, a.ground_osm)
        pano, pose = _pose(a.region, seed, a.out, None if layers is None else _coast_lines(layers))
        if a.position_check:
            _position_check(seed, a.out, pano, pose, layers, osm)
        _detect(seed, a.out, pano, pose, fps, a.device)
    if len(a.seeds) > 1:
        compare(a.out, a.seeds)


if __name__ == "__main__":
    main()

