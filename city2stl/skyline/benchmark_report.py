"""Benchmark page for a skyline HTML report (F-SKYBENCH / F-WEB2).

Writes ``<report>/benchmark.html`` and links it from the report's ``index.html``:
- scores against surveyed truth (overall, height bands, tagged vs untagged, relative order,
  per verification tier: ``score['tiers']``, ``benchmark.score_by_tier``);
- predicted vs true heights (scatter) and a map of buildings coloured by signed error;
- photos (optional ``photos.json`` beside the report): each Commons photo with its camera
  (recorded or solved), FOV, attribution and the tower heights measured from it. A kept photo
  shows, as the seed pages do, the photo with its towers numbered beside a map of the camera
  and its view (``photo_cards``); the numbers are the rows of its table.

    write_benchmark_page(report_dir, score, buildings, truth, photos=None)

``score`` is one result row of ``scripts/10_benchmark.py`` (``benchmark.score_buildings`` plus
region); ``buildings`` and ``truth`` as in ``benchmark.load_report`` / ``footprint_truth``.
Colours are set on the page itself (light and dark), unlike the older report pages whose
tables went unreadable in a dark browser.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

import numpy as np

LINK_MARK = "<!-- benchmark-link -->"


def _fmt(v, spec=".1f", none="–"):
    return none if v is None else format(v, spec)


def _rows(d: dict, label: str) -> str:
    out = []
    for k, v in d.items():
        if not v or not v.get("n"):
            continue
        out.append(f"<tr><td>{html.escape(label)} {html.escape(str(k))}</td><td>{v['n']}</td>"
                   f"<td>{_fmt(v.get('mae_m'))}</td><td>{_fmt(v.get('median_ae_m'))}</td>"
                   f"<td>{_fmt(v.get('bias_m'), '+.1f')}</td>"
                   f"<td>{_fmt(v.get('within_25pct') and 100 * v['within_25pct'], '.0f')}%</td></tr>")
    return "".join(out)


def _plots(report_dir: Path, buildings: list[dict], truth: dict) -> tuple[str, str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    pts, polys, errs = [], [], []
    for b in buildings:
        t = truth.get(b["key"])
        if not t or t["status"] != "confirmed":
            continue
        tagged = b.get("height_tag_m") is not None and b.get("height_source") not in (None, "default")
        pts.append((t["truth_m"], b["effective_height_m"], tagged))
        polys.append(np.asarray(b["footprint_lonlat"])[:, :2])
        errs.append(b["effective_height_m"] - t["truth_m"])
    assets = report_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6, 6), dpi=110)
    if pts:
        a = np.array([(p[0], p[1]) for p in pts])
        tg = np.array([p[2] for p in pts])
        lim = float(max(a.max(), 50)) * 1.05
        ax.plot([0, lim], [0, lim], color="#888", lw=1)
        ax.scatter(a[~tg, 0], a[~tg, 1], s=14, color="#d1495b", label="untagged in OSM", alpha=0.8)
        ax.scatter(a[tg, 0], a[tg, 1], s=14, color="#2e86ab", label="OSM height tag", alpha=0.8)
        ax.set_xlim(0, lim)
        ax.set_ylim(0, lim)
        ax.legend(loc="upper left")
    ax.set_xlabel("true height (survey + 3D Tiles), m")
    ax.set_ylabel("pipeline height, m")
    ax.set_title("Confirmed buildings")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(assets / "bench_scatter.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 6), dpi=110)
    if polys:
        pc = PolyCollection(polys, array=np.clip(errs, -120, 120), cmap="RdBu_r",
                            edgecolors="none")
        pc.set_clim(-120, 120)
        ax.add_collection(pc)
        allxy = np.concatenate(polys)
        ax.set_xlim(allxy[:, 0].min() - 0.003, allxy[:, 0].max() + 0.003)
        ax.set_ylim(allxy[:, 1].min() - 0.003, allxy[:, 1].max() + 0.003)
        fig.colorbar(pc, ax=ax, label="pipeline − truth, m (red: too tall)")
    ax.set_aspect(1 / math.cos(math.radians(float(np.mean([p[:, 1].mean() for p in polys]))))
                  if polys else "equal")
    ax.set_title("Error by building")
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(assets / "bench_map.png")
    plt.close(fig)
    return "assets/bench_scatter.png", "assets/bench_map.png"


def _agree(t: dict) -> str:
    """How many of the other photos that read this tower agree with this reading."""
    return f"{t.get('confirmed_by', 0)} of {t['others']} agree" if t.get("others") else "–"


def _shared_html(ps: dict | None) -> str:
    """Photos grouped by the buildings they show, and every building two or more photos read,
    so a reader can check that photos claiming the same building agree on it."""
    if not ps or not ps.get("shared_buildings"):
        return ""

    def link(key, title, label=None):
        lab = f" #{label}" if label is not None else ""
        return f'<a href="#photo-{html.escape(key)}">{html.escape(title[:40])}{lab}</a>'

    groups = "".join(
        f"<tr><td>{n}</td><td>{len(g['photos'])}</td><td>{g['towers']}</td>"
        f"<td>{g['confirmed']} of {g['readings']}</td><td>{g['contradicted']}</td>"
        f"<td class=photos>{', '.join(link(k, t) for k, t in zip(g['photos'], g['titles'], strict=True))}</td></tr>"
        for n, g in enumerate(ps.get("groups") or [], 1) if len(g["photos"]) > 1)
    builds = "".join(
        f"<tr><td>{html.escape(b['name'] or str(b['tower']))}</td><td>{len(b['reads'])}</td>"
        f"<td class=photos>{'; '.join(('<s>' if x['flag'] else '') + _fmt(x['photo_m']) + ' m (' + link(x['photo'], x['title'], x['label']) + ')' + ('</s>' if x['flag'] else '') for x in b['reads'])}</td>"
        f"<td>{_fmt(b['photo_m'])}</td><td>{b['confirmed']} of {len(b['reads'])}</td>"
        f"<td>{_fmt(b['osm_m'])}</td><td>{_fmt(b['truth_m'])}</td></tr>"
        for b in ps["shared_buildings"])
    return f"""<h2>Photos of the same buildings</h2>
<p class=mut>Photos are grouped when they read at least four of the same towers. A reading
agrees with another photo's when they lie within max(5 m, 15 %). Readings that two or more other
photos contradict are struck through and not used. On Miami and Chicago, readings another
photo confirmed were more than 25 % off in 11 % of cases, contradicted ones in 62 %.</p>
<table class=groups><tr><th>group</th><th>photos</th><th>towers in 2+ photos</th><th>readings agreeing</th><th>contradicted</th><th>photos</th></tr>{groups}</table>
<h3>Buildings read by several photos</h3>
<table class=groups><tr><th>building</th><th>photos</th><th>readings (photo #number)</th><th>consensus m</th><th>agreeing</th><th>OSM m</th><th>truth m</th></tr>{builds}</table>"""


def _photos_html(photos: list[dict]) -> str:
    if not photos:
        return "<p class=mut>No photos for this run.</p>"
    cards = []
    for p in photos:
        cam = p.get("camera") or {}
        towers = sorted(p.get("towers") or [],
                        key=lambda t: (t.get("label") is None, t.get("label") or 0))
        # a flagged reading (photo_heights.reading_flag) measured another building: greyed, with
        # the reason, and left out of the heights
        rows = "".join(
            f"<tr{' class=flag' if t.get('flag') else ''}><td>{'' if t.get('label') is None else t['label']}</td>"
            f"<td>{html.escape(t['name'] or str(t.get('tower', '')))}</td><td>{_fmt(t.get('dist_m'), '.0f')}</td>"
            f"<td>{_fmt(t.get('photo_m'))}</td><td>{_fmt(t.get('osm_m'))}</td><td>{_fmt(t.get('truth_m'))}</td>"
            f"<td>{_fmt(None if t.get('photo_m') is None or t.get('truth_m') is None else t['photo_m'] - t['truth_m'], '+.1f')}</td>"
            f"<td>{_agree(t)}</td><td>{html.escape(t.get('flag') or '')}</td></tr>"
            for t in towers)
        score = p.get("score") or {}
        seen = [t for t in towers if t.get("others")]
        cross = (f"<p><b>Other photos</b> read {len(seen)} of these towers; "
                 f"{sum(1 for t in seen if t.get('confirmed_by'))} agree with this photo</p>") if seen else ""
        info = f"""<h3>{html.escape(p.get('title', ''))}</h3>
<p class=mut>{html.escape(p.get('attribution', ''))}</p>
<p><b>Camera</b> {html.escape(cam.get('source', ''))}: {_fmt(cam.get('lat'), '.5f')}, {_fmt(cam.get('lon'), '.5f')}
· heading {_fmt(cam.get('heading_deg'), '.0f')}° · FOV {_fmt(cam.get('hfov_deg'), '.0f')}°
{('· ±' + _fmt(cam.get('sigma_m'), '.0f') + ' m') if cam.get('sigma_m') is not None else ''}
<a href="https://www.openstreetmap.org/?mlat={cam.get('lat')}&mlon={cam.get('lon')}#map=15/{cam.get('lat')}/{cam.get('lon')}">map</a></p>
<p><b>Status</b> {html.escape(p.get('status', ''))}</p>
{f"<p><b>Towers measured</b> {score.get('n', 0)} confirmed · pair order {_fmt(score.get('pair_order') and 100 * score['pair_order'], '.0f')}% · MAE {_fmt(score.get('mae_m'))} m</p>" if score else ''}
{cross}
{f'<table class=towers><tr><th>#</th><th>tower</th><th>dist m</th><th>photo m</th><th>OSM m</th><th>truth m</th><th>error</th><th>other photos</th><th>not used</th></tr>{rows}</table>' if rows else ''}"""
        if p.get("overlay"):
            # the seed-page style: the photo full width with its towers numbered, then the
            # camera's map beside the numbered table
            cards.append(f"""<div class="card wide" id="photo-{html.escape(str(p.get('key', '')))}">
<a href="{html.escape(p.get('page', ''))}"><img src="{html.escape(p['overlay'])}" alt="photo with the OSM towers numbered"></a>
<p class="mut small">Numbers = table rows. Solid line: roof measured in the photo; dashed: roof the
OSM height predicts; white: the photo's skyline outline. Grey: a reading not used (column "not used"):
hidden behind something in front, far from its OSM height (it measured another building), low on
the horizon, or contradicted by the other photos of that building. Map: camera (triangle), view
cone, the same towers.</p>
<div class=pair><img src="{html.escape(p['map'])}" alt="camera location, view and towers"><div>{info}</div></div>
</div>""")
        else:
            cards.append(f"""<div class="card">
<a href="{html.escape(p.get('page', ''))}"><img src="{html.escape(p.get('thumb', ''))}" alt=""></a>
{info}
</div>""")
    return "<div class=cards>" + "".join(cards) + "</div>"


def _photo_summary_html(ps: dict | None) -> str:
    if not ps:
        return ""
    sb = ps.get("same_buildings", {})

    def row(label, d):
        if not d or not d.get("n"):
            return ""
        return (f"<tr><td>{label}</td><td>{d['n']}</td><td>{_fmt(d.get('mae_m'))}</td>"
                f"<td>{_fmt(d.get('bias_m'), '+.1f')}</td>"
                f"<td>{_fmt(d.get('pair_order') and 100 * d['pair_order'], '.0f')}%</td>"
                f"<td>{_fmt(d.get('spearman'), '.2f')}</td></tr>")

    kept = ps.get("kept", {})
    tried = ps.get("photos", {})
    routes = " · ".join(f"{k} {kept.get(k, 0)}/{tried.get(k, 0)}" for k in tried)
    return f"""<h2>Heights from photos</h2>
<p class=mut>Photos kept by route (kept/tried): {routes}. {ps.get('buildings_measured', 0)} buildings
measured from photos, {ps.get('confirmed', 0)} with confirmed truth. Each building: median over
the photos that measured it.</p>
<table><tr><th>source</th><th>n</th><th>MAE m</th><th>bias m</th><th>pairs in order</th><th>Spearman</th></tr>
{row('photos, all confirmed', ps.get('photo_vs_truth'))}
{row('photos, buildings two photos agree on', ps.get('photo_vs_truth_confirmed'))}
{row('photos, buildings only one photo read', ps.get('photo_vs_truth_single'))}
{row('OSM height tags, same buildings', ps.get('osm_vs_truth'))}
{row('photos, same buildings as Street View', sb.get('photo'))}
{row('OSM height tags, those buildings', sb.get('osm'))}
{row('Street View, those buildings', sb.get('street_view'))}</table>
<p class=mut>Every tower measured from photos has an OSM height (the outline model uses OSM-tagged
towers), so OSM tags are the yardstick photos must beat to add information.</p>
{_readings_html(ps.get('readings'))}"""


def _tiers_html(tiers: dict | None) -> str:
    """Scores per verification tier (F-SKY26): within 25 % should fall in tier order."""
    if not tiers:
        return ""
    return f"""<h2>By verification tier</h2>
<table><tr><th>tier</th><th>n</th><th>MAE m</th><th>median AE m</th><th>bias m</th><th>within 25 %</th></tr>
{_rows(tiers, '')}</table>
<p class=mut>survey: the survey lidar height, scored against 3D Tiles only; verified_2: two
independent readings agree; tag: the OSM tag; single: one reading, unverified; prior: no reading,
the height prior.</p>"""


def _readings_html(rd: dict | None) -> str:
    if not rd:
        return ""
    why = " · ".join(f"{html.escape(k)} {v}" for k, v in (rd.get("not_used") or {}).items())
    return (f"<p class=mut>Tower readings used: {rd.get('used', 0)}"
            f"{'; not used: ' + why if why else ''} (see the photos below).</p>")


def write_benchmark_page(report_dir: str | Path, score: dict, buildings: list[dict],
                         truth: dict, photos: list[dict] | None = None,
                         photo_summary: dict | None = None) -> Path:
    report_dir = Path(report_dir)
    if photos is None and (report_dir / "photos.json").exists():
        photos = json.loads((report_dir / "photos.json").read_text(encoding="utf-8"))
    scatter, mapimg = _plots(report_dir, buildings, truth)
    o = score.get("overall", {})
    rel = score.get("relative", {})
    st = score.get("status", {})
    region = html.escape(str(score.get("region", "")))
    page = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{region} benchmark</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--card:#f5f5f7;--line:#d2d2d7;--acc:#0a66c2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#f2f2f2;--mut:#a1a1a6;--card:#1c1c1e;--line:#3a3a3c;--acc:#4ea1ff}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px}}
a{{color:var(--acc)}} .mut{{color:var(--mut)}} h1{{font-size:24px}} h2{{margin-top:32px;font-size:19px}}
table{{border-collapse:collapse;margin:8px 0;font-size:14px}} th,td{{border:1px solid var(--line);padding:4px 8px;text-align:right}}
th{{background:var(--card)}} td:first-child,th:first-child{{text-align:left}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap}} .kpi{{background:var(--card);border-radius:10px;padding:10px 14px;min-width:120px}}
.kpi b{{display:block;font-size:22px}} .plots{{display:flex;gap:12px;flex-wrap:wrap}} .plots img{{max-width:100%;background:#fff;border-radius:8px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.card{{background:var(--card);border-radius:10px;padding:10px}} .card img{{width:100%;border-radius:6px}} .card h3{{font-size:15px;margin:8px 0 4px}}
.card.wide{{grid-column:1/-1}} .pair{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:14px;align-items:start}} .small{{font-size:13px}}
.pair img{{background:#fff}} .towers td:nth-child(2),.towers th:nth-child(2){{text-align:left}} .towers tr.flag td{{color:var(--mut)}}
.groups td.photos{{text-align:left;font-size:13px}} .groups s{{color:var(--mut)}}
@media (max-width:760px){{.pair{{grid-template-columns:1fr}}}}
</style></head><body>
<p><a href="index.html">← {region} report</a></p>
<h1>{region}: heights against surveyed truth</h1>
<p class=mut>Truth per footprint = survey lidar and Google 3D Tiles agreeing within max(3 m, 10 %).
Only confirmed buildings are scored. Run: {html.escape(str(score.get('report', '')))}</p>
<div class=kpis>
<div class=kpi>confirmed<b>{o.get('n', 0)}</b><span class=mut>of {score.get('n_buildings', 0)} scored, {st.get('disputed', 0)} disputed</span></div>
<div class=kpi>mean error<b>{_fmt(o.get('mae_m'))} m</b><span class=mut>bias {_fmt(o.get('bias_m'), '+.1f')} m</span></div>
<div class=kpi>within 25 %<b>{_fmt(o.get('within_25pct') and 100 * o['within_25pct'], '.0f')}%</b></div>
<div class=kpi>pairs in right order<b>{_fmt((rel.get('per_view') or {}).get('pair_order') and 100 * rel['per_view']['pair_order'], '.0f')}%</b><span class=mut>inside one view (chance 50 %)</span></div>
</div>
<h2>Scores</h2>
<table><tr><th>group</th><th>n</th><th>MAE m</th><th>median AE m</th><th>bias m</th><th>within 25 %</th></tr>
{_rows({'all': o}, '')}{_rows(score.get('osm_tag', {}), 'OSM')}{_rows(score.get('bands', {}), 'truth')}{_rows(score.get('views', {}), 'views')}</table>
<p class=mut>OSM-tagged buildings are helped by the tag filter (estimates far from the tag are
dropped); untagged ones show the pipeline unaided.</p>
{_tiers_html(score.get('tiers'))}
<h2>Predicted vs true</h2>
<div class=plots><img src="{scatter}" alt="scatter"><img src="{mapimg}" alt="error map"></div>
{_photo_summary_html(photo_summary)}
{_shared_html(photo_summary)}
<h2>Photos</h2>
<p class=mut>Wikimedia Commons skyline photos (F-WEB2). Camera from the photo's own location, a
solved pose, or labels; towers measured by identify-then-measure, tilt and camera height fitted
leave-one-out on the other towers' OSM heights.</p>
{_photos_html(photos or [])}
</body></html>"""
    out = report_dir / "benchmark.html"
    out.write_text(page, encoding="utf-8")
    _link_from_index(report_dir)
    return out


def _link_from_index(report_dir: Path) -> None:
    idx = report_dir / "index.html"
    if not idx.exists():
        return
    s = idx.read_text(encoding="utf-8")
    if LINK_MARK in s:
        return
    link = (f'{LINK_MARK}<p style="background:#fff3cd;color:#222;padding:8px 12px;border-radius:6px">'
            f'<a href="benchmark.html" style="color:#0a3070;font-weight:600">Benchmark against '
            f'surveyed truth, and Commons photos →</a></p>')
    i = s.find("</h1>")
    s = s[:i + 5] + link + s[i + 5:] if i >= 0 else link + s
    idx.write_text(s, encoding="utf-8")
