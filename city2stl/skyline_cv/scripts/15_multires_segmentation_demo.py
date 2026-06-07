#!/usr/bin/env python3
"""Multi-resolution coarse-to-fine SegFormer demo on one seed's pano.

Pipeline:
  1. Capture the 12 spin views for one seed (uses the existing on-disk
     image cache when present).
  2. Stitch all 12 RGB views into a 360 deg panorama.
  3. Coarse pass: run SegFormer at low input resolution (default 256 px)
     on the full pano. Cheap one-shot inference.
  4. Identify connected x-clusters of building columns in the coarse mask.
  5. Crop the pano to each cluster's bounding box.
  6. Fine pass: run SegFormer at full 512 px input on each crop.
  7. Re-project the fine masks back into pano coordinates and write all
     artifacts to ``runs/multires/<region>_<seed>/``.

Usage (from the strm2stl/ directory):

    PYTHONPATH=. python city2stl/skyline_cv/scripts/15_multires_segmentation_demo.py \
        --region Cartagena --seed seed_1

The script is a self-contained smoke test of the multires idea. No
matcher integration; the outputs are PNGs you can inspect.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

# Path bootstrap so "PYTHONPATH=. python scripts/15..." works from any cwd.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from city2stl.skyline_cv.region_pdf import (  # noqa: E402
    SkylinePoint,
    _capture_pano_views,
    _load_site_seed_urls,
    _parse_streetview_url,
    _resolve_api_key,
)
from city2stl.skyline_cv.pipeline import (  # noqa: E402
    _ADE20K_BUILDING_CLASSES,
    _ADE20K_SKY,
    _ADE20K_WATER_CLASSES,
    _ensure_segformer,
    _neural_sky_and_building_masks,
    _neural_water_mask,
    detect_buildings_from_mask,
    stitch_pano_masks,
    stitch_pano_views,
)

SPIN_STEP_DEG = 30.0
SPIN_HEADINGS = tuple(float(h) for h in np.arange(0.0, 360.0, SPIN_STEP_DEG))


def _segformer_infer(image_rgb: np.ndarray, input_size: int) -> np.ndarray:
    """Run SegFormer on a single image at an arbitrary input resolution.

    Bypasses the project's cached label_map path because we want different
    input sizes for coarse vs. fine. Returns an (H, W) int label map at
    the original image resolution.
    """
    import torch
    import torch.nn.functional as F
    from transformers import (
        SegformerForSemanticSegmentation,
        SegformerImageProcessor,
    )
    if not _ensure_segformer():
        raise RuntimeError("SegFormer unavailable")
    # Reuse the singleton model the rest of the pipeline loaded; build a
    # one-off processor with the requested input size so the original
    # processor's 512x512 config isn't disturbed.
    from city2stl.skyline_cv import pipeline as _p
    model = _p._segformer_model
    if model is None:
        raise RuntimeError("SegFormer model not loaded")

    model_id = _p._SEGFORMER_MODEL_ID
    processor = SegformerImageProcessor.from_pretrained(model_id)
    processor.size = {"height": int(input_size), "width": int(input_size)}
    processor.do_resize = True

    h, w = image_rgb.shape[:2]
    pil = Image.fromarray(image_rgb)
    inputs = processor(images=pil, return_tensors="pt")
    if _p._segformer_device != "cpu":
        inputs = {k: v.to(_p._segformer_device) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    upsampled = F.interpolate(
        outputs.logits, size=(h, w), mode="bilinear", align_corners=False)
    label_map = upsampled.squeeze(0).argmax(dim=0).cpu().numpy()
    return label_map


def _clean_mask(mask: np.ndarray, open_px: int = 5, close_px: int = 9) -> np.ndarray:
    """Morphological speckle removal followed by gap closing.

    Without this, single-pixel SegFormer speckle in the lower-half of the
    coarse mask makes every column register as "building" and the
    cluster finder produces one mega-cluster spanning the whole pano.
    """
    import cv2
    m = (mask.astype(np.uint8)) * 255
    if open_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (open_px, open_px))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (close_px, close_px))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    return m.astype(bool)


def _building_x_clusters(
    building_mask: np.ndarray,
    min_cluster_width_px: int = 30,
    min_gap_px: int = 24,
    min_column_height_px: int = 30,
) -> list[tuple[int, int]]:
    """Group columns containing building pixels into contiguous x-clusters.

    A column counts as "building" only when its building mask has at
    least ``min_column_height_px`` True rows — kills foliage/foreground
    speckle that would otherwise merge real-skyline clusters with
    adjacent palm/grass strips. Returns (x_left, x_right) inclusive
    tuples. Adjacent runs separated by less than ``min_gap_px`` empty
    columns are merged. Clusters narrower than ``min_cluster_width_px``
    are dropped.
    """
    col_heights = building_mask.sum(axis=0)
    has_building = col_heights >= min_column_height_px
    if not has_building.any():
        return []
    # Find runs of True.
    in_run = False
    runs: list[list[int]] = []
    for x, v in enumerate(has_building):
        if v and not in_run:
            runs.append([x, x])
            in_run = True
        elif v:
            runs[-1][1] = x
        elif in_run:
            in_run = False
    # Merge runs separated by less than min_gap_px.
    merged: list[list[int]] = []
    for run in runs:
        if merged and (run[0] - merged[-1][1]) <= min_gap_px:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    return [(a, b) for a, b in merged if (b - a) >= min_cluster_width_px]


def _building_band(
    building_mask: np.ndarray, slack_px: int = 12,
) -> tuple[int, int] | None:
    """Top/bottom row indices where any building columns appear."""
    rows_with = building_mask.any(axis=1)
    if not rows_with.any():
        return None
    ys = np.where(rows_with)[0]
    h = building_mask.shape[0]
    y_top = max(0, int(ys.min()) - slack_px)
    y_bot = min(h - 1, int(ys.max()) + slack_px)
    return y_top, y_bot


def _label_overlay(
    rgb: np.ndarray, label_map: np.ndarray, alpha: float = 0.5,
) -> np.ndarray:
    """Paint the four pipeline classes over the image for visual inspection."""
    out = rgb.astype(np.float32).copy()
    sky_color = np.array([60, 170, 255], dtype=np.float32)
    bld_color = np.array([235, 70, 55], dtype=np.float32)
    water_color = np.array([30, 80, 200], dtype=np.float32)

    sky = label_map == _ADE20K_SKY
    bld = np.isin(label_map, _ADE20K_BUILDING_CLASSES)
    water = np.isin(label_map, _ADE20K_WATER_CLASSES)

    out[sky] = out[sky] * (1 - alpha) + sky_color * alpha
    out[water] = out[water] * (1 - alpha) + water_color * alpha
    out[bld] = out[bld] * (1 - alpha) + bld_color * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def _save_png(arr: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)


def _run(args: argparse.Namespace) -> int:
    api_key = _resolve_api_key()
    region_name = args.region
    target_seed_name = args.seed

    # Locate the seed URL in the site's seed_urls.
    seed_urls = _load_site_seed_urls(region_name)
    seed_point: SkylinePoint | None = None
    for idx, url in enumerate(seed_urls):
        parsed = _parse_streetview_url(url)
        if parsed is None:
            continue
        lat, lon, heading, fov, pitch, pano_id = parsed
        candidate_name = f"seed_{idx + 1}"
        if candidate_name == target_seed_name:
            seed_point = SkylinePoint(
                name=candidate_name, lat=lat, lon=lon,
                heading=heading, source="seed", score=1.0,
                fov=fov, pitch=pitch, pano_id=pano_id,
            )
            break
    if seed_point is None:
        print(f"seed '{target_seed_name}' not found in {region_name}")
        return 2

    print(f"[multires] capturing {target_seed_name} "
          f"({seed_point.lat:.5f},{seed_point.lon:.5f})")
    t0 = time.perf_counter()
    # Even when the seed URL contains a Photo Sphere pano_id, force
    # location-snapped capture so we get an image for every spin heading
    # (Photo Spheres often only have imagery at the original heading).
    # The main pipeline gets away with photosphere=True only because it
    # resolves via ``runs/seed_resolution_cache.json``; the demo script
    # skips that cache and would otherwise capture 0 usable views.
    prefetch, _eff_pitch, cached_views = _capture_pano_views(
        seed_point, api_key, SPIN_HEADINGS, is_photosphere=False,
    )
    print(f"[multires] capture: {time.perf_counter() - t0:.1f}s "
          f"({len(cached_views)}/12 views passed screening)")

    if not cached_views:
        print("[multires] no usable views; aborting")
        return 3

    # Stitch all RGB views into a 360-degree pano. ``stitch_pano_views``
    # ignores per-view masks if absent; we just need the central crop +
    # per-column headings.
    spin_for_stitch = [
        {"image": v["image"], "geo_heading": v["geo_heading"]}
        for v in cached_views
    ]
    t0 = time.perf_counter()
    pano_out = stitch_pano_views(spin_for_stitch, seed_point.fov, SPIN_STEP_DEG)
    if pano_out is None:
        print("[multires] pano stitch returned None; aborting")
        return 4
    pano_rgb, headings_per_col = pano_out
    print(f"[multires] stitch RGB: {time.perf_counter() - t0:.2f}s, "
          f"pano shape {pano_rgb.shape}")

    out_dir = (ROOT / "city2stl" / "skyline_cv" / "runs" /
               "multires" / f"{region_name}_{target_seed_name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    _save_png(pano_rgb, out_dir / "00_pano_rgb.png")

    # Coarse pass.
    if args.coarse_source == "per_view_masks":
        # Reuse the per-view SegFormer masks the cache already has from
        # the screening step (the in-memory LRU cache + the on-disk
        # screening cache mean these are essentially free here). Stitch
        # them with the existing geometry so the column-to-heading map
        # is identical to the RGB pano. This skips the costly coarse
        # forward pass on a 1800-wide pano.
        t0 = time.perf_counter()
        spin_with_masks: list[dict] = []
        for cv in cached_views:
            img = cv["image"]
            _sky, bmask = _neural_sky_and_building_masks(img)
            wmask = _neural_water_mask(img)
            spin_with_masks.append({
                "image": img,
                "geo_heading": cv["geo_heading"],
                "building_mask": bmask,
                "water_mask": wmask,
            })
        stitched = stitch_pano_masks(
            spin_with_masks, seed_point.fov, SPIN_STEP_DEG)
        if stitched is None:
            print("[multires] stitch_pano_masks returned None; aborting")
            return 4
        coarse_bld_raw, coarse_water = stitched
        print(f"[multires] coarse pass via stitched per-view masks: "
              f"{time.perf_counter() - t0:.1f}s "
              f"(reused per-view SegFormer cache)")
    else:
        t0 = time.perf_counter()
        coarse_labels = _segformer_infer(pano_rgb, input_size=args.coarse_size)
        print(f"[multires] coarse pass ({args.coarse_size}px input): "
              f"{time.perf_counter() - t0:.1f}s")
        coarse_bld_raw = np.isin(coarse_labels, _ADE20K_BUILDING_CLASSES)
        coarse_water = np.isin(coarse_labels, _ADE20K_WATER_CLASSES)
        _save_png(
            _label_overlay(pano_rgb, coarse_labels),
            out_dir / "01_coarse_overlay.png",
        )

    coarse_bld = _clean_mask(coarse_bld_raw)
    # Strip any coarse-building pixels that are at or below the water
    # surface — "buildings should not be placed on water". Reading the
    # per-column waterline and capping building rows at the highest
    # water pixel removes the most common Miami/Cartagena failure mode
    # where boat wakes or distant water reflections get tagged as
    # building by the model. Equivalent to the production pipeline's
    # ground cap but at pano level.
    if coarse_water.any():
        water_top = np.where(
            coarse_water.any(axis=0),
            coarse_water.argmax(axis=0),
            coarse_water.shape[0],
        ).astype(np.int32)
        row_idx = np.arange(coarse_bld.shape[0])[:, None]
        below_water = (row_idx >= water_top[None, :])
        coarse_bld = coarse_bld & ~below_water
    _save_png(
        (coarse_bld_raw.astype(np.uint8) * 255),
        out_dir / "02a_coarse_building_mask_raw.png",
    )
    _save_png(
        (coarse_bld.astype(np.uint8) * 255),
        out_dir / "02b_coarse_building_mask_cleaned.png",
    )
    _save_png(
        (coarse_water.astype(np.uint8) * 255),
        out_dir / "02c_coarse_water_mask.png",
    )

    band = _building_band(coarse_bld, slack_px=args.band_slack_px)
    if band is None:
        print("[multires] coarse pass found no buildings")
        return 5
    y_top, y_bot = band
    print(f"[multires] band: rows {y_top}-{y_bot} of {coarse_bld.shape[0]}")

    clusters = _building_x_clusters(
        coarse_bld, min_cluster_width_px=args.min_cluster_width,
        min_gap_px=args.min_gap_px,
        min_column_height_px=args.min_column_height,
    )
    print(f"[multires] {len(clusters)} building x-cluster(s): "
          f"{[(a, b, b - a) for a, b in clusters]}")
    if not clusters:
        print("[multires] no clusters after width filter")
        return 6

    # Fine pass: one SegFormer call per cluster, cropped to the band.
    fine_mask_pano = np.zeros_like(coarse_bld)
    fine_total_t = 0.0
    index_total_t = 0.0
    # Per-tower segments translated back to pano coordinates. Each entry
    # has the (x_left, x_right, top_y, base_y, heading_deg_center) the
    # matcher would later consume.
    fine_segments: list[dict] = []
    for ci, (xL, xR) in enumerate(clusters):
        crop = pano_rgb[y_top: y_bot + 1, xL: xR + 1]
        cluster_out = out_dir / f"crops/cluster_{ci:02d}"
        cluster_out.mkdir(parents=True, exist_ok=True)
        _save_png(crop, cluster_out / "crop_rgb.png")

        t0 = time.perf_counter()
        fine_labels = _segformer_infer(crop, input_size=args.fine_size)
        fine_total_t += time.perf_counter() - t0
        fine_bld = np.isin(fine_labels, _ADE20K_BUILDING_CLASSES)
        _save_png(
            _label_overlay(crop, fine_labels),
            cluster_out / "fine_overlay.png",
        )
        _save_png(
            (fine_bld.astype(np.uint8) * 255),
            cluster_out / "fine_building_mask.png",
        )
        fine_mask_pano[y_top: y_bot + 1, xL: xR + 1] |= fine_bld

        # Fine indexing: run the production silhouette splitter on this
        # cluster's fine building mask to get one segment per tower.
        # ``detect_buildings_from_mask`` returns (x_left, x_right, top_y,
        # base_y) in CROP coordinates — translate to pano coordinates so
        # the matcher can later look up bearings via ``headings_per_col``.
        t0 = time.perf_counter()
        crop_segments = detect_buildings_from_mask(
            fine_bld, image=crop) or []
        index_total_t += time.perf_counter() - t0
        for s in crop_segments:
            sxL = int(s["x_left"]) + xL
            sxR = int(s["x_right"]) + xL
            sty = int(s.get("top_y", 0)) + y_top
            sby = int(s.get("base_y", fine_bld.shape[0] - 1)) + y_top
            cx = max(0, min(headings_per_col.size - 1, (sxL + sxR) // 2))
            fine_segments.append({
                "cluster_idx": ci,
                "x_left": sxL, "x_right": sxR,
                "top_y": sty, "base_y": sby,
                "heading_deg_center": float(headings_per_col[cx]),
                "width_px": int(sxR - sxL),
                "height_px": int(sby - sty),
            })

    print(f"[multires] {len(clusters)} fine passes "
          f"({args.fine_size}px each): {fine_total_t:.1f}s total, "
          f"silhouette splitter: {index_total_t:.2f}s, "
          f"{len(fine_segments)} per-tower segments")

    _save_png(
        (fine_mask_pano.astype(np.uint8) * 255),
        out_dir / "03_fine_pano_building_mask.png",
    )

    # Overlay per-tower segments on the pano for visual inspection.
    overlay = pano_rgb.copy()
    try:
        import cv2
        palette = (
            (255, 80, 80), (80, 200, 255), (180, 255, 80),
            (255, 180, 60), (200, 120, 255), (80, 255, 180),
            (255, 80, 200), (180, 180, 255),
        )
        for i, seg in enumerate(fine_segments):
            color = palette[i % len(palette)]
            cv2.rectangle(
                overlay,
                (int(seg["x_left"]), int(seg["top_y"])),
                (int(seg["x_right"]), int(seg["base_y"])),
                color, 2,
            )
            cv2.putText(
                overlay, str(i + 1),
                (int(seg["x_left"]) + 4, max(20, int(seg["top_y"]) + 16)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA,
            )
        _save_png(overlay, out_dir / "05_fine_segments_overlay.png")
    except Exception as exc:
        print(f"[multires] overlay render skipped: {exc}")

    # Render the per-column heading axis as a thin strip beneath the
    # pano so reviewers can read off where each cluster lives in compass
    # space. One px = ~360/W deg.
    h_axis = np.full((30, pano_rgb.shape[1], 3), 220, dtype=np.uint8)
    # Draw a tick mark every 30 degrees of heading.
    for hdg in range(0, 360, 30):
        col = int(np.argmin(np.abs(
            ((headings_per_col - hdg + 180.0) % 360.0) - 180.0)))
        if 0 <= col < pano_rgb.shape[1]:
            h_axis[:, col] = (60, 60, 60)
    _save_png(h_axis, out_dir / "04_heading_axis.png")

    cluster_summary = [
        {
            "x_left": int(xL), "x_right": int(xR),
            "heading_left_deg": float(headings_per_col[xL]),
            "heading_right_deg": float(
                headings_per_col[min(xR, headings_per_col.size - 1)]),
        }
        for (xL, xR) in clusters
    ]
    import json
    (out_dir / "summary.json").write_text(json.dumps({
        "region": region_name,
        "seed": target_seed_name,
        "pano_shape": list(pano_rgb.shape),
        "band_y_top": int(y_top),
        "band_y_bot": int(y_bot),
        "coarse_input_size": int(args.coarse_size),
        "fine_input_size": int(args.fine_size),
        "n_clusters": len(clusters),
        "clusters": cluster_summary,
        "n_fine_segments": len(fine_segments),
        "fine_segments": fine_segments,
    }, indent=2), encoding="utf-8")

    print(f"[multires] written to {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--region", required=True,
                   help="Region name (matches sites/<name>.json).")
    p.add_argument("--seed", default="seed_1",
                   help="Which seed in the site's seed_urls list to use "
                        "(default: seed_1).")
    p.add_argument("--coarse-size", type=int, default=512,
                   help="SegFormer input resolution for the coarse pass "
                        "on the full pano (default: 512). Lower values "
                        "(e.g. 256) are faster but produce noisy masks "
                        "that defeat the cluster finder on a 1800 px-"
                        "wide pano.")
    p.add_argument("--fine-size", type=int, default=512,
                   help="SegFormer input resolution for each per-cluster "
                        "fine pass (default: 512).")
    p.add_argument("--band-slack-px", type=int, default=16,
                   help="Extra rows kept above/below the detected building "
                        "band before cropping (default: 16).")
    p.add_argument("--min-cluster-width", type=int, default=30,
                   help="Minimum cluster width in pixels (default: 30).")
    p.add_argument("--min-gap-px", type=int, default=24,
                   help="Maximum empty-column gap between adjacent runs "
                        "that get merged into one cluster (default: 24).")
    p.add_argument("--min-column-height", type=int, default=30,
                   help="Minimum building-mask vertical extent in a "
                        "column for it to count as a 'building column' "
                        "(default: 30). Filters foliage/foreground "
                        "speckle.")
    p.add_argument("--coarse-source", choices=("per_view_masks", "full_pano"),
                   default="per_view_masks",
                   help="Where the coarse mask comes from. "
                        "``per_view_masks`` (default) stitches the "
                        "per-view SegFormer masks that the production "
                        "pipeline already produced - free coarse pass. "
                        "``full_pano`` runs SegFormer on the whole pano "
                        "(more expensive; useful when per-view masks "
                        "aren't already cached).")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
