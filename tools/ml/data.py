"""
tools.ml.data — Datasets, transforms, and data harvesting pipeline.

Two dataset types:
  - RoofCropDataset  : 128x128 RGB crops with shape labels (from OSM ground truth)
  - HeightTileDataset: 256x256 RGB + height pairs (from provider rasters)

Harvesting functions download and prepare training data from OSM + satellite.
"""

from __future__ import annotations

import csv
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

from tools.ml.config import (
    SHAPE_LABELS,
    LABEL_TO_IDX,
    RARE_CLASSES,
    DEFAULT_CROP_SIZE,
    DEFAULT_TILE_SIZE,
    MAX_HEIGHT_M,
    IMAGENET_MEAN,
    IMAGENET_STD,
    TRAIN_CITIES,
    EVAL_CITIES,
    ALL_CITIES,
)

logger = logging.getLogger(__name__)

# Lazy torch imports
_TORCH_AVAILABLE = False
try:
    import torch
    from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
    import torchvision.transforms as T
    _TORCH_AVAILABLE = True
except ImportError:
    pass


def _require_torch():
    if not _TORCH_AVAILABLE:
        raise ImportError(
            "PyTorch and torchvision required. "
            "Install: pip install torch torchvision"
        )


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def build_transforms(crop_size: int, augment: bool = False):
    """Build torchvision transforms for roof crop images.

    Training transforms include random rotation (full 360), flips, color
    jitter, and random erasing.  Roof shape should be rotation-invariant
    so heavy rotation augmentation is appropriate.

    All transforms normalise with ImageNet statistics for the pretrained
    MobileNetV3 backbone.
    """
    _require_torch()
    normalise = T.Normalize(mean=list(IMAGENET_MEAN), std=list(IMAGENET_STD))

    if augment:
        return T.Compose([
            T.Resize((crop_size, crop_size)),
            T.RandomHorizontalFlip(),
            T.RandomVerticalFlip(),
            T.RandomRotation(180),
            T.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2, hue=0.05),
            T.RandomAffine(degrees=0, translate=(0.05, 0.05), scale=(0.9, 1.1)),
            T.ToTensor(),
            normalise,
            T.RandomErasing(p=0.1, scale=(0.02, 0.1)),
        ])

    return T.Compose([
        T.Resize((crop_size, crop_size)),
        T.ToTensor(),
        normalise,
    ])


def build_height_transforms(tile_size: int, augment: bool = False):
    """Build transforms for height tile pairs (RGB + height map).

    Returns a function that takes (rgb_np, height_np) and returns
    (rgb_tensor, height_tensor) with optional augmentation.
    Both arrays are (C, H, W) float32.

    RGB is normalised with ImageNet statistics so the pretrained MobileNetV3
    backbone receives the same input distribution it was trained on.
    """
    _require_torch()

    mean = torch.tensor(IMAGENET_MEAN, dtype=torch.float32).view(3, 1, 1)
    std = torch.tensor(IMAGENET_STD, dtype=torch.float32).view(3, 1, 1)

    def _transform(rgb: np.ndarray, height: np.ndarray):
        if augment:
            # Horizontal flip
            if np.random.random() > 0.5:
                rgb = rgb[:, :, ::-1].copy()
                height = height[:, :, ::-1].copy()
            # Vertical flip
            if np.random.random() > 0.5:
                rgb = rgb[:, ::-1, :].copy()
                height = height[:, ::-1, :].copy()
            # Random 90-degree rotations (preserves spatial structure)
            k = np.random.randint(0, 4)
            if k > 0:
                rgb = np.rot90(rgb, k, axes=(1, 2)).copy()
                height = np.rot90(height, k, axes=(1, 2)).copy()
            # Colour jitter on RGB only (height is not affected)
            if np.random.random() > 0.3:
                brightness = 1.0 + np.random.uniform(-0.3, 0.3)
                contrast = 1.0 + np.random.uniform(-0.25, 0.25)
                rgb = np.clip(rgb * brightness, 0.0, 1.0)
                mean_c = rgb.mean(axis=(1, 2), keepdims=True)
                rgb = np.clip(mean_c + (rgb - mean_c) * contrast, 0.0, 1.0)
                rgb = rgb.astype(np.float32)

        rgb_t = torch.from_numpy(rgb)
        # Apply ImageNet normalisation: (x - mean) / std
        rgb_t = (rgb_t - mean) / std
        return rgb_t, torch.from_numpy(height)

    return _transform


# ---------------------------------------------------------------------------
# Roof Crop Dataset
# ---------------------------------------------------------------------------

class RoofCropDataset(Dataset):
    """Dataset of labelled building crop images for roof shape classification.

    Reads from a manifest CSV produced by the harvest pipeline.  Compatible
    with torchvision ImageFolder directory layout.

    Parameters
    ----------
    manifest_path : Path
        CSV with columns: path, label, city, building_idx, bbox_*
    root_dir : Path
        Root directory containing the crop images.
    transform : callable | None
        torchvision transform to apply to each image.
    city_filter : list[str] | None
        Only include samples from these cities.
    """

    def __init__(
        self,
        manifest_path: Path,
        root_dir: Path,
        transform=None,
        city_filter: "list[str] | None" = None,
    ) -> None:
        _require_torch()
        from PIL import Image  # noqa: F401 — verify available

        self.root_dir = root_dir
        self.transform = transform
        self.samples: list[tuple[Path, int]] = []
        self.cities: set[str] = set()

        with open(manifest_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if city_filter and row["city"] not in city_filter:
                    continue
                label = row["label"]
                if label not in LABEL_TO_IDX:
                    continue
                p = root_dir / row["path"]
                self.samples.append((p, LABEL_TO_IDX[label]))
                self.cities.add(row["city"])

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        from PIL import Image
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img, label

    def class_counts(self) -> list[int]:
        """Count samples per class."""
        counts = [0] * len(SHAPE_LABELS)
        for _, lbl in self.samples:
            counts[lbl] += 1
        return counts

    def class_weights(self) -> "torch.Tensor":
        """Inverse-frequency weights for WeightedRandomSampler."""
        counts = self.class_counts()
        counts = [max(c, 1) for c in counts]
        total = sum(counts)
        weights = [total / c for c in counts]
        sample_weights = torch.tensor(
            [weights[lbl] for _, lbl in self.samples], dtype=torch.float
        )
        return sample_weights

    def label_distribution(self) -> dict[str, int]:
        """Return {label_name: count} dict."""
        counts = self.class_counts()
        return {SHAPE_LABELS[i]: c for i, c in enumerate(counts)}


# ---------------------------------------------------------------------------
# Height Tile Dataset
# ---------------------------------------------------------------------------

class HeightTileDataset(Dataset):
    """Dataset of (RGB, height) tile pairs for height regression training.

    Each sample is stored as a .npz file with:
      rgb   : (3, H, W) float32 in [0, 1]
      height: (1, H, W) float32 in metres
    """

    def __init__(
        self,
        tile_paths: list[Path],
        transform=None,
        max_height: float = MAX_HEIGHT_M,
    ) -> None:
        _require_torch()
        self.tile_paths = tile_paths
        self.transform = transform
        self.max_height = max_height

    def __len__(self) -> int:
        return len(self.tile_paths)

    def __getitem__(self, idx: int):
        data = np.load(self.tile_paths[idx])
        rgb = data["rgb"].astype(np.float32)
        height = data["height"].astype(np.float32)
        height = np.clip(height, 0, self.max_height)

        if self.transform:
            rgb, height = self.transform(rgb, height)
        else:
            rgb = torch.from_numpy(rgb)
            height = torch.from_numpy(height)

        return rgb, height


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def make_roof_loaders(
    manifest_path: Path,
    root_dir: Path,
    crop_size: int = DEFAULT_CROP_SIZE,
    batch_size: int = 16,
    val_cities: "list[str] | None" = None,
    test_cities: "list[str] | None" = None,
    num_workers: int = 0,
    seed: int = 42,
) -> "tuple[DataLoader, DataLoader, DataLoader | None, dict]":
    """Build train/val/test DataLoaders from a manifest with city-based split.

    Parameters
    ----------
    manifest_path, root_dir : Paths to manifest CSV and image root.
    crop_size : Input resolution.
    batch_size : Mini-batch size.
    val_cities : Cities held out for validation.  If None, auto-selected.
    test_cities : Cities held out for testing.  If None, auto-selected.

    Returns
    -------
    (train_loader, val_loader, test_loader_or_None, split_info)
    """
    _require_torch()
    import random

    # Discover all cities in manifest
    all_cities_in_data: set[str] = set()
    with open(manifest_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            all_cities_in_data.add(row["city"])

    cities_list = sorted(all_cities_in_data)

    if val_cities is None or test_cities is None:
        rng = random.Random(seed)
        rng.shuffle(cities_list)
        n = len(cities_list)
        if val_cities is None:
            val_cities = cities_list[:max(1, n // 5)]
        if test_cities is None:
            test_cities = cities_list[max(1, n // 5):max(2, 2 * n // 5)]

    train_cities_set = set(cities_list) - set(val_cities) - set(test_cities)

    train_tf = build_transforms(crop_size, augment=True)
    eval_tf = build_transforms(crop_size, augment=False)

    train_ds = RoofCropDataset(manifest_path, root_dir, train_tf, list(train_cities_set))
    val_ds = RoofCropDataset(manifest_path, root_dir, eval_tf, val_cities)
    test_ds = RoofCropDataset(manifest_path, root_dir, eval_tf, test_cities)

    # Weighted sampler for class balance
    sample_weights = train_ds.class_weights()
    sampler = WeightedRandomSampler(sample_weights, len(train_ds), replacement=True)

    train_loader = DataLoader(train_ds, batch_size=batch_size, sampler=sampler,
                              num_workers=num_workers, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False,
                            num_workers=num_workers, pin_memory=True)
    test_loader = None
    if len(test_ds) > 0:
        test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False,
                                 num_workers=num_workers, pin_memory=True)

    split_info = {
        "train_cities": sorted(train_cities_set),
        "val_cities": val_cities,
        "test_cities": test_cities,
        "n_train": len(train_ds),
        "n_val": len(val_ds),
        "n_test": len(test_ds),
        "train_distribution": train_ds.label_distribution(),
        "val_distribution": val_ds.label_distribution(),
    }

    return train_loader, val_loader, test_loader, split_info


def make_height_loaders(
    tile_paths: list[Path],
    tile_size: int = DEFAULT_TILE_SIZE,
    batch_size: int = 8,
    val_split: float = 0.15,
    seed: int = 42,
    num_workers: int = 0,
) -> "tuple[DataLoader, DataLoader, dict]":
    """Build train/val DataLoaders for height regression.

    Returns
    -------
    (train_loader, val_loader, split_info)
    """
    _require_torch()
    from torch.utils.data import random_split

    train_tf = build_height_transforms(tile_size, augment=True)
    eval_tf = build_height_transforms(tile_size, augment=False)

    full_ds = HeightTileDataset(tile_paths, transform=train_tf)
    n_val = max(1, int(len(full_ds) * val_split))
    n_train = len(full_ds) - n_val

    train_ds, val_ds = random_split(
        full_ds, [n_train, n_val],
        generator=torch.Generator().manual_seed(seed),
    )

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                              num_workers=num_workers, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False,
                            num_workers=num_workers, pin_memory=True)

    return train_loader, val_loader, {"n_train": n_train, "n_val": n_val}


# ---------------------------------------------------------------------------
# Harvest pipeline (roof crops)
# ---------------------------------------------------------------------------

def harvest_roof_crops(
    output_dir: str = "output/roof_crops",
    crop_size: int = DEFAULT_CROP_SIZE,
    min_pixels: int = 12,
    oversample_rare: int = 5,
    cities: "dict | None" = None,
    port: int = 9090,
    start_server: bool = True,
    verbose: bool = True,
) -> dict:
    """Harvest labelled building crops from cities with OSM roof:shape tags.

    Uses larger regions and higher-resolution crops than the v1 harvester.
    Targets all TRAIN_CITIES + EVAL_CITIES by default.

    Parameters
    ----------
    output_dir : Root for saved crops (ImageFolder layout).
    crop_size : Pixel size of each square crop.
    min_pixels : Minimum footprint area in satellite pixels.
    oversample_rare : Augmentation factor for rare classes.
    cities : City dict override.  Default: ALL_CITIES.
    port : Server port.
    start_server : Start server if not running.
    verbose : Print progress.

    Returns
    -------
    dict with total_crops, per_label, per_city, manifest_path.
    """
    import base64
    from io import BytesIO

    city_specs = cities or ALL_CITIES
    out_root = Path(output_dir)
    if not out_root.is_absolute():
        # Resolve relative to strm2stl/
        out_root = Path(__file__).resolve().parents[2] / output_dir
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

    for city_name, spec in city_specs.items():
        if verbose:
            area = spec.area_km2
            print(f"\n  Harvesting: {city_name} ({area:.1f} km2, ~{spec.roof_tag_pct}% tagged)")

        try:
            session.select(city_name)
        except Exception:
            try:
                session.create_region(
                    name=city_name,
                    north=spec.north, south=spec.south,
                    east=spec.east, west=spec.west,
                )
            except Exception as exc:
                if verbose:
                    print(f"  SKIP {city_name}: {exc}")
                continue

        try:
            session.fetch_cities()
        except Exception as exc:
            if verbose:
                print(f"  SKIP {city_name}: OSM fetch failed -- {exc}")
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

        try:
            session.fetch_satellite()
        except Exception as exc:
            if verbose:
                print(f"  SKIP {city_name}: satellite fetch failed -- {exc}")
            continue

        _sat_raw = session.satellite
        if _sat_raw is None:
            if verbose:
                print(f"  SKIP {city_name}: no satellite data")
            continue

        from PIL import Image as _PILImage

        if isinstance(_sat_raw, str):
            _img_bytes = base64.b64decode(_sat_raw)
            sat = np.array(
                _PILImage.open(BytesIO(_img_bytes)).convert("RGB"), dtype=np.uint8
            )
        else:
            sat = _sat_raw

        # Build affine transform from bbox + image shape
        try:
            from rasterio.transform import from_bounds
        except ImportError:
            raise ImportError("rasterio required: pip install rasterio")

        H, W = sat.shape[:2]
        transform = from_bounds(
            spec.west, spec.south, spec.east, spec.north, W, H
        )

        if verbose:
            print(f"  Satellite: {W}x{H} px, {len(buildings)} buildings")

        # Filter to buildings with roof:shape ground truth
        tagged = [
            f for f in buildings
            if f.get("properties", {}).get("roof:shape", "").lower() in SHAPE_LABELS
        ]

        if verbose:
            print(f"  Tagged: {len(tagged)} with roof:shape GT")

        saved_this_city = 0
        for idx, feat in enumerate(tagged):
            props = feat.get("properties", {})
            label = props.get("roof:shape", "").lower()
            if label not in SHAPE_LABELS:
                continue

            geom = feat.get("geometry")
            if geom is None:
                continue

            box = _bbox_to_pixel_box(geom, transform, (H, W), crop_size)
            if box is None:
                continue

            r0, c0, r1, c1 = box
            pix_area = abs(r1 - r0) * abs(c1 - c0)
            if pix_area < min_pixels:
                continue

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
                })
                per_label[label] += 1
                per_city[city_name] += 1
                saved_this_city += 1

        if verbose:
            print(f"  Saved: {saved_this_city} crops")

    # Write manifest
    if manifest_rows:
        fieldnames = ["path", "label", "city", "building_idx"]
        with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(manifest_rows)

    total = sum(per_label.values())
    if verbose:
        print(f"\n  Total: {total} crops across {len(per_city)} cities")
        for lbl in SHAPE_LABELS:
            print(f"    {lbl:<12} {per_label.get(lbl, 0):>6}")

    return {
        "total_crops": total,
        "per_label": dict(per_label),
        "per_city": dict(per_city),
        "manifest_path": str(manifest_path),
    }


# ---------------------------------------------------------------------------
# Harvest helpers (from original harvest_roof_crops.py)
# ---------------------------------------------------------------------------

def _bbox_to_pixel_box(
    footprint_geom: dict,
    satellite_transform,
    satellite_shape: tuple[int, int],
    crop_size: int,
) -> "tuple[int, int, int, int] | None":
    """Convert a GeoJSON geometry to a square pixel crop (row0, col0, row1, col1)."""
    try:
        import rasterio.transform as rtransform
        from shapely.geometry import shape as shapely_shape
    except ImportError:
        return None

    geom = shapely_shape(footprint_geom)
    minx, miny, maxx, maxy = geom.bounds

    row_min, col_min = rtransform.rowcol(satellite_transform, minx, maxy)
    row_max, col_max = rtransform.rowcol(satellite_transform, maxx, miny)

    cy = (row_min + row_max) / 2
    cx = (col_min + col_max) / 2

    half = crop_size // 2
    r0 = int(cy) - half
    c0 = int(cx) - half
    r1 = r0 + crop_size
    c1 = c0 + crop_size

    H, W = satellite_shape
    if r0 < 0 or c0 < 0 or r1 > H or c1 > W:
        return None

    return r0, c0, r1, c1


def _augment_and_save(
    rgb_array: "np.ndarray",
    r0: int, c0: int, r1: int, c1: int,
    base_path: Path,
    factor: int,
) -> list[Path]:
    """Save base crop + (factor-1) augmented copies."""
    from PIL import Image

    crop = rgb_array[r0:r1, c0:c1, :].astype(np.uint8)
    img = Image.fromarray(crop)

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

    saved: list[Path] = []
    for i, aug in enumerate(augments[:factor]):
        p = base_path.parent / f"{base_path.stem}_aug{i}{base_path.suffix}" if i > 0 else base_path
        p.parent.mkdir(parents=True, exist_ok=True)
        aug.save(str(p))
        saved.append(p)

    return saved
