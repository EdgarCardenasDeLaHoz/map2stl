"""Repo paths, the photo-folder layout and the file names beside a photo (labels, flags, depth, guesses).

Also puts the repo root and this folder on `sys.path`, so every sibling module can import `city2stl` and `geo2stl`."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regions import Config

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (REPO_ROOT, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
DEFAULT_OSM = REPO_ROOT / "cache" / "5d947d851a9f2b205205e503611a5df310e7e87e.json"
DEFAULT_DEM = REPO_ROOT / "cache" / "opentopo" / "d6eb6dd762c161a4afb3471521b53e14.tif"
DEFAULT_PHOTOS = REPO_ROOT / "runs" / "photo"
ANNOTATIONS = REPO_ROOT / "city2stl" / "skyline" / "sites" / "annotations"
REGIONS_FILE = HERE / "regions.json"
MODEL_DIR = REPO_ROOT / "runs" / "photo_label_model"
TILES_CACHE_DIR = REPO_ROOT / "runs" / "photo_label_tiles"


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


_EXT_RANK = {".png": 0, ".jpg": 1, ".jpeg": 1, ".webp": 2}
_DERIVED = re.compile(r"^(overlay|landmarks_)|_depth|_labels|_baseline", re.I)
_SEED_NONFRAME = re.compile(r"^pano_|_(mask|seg)$", re.I)   # masks / segmentations beside the frames (pano.png is the strip photo)


def _is_seed_nonframe(p: Path) -> bool:
    """True for masks and segmentations (`pano_seg`, `*_mask`, `*_seg`) inside `03_seeds/seed_*/`; the stitched
    strip `pano.png` is a photo (cylindrical pose, see `pano_seed`)."""
    return p.parent.parent.name == "03_seeds" and bool(_SEED_NONFRAME.search(p.stem))


def _is_seed_strip(p: Path) -> bool:
    """True for `03_seeds/seed_*/pano.png`, the stitched 360 deg strip (listed with a cylindrical pose)."""
    return p.name.lower() == "pano.png" and p.parent.parent.name == "03_seeds"


def _photo_files(cfg: Config) -> list[tuple[str, Path, Path]]:
    """(root key, root folder, file) per folder+stem across the roots (png, else jpg, else webp)."""
    best: dict[tuple, tuple[str, Path, Path]] = {}
    for key, root in cfg.photo_roots.items():
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            ext = p.suffix.lower()
            if ext not in IMG_EXT or _DERIVED.search(p.stem) or _is_seed_nonframe(p):
                continue
            k = (key, p.parent, p.stem)
            if k not in best or _EXT_RANK[ext] < _EXT_RANK[best[k][2].suffix.lower()]:
                best[k] = (key, root, p)
    return sorted(best.values(), key=lambda t: (t[0], t[2]))


def photo_rel(key: str, root: Path, p: Path) -> str:
    rel = p.relative_to(root).as_posix()
    return f"{key}/{rel}" if key else rel


def flags_path(cfg: Config) -> Path:
    """Bad-photo flags of a region, beside its label files."""
    return cfg.annotations_dir / f"{cfg.region}_photo_flags.json"


def safe_photo(cfg: Config, rel: str) -> Path | None:
    """The image file `rel` names inside one of the photo roots, else None.

    A keyed root owns the paths starting `<key>/`; the unkeyed root takes everything else.
    """
    cands = [(root, rel[len(key) + 1:]) for key, root in cfg.photo_roots.items()
             if key and rel.startswith(key + "/")]
    if "" in cfg.photo_roots:
        cands.append((cfg.photo_roots[""], rel))
    for root, sub in cands:
        base = root.resolve()
        p = (base / sub).resolve()
        if base in p.parents and p.suffix.lower() in IMG_EXT and p.is_file():
            return p
    return None


def label_path(cfg: Config, rel: str) -> Path:
    stem = Path(rel).with_suffix("").as_posix()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "photo"
    return cfg.annotations_dir / f"{cfg.region}_{safe}.json"


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _guess_cache_path(photo: Path) -> Path:
    return photo.with_name(photo.stem + "_guesses.json")


def depth_path(photo: Path) -> Path:
    return photo.with_name(photo.stem + "_depth.npy")


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else REPO_ROOT / p
