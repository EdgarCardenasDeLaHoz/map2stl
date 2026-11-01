"""Part of ``city2stl.skyline._region_render._pages`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .._core.types import BuildingRecord
from ..region_data import _distance_m


def _load_known_heights(
    region_name: str,
    buildings: list[BuildingRecord],
    match_radius_m: float = 60.0,
) -> list[dict]:
    """Load surveyed building heights from sites/<region>.json.

    Reads the ``known_heights_m`` dict (keyed by building name, values with
    lat/lon/height_m/floors) and matches each entry to the closest
    BuildingRecord within ``match_radius_m``.  Used for CTBUH-style
    ground-truth validation separate from crowd-sourced OSM tags.

    Returns a list of dicts with keys:
      name, ctbuh_m, floors, lat, lon, matched_id, matched_dist_m.
    """
    # sites/ is beside the skyline package, one level up from _region_render/ (the split left
    # this pointing at _region_render/sites, so no report had a known-heights table)
    cfg = Path(__file__).resolve().parent.parent / "sites" / f"{region_name.lower()}.json"
    if not cfg.exists():
        return []
    try:
        # utf-8-sig: some sites files carry a BOM, and a plain utf-8 read
        # fails on it into the silent `return []` below.
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    raw = data.get("known_heights_m")
    if not isinstance(raw, dict):
        return []

    from shapely.geometry import mapping

    from ..benchmark import match_known_tower

    rows = []
    for b in buildings:
        g = b.geometry
        g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda q: q.area)
        rows.append({"feature_id": b.feature_id, "name": b.name, "centroid_lat": b.centroid_lat,
                     "centroid_lon": b.centroid_lon, "height_tag_m": b.height_tag_m,
                     "footprint_lonlat": mapping(g)["coordinates"][0]})
    out: list[dict] = []
    for bname, info in raw.items():
        if bname.startswith("_"):
            continue
        try:
            klat = float(info["lat"])
            klon = float(info["lon"])
            ctbuh_m = float(info["height_m"])
            floors = int(info.get("floors", 0))
        except Exception:
            continue
        # tower-aware: a podium mapped around its tower is not the tower (benchmark.match_known_tower)
        best, _why = match_known_tower(klat, klon, ctbuh_m, bname, rows, match_radius_m)
        best_id = best["feature_id"] if best is not None else None
        best_dist = (_distance_m(klat, klon, best["centroid_lat"], best["centroid_lon"])
                     if best is not None else float("inf"))
        out.append({
            "name": bname,
            "ctbuh_m": ctbuh_m,
            "floors": floors,
            "lat": klat,
            "lon": klon,
            "matched_id": best_id if best_dist <= match_radius_m else None,
            "matched_dist_m": best_dist,
        })
    return out


def _row_ring(row: dict, records_by_id: dict | None):
    """A row's footprint ring as (lon, lat) pairs: ``footprint_lonlat`` (heights.json rows) or
    its record's exterior; None when neither exists."""
    ring = row.get("footprint_lonlat")
    if ring:
        return ring
    rec = (records_by_id or {}).get(row.get("feature_id"))
    g = getattr(rec, "geometry", None)
    if g is None:
        return None
    try:
        g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda q: q.area)
        return list(g.exterior.coords)
    except Exception:
        return None


def _draw_tier_map(ax, rows: list[dict], records_by_id: dict | None = None,
                   title: str | None = "Published heights by verification tier") -> bool:
    """Footprints filled by verification tier (``tier_display`` colours, Okabe-Ito), best tier
    drawn last so it stays on top, with a legend of tier counts (F-SKY26 2f). A row without a
    footprint is a dot at its centroid. Returns False when nothing could be drawn."""
    from matplotlib.collections import PolyCollection
    from matplotlib.patches import Patch

    from ..tier_display import TIER_COLORS, TIER_LABELS, TIERS, row_tier, tier_counts

    order = ["unlabelled", *reversed(TIERS)]  # best tier drawn last, on top
    polys: dict[str, list] = {t: [] for t in order}
    dots: dict[str, list] = {t: [] for t in order}
    for r in rows:
        t = row_tier(r)
        ring = _row_ring(r, records_by_id)
        if ring and len(ring) >= 3:
            polys[t].append([(float(x), float(y)) for x, y in ring])
        elif r.get("centroid_lon") is not None and r.get("centroid_lat") is not None:
            dots[t].append((float(r["centroid_lon"]), float(r["centroid_lat"])))
    pts = [p for t in order for poly in polys[t] for p in poly] + [p for t in order
                                                                    for p in dots[t]]
    if not pts:
        return False
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    for t in order:
        if polys[t]:
            ax.add_collection(PolyCollection(polys[t], facecolors=TIER_COLORS[t],
                                             edgecolors=TIER_COLORS[t], linewidths=0.6))
        if dots[t]:
            ax.scatter([d[0] for d in dots[t]], [d[1] for d in dots[t]], s=10,
                       color=TIER_COLORS[t], edgecolors="none")
    pad_x = max(1e-4, 0.03 * float(xs.max() - xs.min()))
    pad_y = max(1e-4, 0.03 * float(ys.max() - ys.min()))
    ax.set_xlim(float(xs.min()) - pad_x, float(xs.max()) + pad_x)
    ax.set_ylim(float(ys.min()) - pad_y, float(ys.max()) + pad_y)
    ax.set_aspect(1.0 / max(0.1, float(np.cos(np.radians(float(ys.mean()))))))
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#bbbbbb")
    counts = tier_counts(rows)
    ax.legend(handles=[Patch(facecolor=TIER_COLORS[t], edgecolor=TIER_COLORS[t],
                             label=f"{TIER_LABELS[t]} ({n})") for t, n in counts.items()],
              loc="upper left", bbox_to_anchor=(0.0, -0.01), ncol=3, fontsize=8,
              frameon=False, title="Verification tier", title_fontsize=8,
              alignment="left")
    if title:
        ax.set_title(title, fontsize=10)
    return True


def render_tier_map_png(path: Path, rows: list[dict], records_by_id: dict | None = None,
                        size_in: float = 6.0) -> bool:
    """``_draw_tier_map`` as a PNG (the HTML report's headline map). False when no footprint."""
    fig, ax = plt.subplots(figsize=(size_in, size_in))
    try:
        if not _draw_tier_map(ax, rows, records_by_id):
            return False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=130, bbox_inches="tight")
        return True
    finally:
        plt.close(fig)


def _render_heights_page(pdf, building_heights: list[dict],
                         building_records: list[BuildingRecord] | None,
                         pano_only: bool = False) -> None:
    """The "Seed-Derived Building Heights" page: footprints coloured by verification tier with
    a legend (F-SKY26 2f), the tier counts, the cross-seed agreement metric and (unless
    ``pano_only``) the best cross-seed rows; survey licence lines at the foot when survey rows
    exist. Always written now that it carries a map (it was text-only and skipped under
    ``pano_only``)."""
    from ..tier_display import (
        TIER_LABELS,
        TIER_SHORT,
        VERIFIED_TIERS,
        drone_disagreements,
        drone_seeds,
        height_attributions,
        row_tier,
        tier_counts,
        with_unmeasured_tags,
    )

    records_by_id = {b.feature_id: b for b in (building_records or ())}
    rows = with_unmeasured_tags(building_heights, building_records or ())
    measured = [r for r in building_heights if r.get("measured", True) is not False]
    fig = plt.figure(figsize=(11, 8.5))
    fig.suptitle("Seed-Derived Building Heights", fontsize=16, fontweight="bold")
    ax_map = fig.add_axes([0.02, 0.16, 0.50, 0.76])
    if not _draw_tier_map(ax_map, rows, records_by_id, title=None):
        ax_map.axis("off")
        ax_map.text(0.5, 0.5, "No footprints to map.", ha="center", va="center")
    ax = fig.add_axes([0.55, 0.10, 0.43, 0.80])
    ax.axis("off")

    counts = tier_counts(rows)
    total = sum(counts.values()) or 1
    n_ver = sum(counts.get(t, 0) for t in VERIFIED_TIERS)
    lines = [f"Published heights: {sum(counts.values())}",
             f"  survey: {counts.get('survey', 0)}, corroborated: {counts.get('corroborated', 0)}"
             f" ({100.0 * n_ver / total:.0f} % of all)",
             f"  unverified: {total - n_ver}", ""]
    lines += [f"  {TIER_LABELS[t]:<22}{n:5d}  {100.0 * n / total:4.0f} %"
              for t, n in counts.items()]
    cross_seed = [r for r in measured if r.get("n_seeds", 1) >= 2]
    single_seed = [r for r in measured if r.get("n_seeds", 1) < 2]
    dis = np.asarray([r.get("seed_disagreement_m", 0.0) or 0.0 for r in cross_seed],
                     dtype=np.float32)
    lines += ["", f"Seen by a seed: {len(measured)}",
              f"  cross-seed (>=2 seeds): {len(cross_seed)}",
              f"  single-seed only:       {len(single_seed)}", "",
              "Cross-seed spread (diagnostic only; it does not track error):"]
    if dis.size:
        lines += [f"  median {float(np.median(dis)):5.1f} m   p90 "
                  f"{float(np.percentile(dis, 90)):5.1f} m   max {float(dis.max()):5.1f} m"]
    else:
        lines.append("  (no buildings seen from >= 2 seeds yet)")
    dd = drone_disagreements(measured, drone_seeds(rows))
    lines.append("Drone seeds only (trusted readings; spread is a diagnostic):")
    if dd:
        lines.append(f"  median {float(np.median(dd)):5.1f} m   p90 "
                     f"{float(np.percentile(dd, 90)):5.1f} m   max {dd[-1]:5.1f} m  (n {len(dd)})")
    else:
        lines.append("  (no building read by >= 2 drone seeds)")
    if not pano_only:
        lines += ["", "Cross-seed rows (spread d = diagnostic, views v = reliability signal):"]
        for r in sorted(cross_seed, key=lambda r: (r.get("seed_disagreement_m", 0.0) or 0.0,
                                                   -r.get("n_seeds", 0)))[:18]:
            h = r.get("effective_height_m", r.get("weighted_height_m"))
            lines.append(f"  {str(r.get('name'))[:16]:<16} {float(h or 0):5.0f} m  "
                         f"{TIER_SHORT[row_tier(r)]:<9}"
                         f"v={int(r.get('n_views') or 0):2d} "
                         f"d={float(r.get('seed_disagreement_m', 0.0) or 0.0):4.0f}")
    ax.text(0.0, 1.0, "\n".join(lines), va="top", ha="left", family="monospace", fontsize=8.5)
    attributions = height_attributions(rows)
    if attributions:
        fig.text(0.02, 0.03, "\n".join(attributions), fontsize=7.5, va="bottom", color="#333333")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)
