"""scripts/tile_review.py - Manual GT-tile review tool.

Two modes:

    python scripts/tile_review.py render
        Generates output/tile_review.pdf with one numbered tile per row
        (index, name, RGB, GT, GT colorbar). Stable index order matches
        the order the trainer sees.

    python scripts/tile_review.py drop <indices...> [--dry-run] [--tiles DIR]
        Move flagged tiles into a sibling _bad/ folder so they're
        excluded from training without losing them. Indices match the
        numbers shown in the rendered PDF.

    python scripts/tile_review.py restore [--tiles DIR]
        Move everything back from _bad/ to the dataset folder.

Examples:
    # See every tile, decide which look broken:
    python scripts/tile_review.py render

    # After viewing the PDF, drop specific ones:
    python scripts/tile_review.py drop 5 12 13 14 27 41

    # If you change your mind:
    python scripts/tile_review.py restore
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TILES = REPO / "cache" / "height_tiles_combined"
DEFAULT_PDF = REPO / "output" / "tile_review.pdf"


def _list_tiles(tile_dir: Path) -> list[Path]:
    """Stable order matching the trainer (sorted glob *.npz)."""
    return sorted(tile_dir.glob("*.npz"))


def render(tile_dir: Path, out_pdf: Path):
    """Write a multi-page PDF showing every tile with index + name + RGB + GT."""
    paths = _list_tiles(tile_dir)
    if not paths:
        print(f"no tiles in {tile_dir}")
        return
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    rows_per_page = 6
    print(f"Rendering {len(paths)} tiles -> {out_pdf}")
    with PdfPages(str(out_pdf)) as pdf:
        for page_start in range(0, len(paths), rows_per_page):
            page = paths[page_start : page_start + rows_per_page]
            fig, axes = plt.subplots(
                len(page), 3, figsize=(11, 2.0 * len(page)),
                gridspec_kw={"width_ratios": [1, 1, 0.05]},
            )
            if len(page) == 1:
                axes = axes.reshape(1, 3)

            for row, p in enumerate(page):
                idx = page_start + row
                d = np.load(p)
                rgb = d["rgb"].transpose(1, 2, 0)
                h = d["height"].squeeze()
                h_pos = h[h > 0]
                cov = (h > 0).mean()
                stats = (
                    f"#{idx}  {p.stem}\n"
                    f"cov={cov*100:.1f}%  "
                    f"unique={len(np.unique(h))}  "
                    f"hmax={h.max():.1f}m  "
                    f"hmean+={h_pos.mean() if h_pos.size else 0:.1f}m"
                )

                ax_rgb, ax_gt, ax_cb = axes[row]
                ax_rgb.imshow(np.clip(rgb, 0, 1))
                ax_rgb.set_title(stats, fontsize=8, loc="left", family="monospace")
                ax_rgb.set_xticks([]); ax_rgb.set_yticks([])

                vmax = float(max(h.max(), 1.0))
                im = ax_gt.imshow(h, cmap="viridis", vmin=0, vmax=vmax)
                ax_gt.set_title("GT height (m)", fontsize=8)
                ax_gt.set_xticks([]); ax_gt.set_yticks([])
                fig.colorbar(im, cax=ax_cb)

            fig.tight_layout()
            pdf.savefig(fig); plt.close(fig)
    print(f"Wrote {len(paths)} tiles to {out_pdf}")


def drop(tile_dir: Path, indices: list[int], dry_run: bool = False):
    paths = _list_tiles(tile_dir)
    bad_dir = tile_dir.parent / f"{tile_dir.name}_bad"
    bad_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for i in indices:
        if not (0 <= i < len(paths)):
            print(f"  skipping out-of-range index {i}")
            continue
        src = paths[i]; dst = bad_dir / src.name
        if dry_run:
            print(f"  would move #{i:>3d}: {src.name}")
        else:
            shutil.move(str(src), str(dst))
            moved.append(src.name)
            print(f"  moved #{i:>3d}: {src.name} -> {bad_dir.name}/")
    print(f"\n{'(dry run) ' if dry_run else ''}moved {len(moved)} tiles to {bad_dir}")
    if not dry_run and moved:
        print(f"To restore: python scripts/tile_review.py restore --tiles {tile_dir.name}")


def restore(tile_dir: Path):
    bad_dir = tile_dir.parent / f"{tile_dir.name}_bad"
    if not bad_dir.exists():
        print(f"no {bad_dir}; nothing to restore")
        return
    n = 0
    for f in sorted(bad_dir.glob("*.npz")):
        shutil.move(str(f), str(tile_dir / f.name))
        n += 1
    print(f"restored {n} tiles from {bad_dir}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("render")
    rp.add_argument("--tiles", default=str(DEFAULT_TILES))
    rp.add_argument("--out", default=str(DEFAULT_PDF))

    dp = sub.add_parser("drop")
    dp.add_argument("indices", nargs="+", type=int)
    dp.add_argument("--tiles", default=str(DEFAULT_TILES))
    dp.add_argument("--dry-run", action="store_true")

    rsp = sub.add_parser("restore")
    rsp.add_argument("--tiles", default=str(DEFAULT_TILES))

    args = ap.parse_args()
    tiles = Path(args.tiles)
    if not tiles.is_absolute():
        tiles = REPO / args.tiles

    if args.cmd == "render":
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO / args.out
        render(tiles, out)
    elif args.cmd == "drop":
        drop(tiles, args.indices, args.dry_run)
    elif args.cmd == "restore":
        restore(tiles)


if __name__ == "__main__":
    main()
