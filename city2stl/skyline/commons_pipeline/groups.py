"""Stage 2 (scripts 14, 15): which photos were taken from the same spot.

``score_pairs`` scores every pair of outlines once (``photo_groups.outline_similarity``, B onto A;
the zoom range covers both directions) in a process pool; ``pairs.json`` keeps every pair with
misfit <= 0.5 so the threshold can be tuned without recomputing. ``group_photos`` turns the
pairs into direct links from unlocated to located photos and complete-linkage groups of
unlocated photos (``photo_groups.link_to_located``, ``cliques``; no chaining) and writes
``groups.json``. ``gallery_page`` is the review page of those matches.
"""

from __future__ import annotations

import html
import json
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from city2stl.skyline import photo_groups as pg
from city2stl.skyline.report_render import page
from geo2stl.geo import M_PER_DEG_SPHERE

from .cache import ROOT, cache_dir, outlined, thumb_url

#: ``(key a, key b, misfit, scale, shift, overlap)``
Pair = tuple[str, str, float, float, float, float]
#: ``{unlocated key: (located key, misfit)}``
Links = dict[str, tuple[str, float]]
#: pairs with a worse misfit are not stored
PAIR_MAX_MISFIT = 0.5

_P: dict = {}


def _init(path):
    global _P
    _P = dict(np.load(path))


def _row(args):
    ka, wa, rest = args
    out = []
    for kb, wb in rest:
        s = pg.outline_similarity(_P[ka], wa, _P[kb], wb)
        if s is not None and s.misfit <= PAIR_MAX_MISFIT:
            out.append((ka, kb, s.misfit, s.scale, s.shift, s.overlap))
    return out


def score_pairs(d: Path, meta: list[dict], workers: int = 4) -> list[Pair]:
    """Every outline pair with misfit <= 0.5; ``d`` holds ``profiles.npz``."""
    by = {m["key"]: m for m in meta}
    keys = [m["key"] for m in meta]
    jobs = [(ka, by[ka]["px"][0], [(kb, by[kb]["px"][0]) for kb in keys[i + 1:]])
            for i, ka in enumerate(keys)]
    pairs: list[Pair] = []
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(d / "profiles.npz",)) as ex:
        for rows in ex.map(_row, jobs, chunksize=4):
            pairs.extend(rows)
    return pairs


def group_photos(meta: list[dict], pairs: list[Pair], max_misfit: float
                 ) -> tuple[Links, list[set[str]]]:
    """Direct links from unlocated to located photos, and complete-linkage groups of unlocated
    photos (no chaining), at ``max_misfit``."""
    keys = [m["key"] for m in meta]
    located = {m["key"] for m in meta if m["lat"] is not None}
    return pg.link_to_located(pairs, located, max_misfit), pg.cliques(pairs, set(keys) - located, max_misfit)


def write_groups(d: Path, max_misfit: float, links: Links, groups) -> None:
    (d / "groups.json").write_text(json.dumps({
        "max_misfit": max_misfit,
        "links": {u: {"located": loc, "misfit": m} for u, (loc, m) in links.items()},
        "groups": [sorted(g) for g in groups]}, indent=0), encoding="utf-8")


def camera_distance_check(pairs: list[Pair], by: dict[str, dict]) -> list[tuple[float, float]]:
    """``(misfit, camera distance in m)`` for the pairs where both photos carry a location:
    photos from the same spot should be close."""
    def dist(a, b):
        return math.hypot((a["lat"] - b["lat"]) * M_PER_DEG_SPHERE,
                          (a["lon"] - b["lon"]) * M_PER_DEG_SPHERE * math.cos(math.radians(a["lat"])))

    return [(mis, dist(by[a], by[b])) for a, b, mis, *_ in pairs
            if by[a]["lat"] is not None and by[b]["lat"] is not None]


def group_region(region: str, workers: int = 4, max_misfit: float = pg.SAME_SPOT_MISFIT) -> dict:
    """Script 14: score the pairs of ``region``'s cached outlines, write ``pairs.json`` and
    ``groups.json``, print the camera-distance check. Returns ``{pairs, links, groups}``."""
    d = cache_dir(region)
    meta = outlined(region)
    by = {m["key"]: m for m in meta}
    pairs = score_pairs(d, meta, workers)
    (d / "pairs.json").write_text(json.dumps(pairs), encoding="utf-8")
    links, groups = group_photos(meta, pairs, max_misfit)
    write_groups(d, max_misfit, links, groups)

    loc = camera_distance_check(pairs, by)
    print(f"{len(meta)} photos, {len(pairs)} pairs with misfit <= 0.5")
    for lo, hi in [(0, 0.05), (0.05, 0.1), (0.1, 0.15), (0.15, 0.25), (0.25, 0.5)]:
        ds = [x for m, x in loc if lo <= m < hi]
        if ds:
            print(f"  misfit {lo:.2f}-{hi:.2f}: {len(ds):4d} located pairs, camera distance median "
                  f"{np.median(ds):6.0f} m, share under 500 m {np.mean(np.array(ds) < 500):.0%}")
    print(f"at misfit <= {max_misfit}: {len(links)} unlocated photos linked to a located one; "
          f"{len(groups)} groups of unlocated photos (sizes {[len(g) for g in groups][:8]})")
    return {"pairs": pairs, "links": links, "groups": groups}


# ---- review page (script 15) ----------------------------------------------------------------

def _fig(m: dict, note: str = "") -> str:
    cam = (f"{m['lat']:.4f}, {m['lon']:.4f}" if m.get("lat") is not None else "no location")
    title = html.escape(m["title"][5:80])
    fov = f" · FOV {m['hfov_deg']:.0f}°" if m.get("hfov_deg") else ""
    return (f'<figure><img loading=lazy src="{html.escape(thumb_url(m["url"], 330))}" alt="">'
            f'<figcaption><b>{title}</b><br>{m.get("year") or "?"} · {cam}{fov} {note}'
            '</figcaption></figure>')


def _group_html(i: int, g, ks_meta: dict, mis: dict) -> str:
    ks = sorted(g)
    ms = ", ".join(format(mis.get((a, b), float("nan")), ".3f")
                   for j, a in enumerate(ks) for b in ks[j + 1:])
    figs = "".join(_fig(ks_meta[k]) for k in ks)
    return (f"<div class=group><h3>Group {i + 1}: {len(g)} photos, pair misfits {ms}</h3>"
            f"<div class=grid>{figs}</div></div>")


def gallery_page(region: str, meta: dict[str, dict], pairs: list[Pair], links: Links, groups,
                 max_misfit: float) -> str:
    """The review page: each unlocated photo beside the located photo it matched (with that
    photo's camera), each group of unlocated photos together, and every match's misfit."""
    located = {k for k, m in meta.items() if m["lat"] is not None}
    mis = {(a, b): m for a, b, m, *_ in pairs}
    mis.update({(b, a): m for (a, b), m in list(mis.items())})
    link_rows = "".join(
        f'<div class=pair>{_fig(meta[u], "<br><span class=tag>unlocated</span>")}'
        f'<div class=mid>misfit<br><b>{m:.3f}</b><br>→ camera</div>'
        f'{_fig(meta[loc], "<br><span class=tag2>located</span>")}</div>'
        for u, (loc, m) in sorted(links.items(), key=lambda kv: kv[1][1]))
    group_rows = "".join(_group_html(i, g, meta, mis) for i, g in enumerate(groups))
    n_unloc = len(set(meta) - located)
    css = """:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--card:#f5f5f7;--acc:#0a66c2}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#f2f2f2;--mut:#a1a1a6;--card:#1c1c1e;--acc:#4ea1ff}}
body{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;max-width:1300px;margin:0 auto;padding:16px}
h2{margin-top:28px} h3{font-size:14px;color:var(--mut)} .mut{color:var(--mut)}
figure{margin:0;background:var(--card);border-radius:8px;overflow:hidden;width:300px}
img{width:100%;height:170px;object-fit:cover;display:block;background:#888}
figcaption{padding:6px 8px;font-size:12px;color:var(--mut)} figcaption b{color:var(--fg)}
.pair{display:flex;gap:10px;align-items:center;margin:10px 0;flex-wrap:wrap} .mid{text-align:center;color:var(--mut);min-width:80px}
.grid{display:flex;gap:10px;flex-wrap:wrap} .group{margin:14px 0}
.tag,.tag2{border-radius:4px;padding:0 5px;color:#fff} .tag{background:#d1495b} .tag2{background:#2e86ab}
"""
    body = f"""<h1>{html.escape(region)}: photos taken from the same spot</h1>
<p class=mut>Skyline-outline similarity (zoom + shift + tilt), misfit &le; {max_misfit}. Located pairs at
this threshold were all within 500 m of each other. No chaining: a photo links only to a photo it
matches directly. {len(links)} of {n_unloc} unlocated photos link to a located photo; {len(groups)}
groups of unlocated photos. Please check that each pair or group really is the same viewpoint.</p>
<h2>Unlocated → located ({len(links)})</h2>{link_rows or '<p class=mut>none</p>'}
<h2>Groups of unlocated photos ({len(groups)})</h2>{group_rows or '<p class=mut>none</p>'}"""
    return page(f"{region} photo groups", css, body, lang=None, viewport=True)


def gallery_region(region: str, max_misfit: float = pg.SAME_SPOT_MISFIT, root: Path = ROOT) -> Path:
    """Script 15: regroup the cached ``pairs.json`` at ``max_misfit`` (no recomputing), write
    ``groups.json`` and ``<root>/runs/commons_review/<region>_groups.html``."""
    d = cache_dir(region)
    rows = outlined(region)
    meta = {m["key"]: m for m in rows}
    pairs = json.loads((d / "pairs.json").read_text(encoding="utf-8"))
    links, groups = group_photos(rows, pairs, max_misfit)
    write_groups(d, max_misfit, links, groups)
    out = root / "runs" / "commons_review" / f"{region}_groups.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gallery_page(region, meta, pairs, links, groups, max_misfit), encoding="utf-8")
    print(out, "|", len(links), "links,", len(groups), "groups")
    return out
