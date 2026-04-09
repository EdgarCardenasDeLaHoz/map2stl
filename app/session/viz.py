"""
viz.py — Shared visualisation utilities for strm2stl notebooks.

Migrated from notebooks/figure.py.
"""

import numpy as np
import matplotlib.pyplot as plt


def plot_data(im, name=None, bbox=None, colormap="terrain", close=False):
    """Display a DEM array with terrain/rainbow colormaps, grayscale with grid,
    and cross-section profiles.

    Parameters
    ----------
    im : ndarray
        2-D elevation array (will be vertically flipped for display).
    name : str, optional
        Title applied to figures.
    bbox : sequence, optional
        [west, south, east, north] passed to imshow extent.
    colormap : str, optional
        Primary colormap for the first subplot (default 'terrain').
    close : bool, optional
        Close all existing figures before plotting.
    """
    im = im.copy()

    if close:
        plt.close("all")

    # ── Figure 1: colour maps ───────────────────────────────────────────────
    fig, axs = plt.subplots(1, 2, figsize=(12, 6), layout="tight",
                            sharex=True, sharey=True)
    pcm = axs[0].imshow(im, cmap=colormap)
    fig.colorbar(pcm, ax=axs[0])
    axs[0].set_title(colormap)

    pcm = axs[1].imshow(im, cmap="rainbow")
    fig.colorbar(pcm, ax=axs[1])
    axs[1].set_title("rainbow")

    for ax in axs:
        ax.grid(True)

    if name is not None:
        fig.suptitle(name)

    # ── Figure 2: grayscale + cross-section profiles ────────────────────────
    fig2, axs2 = plt.subplots(1, 2, figsize=(12, 6), layout="tight")

    # imshow extent = [left, right, bottom, top] = [west, east, south, north]
    west, south, east, north = bbox if bbox is not None else [0, 0, 1, 1]
    axs2[0].imshow(im, cmap="gray", extent=[west, east, south, north])
    axs2[0].grid(True, color="red")
    axs2[0].set_title("grayscale (geo extent)")

    axs2[1].imshow(im, cmap="gray")
    axs2[1].set_title("profiles")

    x_range = np.arange(0, im.shape[0], 50)
    y_range = np.arange(0, im.shape[1], 50)

    for i in x_range:
        y = np.concatenate([[0], im[i], [0]])
        y = -y + i
        axs2[1].axhline(i)
        axs2[1].fill(y)

    for i in y_range:
        y = -im[:, i] + i
        axs2[1].axvline(i)
        axs2[1].plot(y, range(len(im[:, i])))

    if name is not None:
        fig2.suptitle(name)

    # ── Figure 3: histogram ─────────────────────────────────────────────────
    plt.figure(figsize=(6, 3))
    plt.hist(im.ravel()[::10], bins=100)
    plt.xlabel("Elevation (m)")
    plt.ylabel("Count")
    plt.title(f"{'Elevation distribution' if name is None else name}")
    plt.tight_layout()

    print(f"shape={im.shape}  min={im.min():.1f}  max={im.max():.1f}")
