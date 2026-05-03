"""
tools/harvest_roof_crops.py — Harvest labelled 64×64 RGB building patches for
training the RoofNet roof-shape classification head.

For each evaluation city the script:
  1. Fetches cached OSM building data (requires seed_eval_regions.py to have
     been run, or passes start_server=True to start the server).
  2. Filters buildings that carry an OSM ``roof:shape`` tag (ground truth).
  3. Crops 64×64 px tiles from the cached satellite image centred on each
     building footprint bounding box.
  4. Saves each crop as ``<label>/<city>_<idx>.png`` under the output directory.
  5. Writes a manifest CSV with columns: path, label, city, building_id, bbox.

The output directory structure is compatible with ``torchvision.datasets.ImageFolder``
so ``train_roof_classifier.py`` can consume it directly.

Usage
-----
    # from the strm2stl/ directory:
    ..\\..venv\\Scripts\\python.exe -m tools.harvest_roof_crops [options]

Options
-------
  --output-dir PATH     Where to write crops (default: output/roof_crops/)
  --crop-size N         Pixel size of each square crop (default: 64)
  --min-pixels N        Minimum footprint area in pixels to include (default: 8)
  --oversample-rare N   Oversample rare classes (skillion, dome) by factor N (default: 5)
  --no-server           Assume server already running on --port
  --port N              Server port (default: 9090)
  --quiet               Suppress progress output
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parent
_REPO_ROOT = _STRM2STL.parent
for _p in (_STRM2STL, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.data.seed_eval_regions import EVAL_CITIES  # noqa: E402

# ---------------------------------------------------------------------------
# Shape label set (must match roof_classifier._SHAPE_LABELS and RoofNet order)
# ---------------------------------------------------------------------------
SHAPE_LABELS = ["flat", "gabled", "hipped", "pyramidal", "skillion", "dome"]

# Classes with low OSM coverage that will be oversampled
RARE_CLASSES = {"skillion", "dome"}

# Default crop size in pixels
DEFAULT_CROP_SIZE = 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox_to_pixel_box(
    footprint_geom: dict,
    satellite_transform,
    satellite_shape: tuple[int, int],
    crop_size: int,
) -> tuple[int, int, int, int] | None:
    """Convert a GeoJSON geometry to a square pixel crop (row0, col0, row1, col1).

    Returns None if the footprint is too small or out of bounds.
    """
    try:
        import rasterio.transform as rtransform
        from shapely.geometry import shape as shapely_shape
    except ImportError:
        raise ImportError(
            "rasterio and shapely are required for harvest_roof_crops. "
            "Install with: pip install rasterio shapely"
        )

    geom = shapely_shape(footprint_geom)
    minx, miny, maxx, maxy = geom.bounds

    # Convert geographic coords to pixel coords via affine transform
    row_min, col_min = rtransform.rowcol(satellite_transform, minx, maxy)
    row_max, col_max = rtransform.rowcol(satellite_transform, maxx, miny)

    # Centre of the footprint
    cy = (row_min + row_max) / 2
    cx = (col_min + col_max) / 2

    half = crop_size // 2
    r0 = int(cy) - half
    c0 = int(cx) - half
    r1 = r0 + crop_size
    c1 = c0 + crop_size

    H, W = satellite_shape
    if r0 < 0 or c0 < 0 or r1 > H or c1 > W:
        return None  # out of bounds

    return r0, c0, r1, c1


def _footprint_pixel_area(
    footprint_geom: dict,
    satellite_transform,
) -> int:
    """Approximate footprint area in satellite pixels."""
    try:
        import rasterio.transform as rtransform
        from shapely.geometry import shape as shapely_shape
    except ImportError:
        return 1  # can't measure — include anyway

    geom = shapely_shape(footprint_geom)
    minx, miny, maxx, maxy = geom.bounds

    row_min, col_min = rtransform.rowcol(satellite_transform, minx, maxy)
    row_max, col_max = rtransform.rowcol(satellite_transform, maxx, miny)

    return max(1, abs(row_max - row_min) * abs(col_max - col_min))


def _save_crop(
    rgb_array: "np.ndarray",
    r0: int, c0: int, r1: int, c1: int,
    out_path: Path,
) -> bool:
    """Crop and save a PNG tile.  Returns True on success."""
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("Pillow is required. Install with: pip install Pillow")

    crop = rgb_array[r0:r1, c0:c1, :]
    if crop.shape[0] != (r1 - r0) or crop.shape[1] != (c1 - c0):
        return False

    img = Image.fromarray(crop.astype(np.uint8))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path))
    return True


def _augment_and_save(
    rgb_array: "np.ndarray",
    r0: int, c0: int, r1: int, c1: int,
    base_path: Path,
    factor: int,
) -> list[Path]:
    """Save base crop + ``factor-1`` augmented copies (flips + 90° rotations)."""
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("Pillow required. Install: pip install Pillow")

    crop = rgb_array[r0:r1, c0:c1, :].astype(np.uint8)
    img = Image.fromarray(crop)

    saved: list[Path] = []

    augments = [
        img,
        img.transpose(Image.FLIP_LEFT_RIGHT),
        img.transpose(Image.ROTATE_90),
        img.transpose(Image.ROTATE_180),
        img.transpose(Image.ROTATE_270),
        img.transpose(Image.FLIP_TOP_BOTTOM),
        img.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.ROTATE_90),
        img.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.ROTATE_90),
    ]

    for i, aug in enumerate(augments[:factor]):
        p = base_path.parent / f"{base_path.stem}_aug{i}{base_path.suffix}"
        if i == 0:
            p = base_path  # canonical (non-augmented) saved with original name
        p.parent.mkdir(parents=True, exist_ok=True)
        aug.save(str(p))
        saved.append(p)

    return saved


# ---------------------------------------------------------------------------
# Main harvest function
# ---------------------------------------------------------------------------

def harvest_roof_crops(
    output_dir: str = "output/roof_crops",
    crop_size: int = DEFAULT_CROP_SIZE,
    min_pixels: int = 8,
    oversample_rare: int = 5,
    start_server: bool = True,
    port: int = 9090,
    verbose: bool = True,
) -> dict:
    """Harvest labelled building crops from all eval cities.

    Parameters
    ----------
    output_dir : str
        Root directory for saved crops (relative to strm2stl/ or absolute).
    crop_size : int
        Pixel dimension of each square crop.
    min_pixels : int
        Minimum footprint area in satellite pixels; smaller buildings are skipped.
    oversample_rare : int
        Augmentation factor for rare classes (skillion, dome).
    start_server : bool
        Start the FastAPI server if not already running.
    port : int
        Server port.
    verbose : bool
        Print progress messages.

    Returns
    -------
    dict with keys 'total_crops', 'per_label', 'per_city', 'manifest_path'.
    """
    try:
        import rasterio
        import rasterio.transform
    except ImportError:
        raise ImportError(
            "rasterio is required. Install with: pip install rasterio"
        )

    out_root = Path(output_dir)
    if not out_root.is_absolute():
        out_root = _STRM2STL / output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    manifest_path = out_root / "manifest.csv"

    from app.session.terrain_session import TerrainSession

    session = TerrainSession(port=port)
    if start_server:
        session.start()
    else:
        if not session._wait_for_server_ready(max_attempts=3):
            raise RuntimeError(f"Server not reachable on port {port}.")

    manifest_rows: list[dict] = []
    per_label: Counter = Counter()
    per_city: Counter = Counter()

    for city_name, cfg in EVAL_CITIES.items():
        if verbose:
            print(f"\n{'─' * 60}")
            print(f"  Harvesting: {city_name}")
            print(f"{'─' * 60}")

        try:
            session.select(city_name)
        except Exception:
            try:
                session.create_region(
                    name=city_name,
                    north=cfg["north"], south=cfg["south"],
                    east=cfg["east"], west=cfg["west"],
                )
            except Exception as exc:
                if verbose:
                    print(f"  SKIP {city_name}: cannot select/create region — {exc}")
                continue

        # ── Fetch OSM data ─────────────────────────────────────────────
        try:
            session.fetch_cities()
        except Exception as exc:
            if verbose:
                print(f"  SKIP {city_name}: OSM fetch failed — {exc}")
            continue

        if session.city_data is None:
            if verbose:
                print(f"  SKIP {city_name}: no city data")
            continue

        buildings = session.city_data.get("buildings", {}).get("features", [])
        if not buildings:
            if verbose:
                print(f"  SKIP {city_name}: no buildings")
            continue

        # ── Fetch satellite image ──────────────────────────────────────
        try:
            session.fetch_satellite()
        except Exception as exc:
            if verbose:
                print(f"  SKIP {city_name}: satellite fetch failed — {exc}")
            continue

        _sat_raw = session.satellite
        if _sat_raw is None:
            if verbose:
                print(f"  SKIP {city_name}: no satellite data")
            continue

        # TerrainSession.satellite is a base64-encoded JPEG string — decode to numpy
        if isinstance(_sat_raw, str):
            from PIL import Image as _PILImage
            _img_bytes = base64.b64decode(_sat_raw)
            sat = np.array(
                _PILImage.open(BytesIO(_img_bytes)).convert("RGB"), dtype=np.uint8
            )
        else:
            sat = _sat_raw  # already numpy (future-proof)

        # Build rasterio affine transform from bbox + array shape
        transform = getattr(session, "satellite_transform", None)
        if transform is None:
            H, W = sat.shape[:2]
            from rasterio.transform import from_bounds
            transform = from_bounds(
                cfg["west"], cfg["south"], cfg["east"], cfg["north"], W, H
            )

        if verbose:
            print(f"  Satellite: {sat.shape[1]}×{sat.shape[0]} px")

        # ── Filter to buildings with roof:shape ────────────────────────
        tagged = [
            f for f in buildings
            if f.get("properties", {}).get("roof:shape") in SHAPE_LABELS
        ]

        if verbose:
            print(f"  Buildings: {len(buildings)} total, {len(tagged)} with roof:shape GT")

        # ── Crop and save ──────────────────────────────────────────────
        saved_this_city = 0
        skipped = 0

        for idx, feat in enumerate(tagged):
            props = feat.get("properties", {})
            label = props.get("roof:shape", "").lower()
            if label not in SHAPE_LABELS:
                skipped += 1
                continue

            geom = feat.get("geometry")
            if geom is None:
                skipped += 1
                continue

            pix_area = _footprint_pixel_area(geom, transform)
            if pix_area < min_pixels:
                skipped += 1
                continue

            box = _bbox_to_pixel_box(geom, transform, sat.shape[:2], crop_size)
            if box is None:
                skipped += 1
                continue

            r0, c0, r1, c1 = box
            is_rare = label in RARE_CLASSES
            factor = oversample_rare if is_rare else 1

            base_filename = f"{city_name}_{idx:05d}.png"
            base_path = out_root / label / base_filename

            saved_paths = _augment_and_save(sat, r0, c0, r1, c1, base_path, factor)

            for sp in saved_paths:
                manifest_rows.append({
                    "path": str(sp.relative_to(out_root)),
                    "label": label,
                    "city": city_name,
                    "building_idx": idx,
                    "bbox_row0": r0, "bbox_col0": c0,
                    "bbox_row1": r1, "bbox_col1": c1,
                })
                per_label[label] += 1
                per_city[city_name] += 1
                saved_this_city += 1

        if verbose:
            print(f"  Saved: {saved_this_city} crops  (skipped: {skipped})")

    # ── Write manifest ─────────────────────────────────────────────────
    if manifest_rows:
        fieldnames = ["path", "label", "city", "building_idx",
                      "bbox_row0", "bbox_col0", "bbox_row1", "bbox_col1"]
        with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(manifest_rows)

    # ── Summary ────────────────────────────────────────────────────────
    total = sum(per_label.values())
    if verbose:
        print(f"\n{'═' * 60}")
        print(f"  Harvest summary")
        print(f"{'═' * 60}")
        print(f"  Total crops: {total}")
        print(f"  Per label:")
        for lbl in SHAPE_LABELS:
            print(f"    {lbl:<12} {per_label.get(lbl, 0):>6}")
        print(f"  Per city:")
        for city, n in per_city.most_common():
            print(f"    {city:<25} {n:>6}")
        print(f"  Manifest: {manifest_path}")

    return {
        "total_crops": total,
        "per_label": dict(per_label),
        "per_city": dict(per_city),
        "manifest_path": str(manifest_path),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Harvest labelled building crop patches for RoofNet training."
    )
    parser.add_argument(
        "--output-dir", default="output/roof_crops",
        help="Root directory for saved crops (default: output/roof_crops/)",
    )
    parser.add_argument(
        "--crop-size", type=int, default=DEFAULT_CROP_SIZE,
        help=f"Pixel size of each square crop (default: {DEFAULT_CROP_SIZE})",
    )
    parser.add_argument(
        "--min-pixels", type=int, default=8,
        help="Minimum footprint pixel area to include (default: 8)",
    )
    parser.add_argument(
        "--oversample-rare", type=int, default=5,
        help="Augmentation factor for skillion/dome classes (default: 5)",
    )
    parser.add_argument(
        "--no-server", action="store_true",
        help="Assume server already running on --port",
    )
    parser.add_argument(
        "--port", type=int, default=9090,
        help="Server port (default: 9090)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    harvest_roof_crops(
        output_dir=args.output_dir,
        crop_size=args.crop_size,
        min_pixels=args.min_pixels,
        oversample_rare=args.oversample_rare,
        start_server=not args.no_server,
        port=args.port,
        verbose=not args.quiet,
    )
