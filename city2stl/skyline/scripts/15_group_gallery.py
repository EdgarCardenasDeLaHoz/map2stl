#!/usr/bin/env python3
"""F-WEB2 step G3: review page for photo grouping (no recomputing).

    python -m city2stl.skyline.scripts.15_group_gallery --region miami [--max-misfit 0.07]

Reads ``pairs.json`` (``14_group_photos``), applies ``photo_groups.link_to_located`` and
``cliques`` at the threshold, writes ``groups.json`` and ``runs/commons_review/<region>_groups.html``:
each unlocated photo beside the located photo it matched (with that photo's camera), each group
of unlocated photos together, and every match's misfit, so a person can confirm the matches
before anything relies on them.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from city2stl.skyline import photo_groups as pg

ROOT = Path(__file__).resolve().parents[1]


def _thumb(url: str) -> str:
    # Commons thumbnails: swap the width step for a small one
    import re
    return re.sub(r"/(\d+)px-", "/330px-", url)


def _fig(m: dict, note: str = "") -> str:
    cam = (f"{m['lat']:.4f}, {m['lon']:.4f}" if m.get("lat") is not None else "no location")
    title = html.escape(m["title"][5:80])
    fov = f" · FOV {m['hfov_deg']:.0f}°" if m.get("hfov_deg") else ""
    return (f'<figure><img loading=lazy src="{html.escape(_thumb(m["url"]))}" alt="">'
            f'<figcaption><b>{title}</b><br>{m.get("year") or "?"} · {cam}{fov} {note}'
            '</figcaption></figure>')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--max-misfit", type=float, default=pg.SAME_SPOT_MISFIT)
    args = ap.parse_args()
    d = ROOT / "runs" / "commons_cache" / args.region
    meta = {m["key"]: m for m in json.loads((d / "profiles.json").read_text(encoding="utf-8"))
            if "px" in m}
    pairs = json.loads((d / "pairs.json").read_text(encoding="utf-8"))
    located = {k for k, m in meta.items() if m["lat"] is not None}
    links = pg.link_to_located(pairs, located, args.max_misfit)
    groups = pg.cliques(pairs, set(meta) - located, args.max_misfit)
    (d / "groups.json").write_text(json.dumps({
        "max_misfit": args.max_misfit,
        "links": {u: {"located": loc, "misfit": m} for u, (loc, m) in links.items()},
        "groups": [sorted(g) for g in groups]}, indent=0), encoding="utf-8")
    mis = {(a, b): m for a, b, m, *_ in pairs}
    mis.update({(b, a): m for (a, b), m in list(mis.items())})

    link_rows = "".join(
        f'<div class=pair>{_fig(meta[u], "<br><span class=tag>unlocated</span>")}'
        f'<div class=mid>misfit<br><b>{m:.3f}</b><br>→ camera</div>'
        f'{_fig(meta[loc], "<br><span class=tag2>located</span>")}</div>'
        for u, (loc, m) in sorted(links.items(), key=lambda kv: kv[1][1]))
    def _group(i, g):
        ks = sorted(g)
        ms = ", ".join(format(mis.get((a, b), float("nan")), ".3f")
                       for j, a in enumerate(ks) for b in ks[j + 1:])
        figs = "".join(_fig(meta[k]) for k in ks)
        return (f"<div class=group><h3>Group {i + 1}: {len(g)} photos, pair misfits {ms}</h3>"
                f"<div class=grid>{figs}</div></div>")

    group_rows = "".join(_group(i, g) for i, g in enumerate(groups))
    n_unloc = len(set(meta) - located)
    page = f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{args.region} photo groups</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--card:#f5f5f7;--acc:#0a66c2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#f2f2f2;--mut:#a1a1a6;--card:#1c1c1e;--acc:#4ea1ff}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;max-width:1300px;margin:0 auto;padding:16px}}
h2{{margin-top:28px}} h3{{font-size:14px;color:var(--mut)}} .mut{{color:var(--mut)}}
figure{{margin:0;background:var(--card);border-radius:8px;overflow:hidden;width:300px}}
img{{width:100%;height:170px;object-fit:cover;display:block;background:#888}}
figcaption{{padding:6px 8px;font-size:12px;color:var(--mut)}} figcaption b{{color:var(--fg)}}
.pair{{display:flex;gap:10px;align-items:center;margin:10px 0;flex-wrap:wrap}} .mid{{text-align:center;color:var(--mut);min-width:80px}}
.grid{{display:flex;gap:10px;flex-wrap:wrap}} .group{{margin:14px 0}}
.tag,.tag2{{border-radius:4px;padding:0 5px;color:#fff}} .tag{{background:#d1495b}} .tag2{{background:#2e86ab}}
</style></head><body>
<h1>{html.escape(args.region)}: photos taken from the same spot</h1>
<p class=mut>Skyline-outline similarity (zoom + shift + tilt), misfit &le; {args.max_misfit}. Located pairs at
this threshold were all within 500 m of each other. No chaining: a photo links only to a photo it
matches directly. {len(links)} of {n_unloc} unlocated photos link to a located photo; {len(groups)}
groups of unlocated photos. Please check that each pair or group really is the same viewpoint.</p>
<h2>Unlocated → located ({len(links)})</h2>{link_rows or '<p class=mut>none</p>'}
<h2>Groups of unlocated photos ({len(groups)})</h2>{group_rows or '<p class=mut>none</p>'}
</body></html>"""
    out = ROOT / "runs" / "commons_review" / f"{args.region}_groups.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(out, "|", len(links), "links,", len(groups), "groups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
