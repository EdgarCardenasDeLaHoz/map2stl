#!/usr/bin/env python3
"""Stand-alone cross-view registration demo (F-SKY10 inspector).

Builds a PDF that pairs every matched segment of one Street View image
with the satellite crop of its matched OSM/MS polygon, plus the actual
pixel patches the F-SKY10 colour-consistency scorer compared. Lets you
*see* the registration the matcher is doing, without re-running the full
33-page Cartagena report.

Usage:
    PYTHONPATH=. python city2stl/skyline_cv/scripts/10_cross_view_demo.py \
        --region Cartagena --seed-index 5

One row per matched segment:
  | SV image (segment outlined) | SV roof strip | satellite poly crop |
  | RGB swatches + numerical scores                                   |

The script reuses the existing on-disk caches (image_cache,
seed_resolution_cache, satellite_image_cache) so re-running on the same
seed is fast and reproducible.

Notes:
- Designed to run against one seed view at a time; loops every 30° spin
  heading at that seed unless --heading is given.
- Outputs to ``city2stl/skyline_cv/runs/cross_view_demo/<region>_seed<i>.pdf``.
- Does NOT modify any caches the main pipeline writes (read-only on the
  satellite_image cache, append-only on image_cache).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cross-view registration inspector (F-SKY10)")
    p.add_argument("--region", required=True,
                   help="Saved region name (e.g. Cartagena)")
    p.add_argument("--seed-index", type=int, default=5,
                   help="1-based seed index from sites/<region>.json seed_urls "
                        "(default: 5 = Cartagena's Bocagrande-facing seed)")
    p.add_argument("--heading", type=float, default=None,
                   help="Single spin heading in degrees (skips full 12-view spin "
                        "if set). When omitted, demos every 30° from 0°.")
    p.add_argument("--out", default=None,
                   help="Output PDF path. Default: "
                        "runs/cross_view_demo/<region>_seed<i>.pdf")
    p.add_argument("--api-key", default=None,
                   help="Override GOOGLE_MAPS_API_KEY env var")
    p.add_argument("--max-matches-per-page", type=int, default=4,
                   help="How many segment rows per PDF page (default 4)")
    return p


def _polygon_lonlat(building) -> list[tuple[float, float]]:
    geom = getattr(building, "geometry", None)
    if geom is None:
        return []
    try:
        xs, ys = geom.exterior.xy
    except Exception:
        return []
    return [(float(x), float(y)) for x, y in zip(xs, ys)]


def _swatch(ax, rgb: tuple[float, float, float] | None, label: str) -> None:
    ax.axis("off")
    if rgb is None:
        ax.text(0.5, 0.5, "n/a", ha="center", va="center",
                transform=ax.transAxes, fontsize=8, color="grey")
        ax.set_title(label, fontsize=8)
        return
    r, g, b = rgb
    ax.add_patch(mpatches.Rectangle(
        (0, 0), 1, 1, facecolor=(r / 255.0, g / 255.0, b / 255.0),
        edgecolor="black", linewidth=0.5,
        transform=ax.transAxes,
    ))
    ax.set_title(f"{label}\nRGB={int(r)},{int(g)},{int(b)}", fontsize=8)


def _render_match_row(fig, row_y, row_h, sv_image, seg, sat_image,
                      sat_project, polygon_lonlat, score_dict):
    """Render one matched-segment row with 5 sub-panels."""
    from city2stl.skyline_cv.satellite_image import crop_polygon_from_satellite
    # 5 panels across: SV context (wide) | SV strip | sat crop | sv swatch | sat swatch
    widths = [0.30, 0.16, 0.20, 0.07, 0.07]
    x_cursor = 0.04
    pad = 0.01
    h_img, w_img = sv_image.shape[:2]

    # Panel 1: SV image with segment box overlay
    ax = fig.add_axes([x_cursor, row_y, widths[0], row_h])
    ax.imshow(sv_image)
    x_l = max(0, int(seg["x_left"]))
    x_r = min(w_img, int(seg["x_right"]))
    top_y = max(0, int(seg["top_y"]))
    base_y = min(h_img, int(seg.get("base_y", h_img)))
    ax.add_patch(mpatches.Rectangle(
        (x_l, top_y), x_r - x_l, base_y - top_y,
        fill=False, edgecolor="lime", linewidth=1.4,
    ))
    fid = (seg.get("matched_projection") or {}).get("feature_id", "?")
    is_ms = str(fid).startswith("ms_")
    src = "MS" if is_ms else "OSM"
    ax.set_title(f"SV  seg→{fid} ({src})", fontsize=8)
    ax.axis("off")
    x_cursor += widths[0] + pad

    # Panel 2: SV roof strip (the actual pixels scored)
    ax = fig.add_axes([x_cursor, row_y, widths[1], row_h])
    strip = sv_image[top_y:min(h_img, top_y + 14), x_l:x_r]
    if strip.size > 0:
        ax.imshow(strip)
        ax.set_title(f"SV roof strip {strip.shape[1]}×{strip.shape[0]}",
                     fontsize=8)
    else:
        ax.text(0.5, 0.5, "empty", ha="center", va="center",
                transform=ax.transAxes, fontsize=8, color="grey")
    ax.axis("off")
    x_cursor += widths[1] + pad

    # Panel 3: Satellite crop with polygon outline
    ax = fig.add_axes([x_cursor, row_y, widths[2], row_h])
    sat_crop = crop_polygon_from_satellite(
        sat_image, sat_project, polygon_lonlat, padding_px=6)
    if sat_crop is not None and sat_crop.size > 0:
        ax.imshow(sat_crop)
        # Polygon outline in the local crop coordinate frame
        xs_g, ys_g = zip(*[sat_project(lon, lat)
                           for lon, lat in polygon_lonlat])
        xs_min, ys_min = min(xs_g), min(ys_g)
        # crop_polygon_from_satellite pads by 6 px and clamps to image —
        # use the actual crop origin (xs_min - 6 clamped to 0) so the
        # polygon overlay registers against the displayed crop.
        x_off = max(0, int(math.floor(xs_min)) - 6)
        y_off = max(0, int(math.floor(ys_min)) - 6)
        xs_local = [x - x_off for x in xs_g]
        ys_local = [y - y_off for y in ys_g]
        ax.plot(xs_local + [xs_local[0]],
                ys_local + [ys_local[0]],
                color="lime", linewidth=1.2)
        ax.set_title(f"Sat crop {sat_crop.shape[1]}×{sat_crop.shape[0]}",
                     fontsize=8)
    else:
        ax.text(0.5, 0.5, "(polygon outside sat image)",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=8, color="grey")
        ax.set_title("Sat crop", fontsize=8)
    ax.axis("off")
    x_cursor += widths[2] + pad

    # Panel 4: SV roof strip median RGB swatch
    ax = fig.add_axes([x_cursor, row_y + 0.02, widths[3], row_h - 0.04])
    _swatch(ax, score_dict.get("sv_rgb"), "SV med")
    x_cursor += widths[3] + pad

    # Panel 5: Satellite polygon median RGB swatch
    ax = fig.add_axes([x_cursor, row_y + 0.02, widths[4], row_h - 0.04])
    _swatch(ax, score_dict.get("sat_rgb"), "Sat med")
    x_cursor += widths[4] + pad

    # Score readout (right of the swatches, below the row)
    ax = fig.add_axes(
        [x_cursor, row_y, 0.99 - x_cursor, row_h])
    ax.axis("off")
    lines = [
        f"forward_m = {seg.get('matched_projection', {}).get('forward_m', float('nan')):.0f}",
        f"intra_iou  = {score_dict.get('intra_iou', float('nan')):.3f}",
        f"w_score    = {score_dict.get('w_score',  float('nan')):.3f}",
        f"occlusion  = {score_dict.get('occlusion', float('nan')):.3f}",
        f"cv_color   = {score_dict.get('cv',       float('nan')):.3f}",
        f"intra-only = {score_dict.get('combined_intra', float('nan')):.3f}",
        f"final      = {score_dict.get('combined',       float('nan')):.3f}",
    ]
    ax.text(0.0, 1.0, "\n".join(lines), va="top", ha="left",
            family="monospace", fontsize=8)


def main() -> int:
    args = build_parser().parse_args()

    # All region_pdf helpers run client-side; importing here keeps the
    # module-level startup cost (model loaders, etc.) deferred to actual
    # invocation rather than -h / --help.
    from city2stl.skyline_cv.region_pdf import (  # type: ignore
        _resolve_api_key,
        _load_region_bbox,
        _load_osm_for_region,
        _osm_to_building_records,
        _load_site_seed_urls,
        _load_site_use_satellite_footprints,
        _parse_streetview_url,
        _streetview_image,
    )
    from city2stl.skyline_cv.pipeline import (  # type: ignore
        BuildingRecord, Viewpoint, CapturedView,
        register_view_to_osm,
        detect_building_silhouettes,
        detect_buildings_from_mask,
        match_segments_to_buildings,
        osm_anchor_silhouettes,
        _merge_silhouette_sources,
        _neural_sky_and_building_masks,
    )
    from city2stl.skyline_cv.satellite_image import fetch_region_satellite
    from city2stl.skyline_cv.cross_view import (
        make_cross_view_scorer, _median_rgb, _street_view_roof_strip,
    )

    api_key = _resolve_api_key(args.api_key)
    bbox = _load_region_bbox(args.region)
    osm_data, _ = _load_osm_for_region(bbox)
    building_records = _osm_to_building_records(osm_data)

    if _load_site_use_satellite_footprints(args.region):
        from city2stl.skyline_cv.satellite_footprints import (
            fetch_microsoft_buildings_for_bbox, merge_satellite_into_osm,
        )
        sat_polys = fetch_microsoft_buildings_for_bbox(
            (bbox.south, bbox.west, bbox.north, bbox.east))
        building_records = merge_satellite_into_osm(building_records, sat_polys)
        print(f"[demo] MS-merged building inventory = {len(building_records)}")

    sat_image, sat_project, sat_meta = fetch_region_satellite(
        (bbox.south, bbox.west, bbox.north, bbox.east), target_m_per_px=2.0,
    )
    print(f"[demo] satellite image: zoom={sat_meta['zoom']} "
          f"shape={sat_meta['shape']} tiles={sat_meta['tiles_loaded']}/{sat_meta['tiles_total']}")

    seed_urls = _load_site_seed_urls(args.region)
    if not seed_urls or args.seed_index < 1 or args.seed_index > len(seed_urls):
        print(f"[demo] invalid seed_index {args.seed_index} for "
              f"region with {len(seed_urls)} seeds; aborting.")
        return 2
    url = seed_urls[args.seed_index - 1]
    parsed = _parse_streetview_url(url)
    if parsed is None:
        print("[demo] failed to parse seed URL")
        return 3
    lat, lon, base_heading, fov, pitch, pano_id = parsed

    # The main pipeline runs a metadata-probe + image-probe dance that
    # rebinds (lat, lon, pano_id) to the Google-internal pano-id that
    # the Image Static API will actually serve. The result is persisted
    # in ``runs/seed_resolution_cache.json`` keyed by
    # ``seed_<N>|lat|lon|orig_pano_id``. If we skip that lookup here
    # the demo hits Google with the URL's original pano-id, which is
    # the Photo Sphere id — the Static Image endpoint returns 404
    # for every heading. Re-using the cache keeps the demo aligned
    # with the main pipeline's resolved seed.
    import json as _json
    _cache_path = (
        ROOT / "city2stl" / "skyline_cv" / "runs"
        / "seed_resolution_cache.json"
    )
    if _cache_path.exists():
        try:
            _cache = _json.loads(_cache_path.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
        ck = f"seed_{args.seed_index}|{lat:.6f}|{lon:.6f}|{pano_id or ''}"
        entry = _cache.get(ck)
        if entry is not None:
            lat = float(entry["lat"])
            lon = float(entry["lon"])
            pano_id = entry.get("pano_id") or pano_id
            print(f"[demo] using cached resolved pano for seed_{args.seed_index}")
    print(f"[demo] seed_{args.seed_index} resolved @ {lat:.5f},{lon:.5f} "
          f"heading={base_heading:.1f} fov={fov:.1f} pano={pano_id}")

    # Per-seed building spatial prefilter — keep it tight so the per-view
    # matcher candidate list isn't dominated by far-side-of-bbox noise.
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(lat))
    seed_buildings: list[BuildingRecord] = []
    for b in building_records:
        dx = (b.centroid_lon - lon) * mlon
        dy = (b.centroid_lat - lat) * mlat
        if dx * dx + dy * dy <= 4500.0 ** 2:
            seed_buildings.append(b)
    print(f"[demo] {len(seed_buildings)} candidate buildings within 4.5 km")
    buildings_by_id = {b.feature_id: b for b in seed_buildings}

    out_pdf = Path(args.out) if args.out else (
        ROOT / "city2stl" / "skyline_cv" / "runs" / "cross_view_demo"
        / f"{args.region}_seed{args.seed_index}.pdf"
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    headings: list[float] = (
        [float(args.heading)] if args.heading is not None
        else [float(h) for h in np.arange(0.0, 360.0, 30.0)]
    )

    with PdfPages(out_pdf) as pdf:
        for hdg in headings:
            image = _streetview_image(
                api_key, lat, lon, hdg, fov=fov, pitch=pitch,
                pano_id=pano_id, pano_only=(pano_id is not None),
            )
            # Photo-Sphere panos often refuse arbitrary heading×pitch
            # combos at the URL's literal pitch — retry once at the
            # production pipeline's standard +12° corrected pitch and
            # then at pitch=0 before giving up. Mirrors the main run's
            # any_needs_pitch_correction fallback in stripped-down form.
            if image is None:
                for fallback_pitch in (min(pitch + 12.0, 15.0), 0.0):
                    image = _streetview_image(
                        api_key, lat, lon, hdg, fov=fov,
                        pitch=fallback_pitch,
                        pano_id=pano_id,
                        pano_only=(pano_id is not None),
                    )
                    if image is not None:
                        print(f"[demo] heading {hdg:.0f}: recovered at "
                              f"pitch={fallback_pitch:+.1f}")
                        break
            if image is None:
                print(f"[demo] heading {hdg:.0f}: no image")
                continue
            cap = CapturedView(
                viewpoint=Viewpoint(
                    name=f"seed{args.seed_index}_h{int(hdg)}",
                    query="", lat=lat, lon=lon,
                    heading=hdg, pitch=pitch, fov=fov,
                    image_width=image.shape[1], image_height=image.shape[0],
                ),
                image_path=Path(""),       # demo only — register doesn't read this
                metadata_path=Path(""),    # demo only — register doesn't read this
                image=image,
            )
            reg = register_view_to_osm(
                cap, seed_buildings,
                heading_search_deg=8.0, heading_step_deg=1.0,
            )

            contour = reg.get("contour")
            if contour is None:
                print(f"[demo] heading {hdg:.0f}: no contour, skipping")
                continue
            contour_segs = detect_building_silhouettes(contour, image)
            _, bmask = _neural_sky_and_building_masks(image)
            mask_segs = detect_buildings_from_mask(
                bmask, contour=contour, image=image)
            segments = _merge_silhouette_sources(contour_segs, mask_segs)
            all_projs = reg.get("all_projections") or reg.get("projections", [])
            if int(reg.get("n_matches", 0)) >= 3:
                segments = osm_anchor_silhouettes(
                    segments, all_projs, building_mask=bmask)
            scorer = make_cross_view_scorer(sat_image, sat_project, image)
            matched = match_segments_to_buildings(
                segments, all_projs, buildings_by_id,
                cross_view_scorer=scorer,
            )
            kept = [m for m in matched if m.get("matched_projection")]
            print(f"[demo] heading {hdg:.0f}: "
                  f"{len(segments)} segs -> {len(kept)} matched")
            if not kept:
                continue

            # Paginate.
            per_page = max(1, args.max_matches_per_page)
            for page_start in range(0, len(kept), per_page):
                page_rows = kept[page_start: page_start + per_page]
                fig = plt.figure(figsize=(14, 9.5))
                fig.suptitle(
                    f"Cross-view registration demo — "
                    f"{args.region} seed_{args.seed_index} heading {hdg:.0f}°  "
                    f"({page_start+1}–{page_start+len(page_rows)} of {len(kept)})",
                    fontsize=12,
                )
                row_h = (0.86 - 0.04) / per_page * 0.92
                row_pad = (0.86 - 0.04) / per_page * 0.08
                for i, seg in enumerate(page_rows):
                    row_y = 0.04 + (per_page - 1 - i) * (row_h + row_pad)
                    fid = seg["matched_projection"]["feature_id"]
                    poly = _polygon_lonlat(buildings_by_id.get(fid))
                    # Recompute the score components for display. The
                    # matcher captures the chosen-pair combined into
                    # match_diagnostics[0]; we pull intra-only by
                    # re-scoring without the closure.
                    sv_strip = _street_view_roof_strip(image, seg)
                    sv_rgb = _median_rgb(sv_strip)
                    from city2stl.skyline_cv.satellite_image import crop_polygon_from_satellite
                    sat_crop = crop_polygon_from_satellite(
                        sat_image, sat_project, poly)
                    sat_rgb = _median_rgb(sat_crop)
                    diag = (seg.get("match_diagnostics") or [{}])[0]
                    score_dict = {
                        "sv_rgb": sv_rgb,
                        "sat_rgb": sat_rgb,
                        "intra_iou": diag.get("iou", float("nan")),
                        "w_score":   diag.get("width_score", float("nan")),
                        "occlusion": diag.get("occlusion", float("nan")),
                        "cv":        diag.get("cv", float("nan")),
                        "combined":  diag.get("combined", float("nan")),
                        # combined_intra ≈ (final - 0.15·cv) / 0.85 when cv present
                        "combined_intra": (
                            (diag.get("combined", 0.0)
                             - 0.15 * float(diag.get("cv", 0.5))) / 0.85
                            if "cv" in diag else diag.get("combined", float("nan"))
                        ),
                    }
                    _render_match_row(
                        fig, row_y, row_h, image, seg, sat_image,
                        sat_project, poly, score_dict,
                    )
                pdf.savefig(fig)
                plt.close(fig)

    print(f"[demo] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
