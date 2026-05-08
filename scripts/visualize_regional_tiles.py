"""Custom visualization: display specific regional tiles with model predictions."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
from matplotlib.backends.backend_pdf import PdfPages

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ml.models import Retna_V1
from tools.ml.train.train_retna import HEIGHT_NORM_M

REPO = Path(__file__).resolve().parents[1]


def main():
    """Visualize specific regional tiles with model predictions."""

    checkpoint_path = REPO / "models" / "retna_phase_g_test.pt"
    tiles_dir = REPO / "cache" / "height_tiles_combined"
    output_pdf = REPO / "output" / "phase_g_regional_tiles_custom.pdf"

    if not checkpoint_path.exists():
        print(f"ERROR: Model not found: {checkpoint_path}")
        return 1

    if not tiles_dir.exists():
        print(f"ERROR: Tiles dir not found: {tiles_dir}")
        return 1

    # Load model
    state = torch.load(str(checkpoint_path), map_location="cpu", weights_only=False)
    hidden = state.get("hidden_channels", [8, 8, 8, 8])
    norm_m = state.get("height_norm_m", HEIGHT_NORM_M)

    model = Retna_V1(in_channels=3, out_classes=1, hidden_channels=hidden)
    model.load_state_dict(state["model_state_dict"])
    model.eval()

    print(f"Model: Retna_V1 {hidden}, {sum(p.numel() for p in model.parameters()):,} params")
    print(f"Checkpoint: {checkpoint_path}")

    # Regional tile selection (all 100 tiles sorted)
    all_tiles = sorted(tiles_dir.glob("*.npz"))
    print(f"Total tiles available: {len(all_tiles)}")

    # Select tiles from different regions and positions
    # These are sorted indices covering the full dataset
    tile_indices = [
        0,    # Amsterdam_0000_0000
        5,    # Amsterdam_0000_0005
        10,   # Amsterdam_0001_0001
        20,   # Amsterdam_0002_0002
        30,   # Amsterdam_0003_0003
        40,   # Amsterdam_0004_0004
        50,   # Amsterdam_0005_0005
        55,   # Amsterdam_0006_0001
        60,   # Barcelona_0000_0001
        70,   # Barcelona_0000_0011
        75,   # Barcelona_0001_0002
        80,   # Barcelona_0001_0007
        85,   # Barcelona_0001_0012
        92,   # Barcelona_0002_0005
        99,   # Barcelona_0002_0012
    ]

    selected_tiles = [all_tiles[i] for i in tile_indices if i < len(all_tiles)]
    print(f"\nSelecting {len(selected_tiles)} regional tiles:")
    for i, tile_path in enumerate(selected_tiles, 1):
        print(f"  {i:2d}. {tile_path.name}")

    # Load and predict
    samples = []
    with torch.no_grad():
        for tile_path in selected_tiles:
            data = np.load(tile_path)
            rgb = data["rgb"]  # (3, 256, 256)
            height = data["height"][0]  # (256, 256) in meters

            # Resize to 128x128 for model
            from scipy.ndimage import zoom
            rgb_128 = np.array(
                [zoom(rgb[c], 128 / rgb.shape[1], order=1) for c in range(3)]
            )
            height_128 = zoom(height, 128 / height.shape[0], order=1)

            # Normalize to [0,1]
            rgb_norm = torch.tensor(rgb_128, dtype=torch.float32).unsqueeze(0)
            height_norm = torch.tensor(height_128 / norm_m, dtype=torch.float32).unsqueeze(0).unsqueeze(0)

            # Predict
            pred_norm = model(rgb_norm)
            pred_m = pred_norm.squeeze().numpy() * norm_m

            # Upscale prediction back to 256x256 for visualization
            pred_256 = zoom(pred_m, 256 / 128, order=1)
            height_256 = height

            # Metrics
            mask = height_256 > 0
            if mask.any():
                mae = float(np.abs(pred_256[mask] - height_256[mask]).mean())
            else:
                mae = 0.0

            samples.append({
                "name": tile_path.stem,
                "rgb": rgb,
                "target_m": height_256,
                "pred_m": pred_256,
                "mae": mae,
            })

    print(f"\nGenerating PDF with {len(samples)} regional tiles...")

    # Render PDF
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    with PdfPages(str(output_pdf)) as pdf:
        # Title page
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle("Phase G Regional Tile Sampling", fontsize=16, fontweight="bold")
        ax = fig.add_subplot(111)
        ax.axis("off")

        summary_text = (
            f"Model: Retna_V1 {hidden}\n"
            f"Checkpoint: retna_phase_g_test.pt\n"
            f"Total validation tiles: {len(selected_tiles)}\n"
            f"\n"
            f"Tile Coverage:\n"
            f"  • Amsterdam: {sum(1 for s in samples if 'Amsterdam' in s['name'])} tiles\n"
            f"  • Barcelona: {sum(1 for s in samples if 'Barcelona' in s['name'])} tiles\n"
            f"\n"
            f"Metrics Summary:\n"
        )

        maes = [s["mae"] for s in samples]
        summary_text += (
            f"  Mean MAE: {np.mean(maes):.2f}m\n"
            f"  Median MAE: {np.median(maes):.2f}m\n"
            f"  Std MAE: {np.std(maes):.2f}m\n"
            f"  Min MAE: {np.min(maes):.2f}m\n"
            f"  Max MAE: {np.max(maes):.2f}m\n"
        )

        ax.text(0.05, 0.95, summary_text, transform=ax.transAxes, fontsize=11,
                verticalalignment="top", family="monospace")
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)

        # Per-tile pages
        for rank, s in enumerate(samples, 1):
            rgb = s["rgb"].transpose(1, 2, 0)
            gt = s["target_m"]
            pr = s["pred_m"]
            err = pr - gt
            vmax = float(max(gt.max(), pr.max(), 1.0))
            ev = float(max(abs(err.min()), abs(err.max()), 1.0))

            fig, axes = plt.subplots(1, 4, figsize=(16, 4))
            axes[0].imshow(np.clip(rgb, 0, 1))
            axes[0].set_title("RGB Satellite")

            axes[1].imshow(gt, cmap="viridis", vmin=0, vmax=vmax)
            axes[1].set_title(f"Ground Truth Height (max={gt.max():.1f}m)")

            axes[2].imshow(pr, cmap="viridis", vmin=0, vmax=vmax)
            axes[2].set_title(f"Predicted Height (max={pr.max():.1f}m)")

            im = axes[3].imshow(err, cmap="RdBu_r", vmin=-ev, vmax=ev)
            axes[3].set_title(f"Error (MAE={s['mae']:.2f}m)")

            for ax in axes:
                ax.set_xticks([])
                ax.set_yticks([])

            fig.colorbar(im, ax=axes[3], shrink=0.7)
            fig.suptitle(f"[{rank}/{len(samples)}] {s['name']}", fontsize=11)
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

    print(f"\n[OK] Wrote {len(samples) + 1} pages -> {output_pdf}")
    print(f"\nPDF contains:")
    print(f"  • Page 1: Summary statistics")
    print(f"  • Pages 2-{len(samples)+1}: Individual regional tiles")
    print(f"\nTile selection covers:")
    print(f"  • Amsterdam: evenly spaced across grid")
    print(f"  • Barcelona: evenly spaced across grid")

    return 0


if __name__ == "__main__":
    sys.exit(main())
