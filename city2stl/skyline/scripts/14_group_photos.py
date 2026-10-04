#!/usr/bin/env python3
"""F-WEB2 step G2: pairwise outline similarity of a region's photos, groups, and a check.

    python -m city2stl.skyline.scripts.14_group_photos --region miami [--workers 4] [--max-misfit 0.15]

Reads the outline cache (``13_photo_profiles``), scores every pair once
(``photo_groups.outline_similarity``, B onto A; the zoom range covers both directions) in a
process pool, and writes ``runs/commons_cache/<region>/pairs.json`` (every pair with misfit
<= 0.5, so the threshold can be tuned without recomputing) and ``groups.json``: direct links from unlocated to located photos and complete-linkage
groups of unlocated photos (``photo_groups.link_to_located``, ``cliques``; no chaining).

The check: for pairs where both photos carry a camera location, the distance between their
cameras against the misfit. Photos from the same spot should be close.
"""

from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from city2stl.skyline import photo_groups as pg

ROOT = Path(__file__).resolve().parents[1]
_P: dict = {}


def _init(path):
    global _P
    _P = dict(np.load(path))


def _row(args):
    ka, wa, rest = args
    out = []
    for kb, wb in rest:
        s = pg.outline_similarity(_P[ka], wa, _P[kb], wb)
        if s is not None and s.misfit <= 0.5:
            out.append((ka, kb, s.misfit, s.scale, s.shift, s.overlap))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-misfit", type=float, default=pg.SAME_SPOT_MISFIT)
    args = ap.parse_args()
    d = ROOT / "runs" / "commons_cache" / args.region
    meta = [m for m in json.loads((d / "profiles.json").read_text(encoding="utf-8")) if "px" in m]
    by = {m["key"]: m for m in meta}
    keys = [m["key"] for m in meta]
    jobs = [(ka, by[ka]["px"][0], [(kb, by[kb]["px"][0]) for kb in keys[i + 1:]])
            for i, ka in enumerate(keys)]
    pairs = []
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(d / "profiles.npz",)) as ex:
        for rows in ex.map(_row, jobs, chunksize=4):
            pairs.extend(rows)
    (d / "pairs.json").write_text(json.dumps(pairs), encoding="utf-8")

    # direct links to located photos and complete-linkage groups (no chaining)
    located = {k for k in keys if by[k]["lat"] is not None}
    links = pg.link_to_located(pairs, located, args.max_misfit)
    groups = pg.cliques(pairs, set(keys) - located, args.max_misfit)
    (d / "groups.json").write_text(json.dumps({
        "max_misfit": args.max_misfit,
        "links": {u: {"located": loc, "misfit": m} for u, (loc, m) in links.items()},
        "groups": [sorted(g) for g in groups]}, indent=0), encoding="utf-8")

    # the check: camera distance vs misfit for located pairs
    def dist(a, b):
        return math.hypot((a["lat"] - b["lat"]) * 111320,
                          (a["lon"] - b["lon"]) * 111320 * math.cos(math.radians(a["lat"])))

    loc = [(mis, dist(by[a], by[b])) for a, b, mis, *_ in pairs
           if by[a]["lat"] is not None and by[b]["lat"] is not None]
    print(f"{len(keys)} photos, {len(pairs)} pairs with misfit <= 0.5")
    for lo, hi in [(0, 0.05), (0.05, 0.1), (0.1, 0.15), (0.15, 0.25), (0.25, 0.5)]:
        ds = [x for m, x in loc if lo <= m < hi]
        if ds:
            print(f"  misfit {lo:.2f}-{hi:.2f}: {len(ds):4d} located pairs, camera distance median "
                  f"{np.median(ds):6.0f} m, share under 500 m {np.mean(np.array(ds) < 500):.0%}")
    print(f"at misfit <= {args.max_misfit}: {len(links)} unlocated photos linked to a located one; "
          f"{len(groups)} groups of unlocated photos (sizes {[len(g) for g in groups][:8]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
