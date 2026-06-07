"""1-page diagnostic image for a HeightTraceRecorder run.

Draws one matplotlib row per view-with-target: cropped RGB with the contour,
the mask overlay, the building's projected x-range, the mask roof_y, the
contour_top_y, and the final y_px — annotated with each gate's verdict.

Stays optional: imported lazily by scripts/09_height_trace.py so the trace
JSON still writes even when matplotlib (already a skyline hard dep) hits
some unrelated init issue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from .height_trace import HeightTraceRecorder


def _events_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """First event per stage for the building in a view — gates can only fire once."""
    out: dict[str, dict[str, Any]] = {}
    for e in events:
        out.setdefault(e["stage"], e)
    return out


def _gate_summary_lines(idx: dict[str, dict[str, Any]]) -> list[str]:
    """Compact stage-by-stage verdict text rendered alongside the panel."""
    lines: list[str] = []
    if "building_start" in idx:
        b = idx["building_start"]
        tag = b.get("tag_h")
        tag_str = f"{tag:.1f}m" if isinstance(tag, (int, float)) else "untagged"
        lines.append(
            f"tag_h={tag_str}  area={b.get('area_m2', 0):.0f}m²  "
            f"terrain={b.get('terrain_elev_m', 0):.1f}m"
        )
    if "closest_in_bin" in idx:
        c = idx["closest_in_bin"]
        lines.append(
            f"closest-in-bin: forward={c['forward_m']:.0f}m vs "
            f"nearest={c['closest_in_bin_m']:.0f}m ({c['rivals_in_bin']} rivals)"
        )
    for drop in ("drop_no_projection", "drop_x_out_of_bounds",
                 "drop_forward_too_close", "drop_closest_in_bin",
                 "drop_contour_nan", "drop_height_nan",
                 "drop_geometric_gate", "drop_plausibility_tag",
                 "drop_plausibility_area"):
        if drop in idx:
            lines.append(f"DROP at {drop}: {idx[drop]}")
    if "roof_y_from_mask" in idx:
        r = idx["roof_y_from_mask"]
        lines.append(
            f"mask roof_y={r['roof_y_mask']}  coverage={r['coverage']}  "
            f"x_range={r['x_range']}"
        )
    if "contour_override" in idx:
        o = idx["contour_override"]
        verdict = "FIRED" if o["fired"] else "skipped"
        lines.append(
            f"contour override: gap={o['gap_px']:.1f}px → {verdict}  "
            f"contour_top_y={o['contour_top_y']:.1f}  implied_h={o['implied_h_m']}"
        )
    if "pinhole_math" in idx:
        m = idx["pinhole_math"]
        lines.append(
            f"pinhole: y_px={m['y_px']:.1f}  angle={np.degrees(m['angle_rad']):.2f}°  "
            f"forward={m['forward_m']:.0f}m  → height={m['height_m']:.1f}m"
        )
    if "geometric_y_gate" in idx:
        g = idx["geometric_y_gate"]
        lines.append(
            f"geo y-gate: y={g['y_px']:.1f} in [{g['min_y_for_building']:.1f}, "
            f"{g['max_y_for_building']:.1f}]"
        )
    if "emit" in idx:
        em = idx["emit"]
        lines.append(
            f"EMIT: height={em['height_m']:.1f}m  conf={em['confidence']:.2f}"
        )
    return lines


def _draw_view_panel(ax_img, ax_txt, artefacts: dict[str, Any],
                     events: list[dict[str, Any]]) -> None:
    image = artefacts["image"]
    contour = np.asarray(artefacts["contour"], dtype=np.float32)
    mask = artefacts.get("mask")
    idx = _events_index(events)

    ax_img.imshow(image)
    # Contour line (matches the cyan line in region_pdf overlays).
    if contour.size:
        xs = np.arange(contour.size)
        finite = np.isfinite(contour)
        ax_img.plot(xs[finite], contour[finite], color="#00DCFF",
                    linewidth=1.2, alpha=0.85, label="contour")

    # Building mask outline.
    if mask is not None:
        try:
            ax_img.contour(mask.astype(bool), levels=[0.5],
                           colors=["#FFD24A"], linewidths=0.8, alpha=0.7)
        except Exception:
            pass

    # Projected x_range vertical band.
    r = idx.get("roof_y_from_mask")
    if r and r.get("x_range"):
        xL, xR = r["x_range"]
        ax_img.axvspan(xL, xR, color="#E55", alpha=0.18,
                       label="projected x_range")
    # Mask roof_y dot.
    if r and r.get("roof_y_mask") is not None and r.get("x_range"):
        xL, xR = r["x_range"]
        ax_img.plot([(xL + xR) / 2.0], [r["roof_y_mask"]],
                    marker="o", color="#FF6", markersize=8,
                    markeredgecolor="black", label="mask roof_y")
    # Contour-override top_y dot.
    o = idx.get("contour_override")
    if o and o.get("contour_top_y") is not None and r and r.get("x_range"):
        xL, xR = r["x_range"]
        col = "#5F5" if o["fired"] else "#888"
        ax_img.plot([(xL + xR) / 2.0], [o["contour_top_y"]],
                    marker="^", color=col, markersize=10,
                    markeredgecolor="black", label="contour_top_y")
    # Final y_px used in pinhole math.
    m = idx.get("pinhole_math")
    if m and r and r.get("x_range"):
        xL, xR = r["x_range"]
        ax_img.plot([(xL + xR) / 2.0], [m["y_px"]], marker="x",
                    color="#F0F", markersize=12, markeredgewidth=2,
                    label="final y_px")

    view_name = events[0]["view_name"] if events else "?"
    fid = events[0]["feature_id"] if events else "?"
    ax_img.set_title(f"{view_name}  |  {fid}", fontsize=9)
    ax_img.set_xlim(0, image.shape[1])
    ax_img.set_ylim(image.shape[0], 0)
    ax_img.legend(loc="lower right", fontsize=6, framealpha=0.85)
    ax_img.tick_params(labelsize=6)

    # Text panel.
    ax_txt.axis("off")
    lines = _gate_summary_lines(idx)
    ax_txt.text(
        0.0, 1.0, "\n".join(lines) or "(no events)",
        va="top", ha="left", family="monospace", fontsize=7,
        transform=ax_txt.transAxes, wrap=True,
    )


def render_trace_diagnostic(
    recorder: HeightTraceRecorder,
    out_path: Path,
    *,
    target: str | None = None,
) -> None:
    """Render one PNG with a per-view panel for the target building.

    When `target` is None, falls back to the most-traced feature_id (the one
    with the most events recorded). If the recorder is empty, writes a
    placeholder page so the script's "wrote PNG" claim stays honest.
    """
    if target is None:
        if recorder.events:
            counts: dict[str, int] = {}
            for e in recorder.events:
                counts[e["feature_id"]] = counts.get(e["feature_id"], 0) + 1
            target = max(counts.items(), key=lambda kv: kv[1])[0]
        else:
            target = "(no events)"

    # Group events by view for the target.
    events_by_view: dict[str, list[dict[str, Any]]] = {}
    for e in recorder.events:
        if target and e["feature_id"] != target:
            continue
        events_by_view.setdefault(e["view_name"], []).append(e)

    views = sorted(events_by_view.keys())
    if not views:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.axis("off")
        ax.text(0.5, 0.5,
                f"No trace events for {target!r}.\n"
                "Building may not have been in any seed view's FOV.",
                ha="center", va="center", fontsize=11)
        fig.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return

    n = len(views)
    fig = plt.figure(figsize=(12, max(3.0, 2.5 * n)))
    for i, view_name in enumerate(views):
        artefacts = recorder.view_artifacts.get(view_name)
        if artefacts is None:
            # No image was saved (e.g. trace target wasn't in projections for
            # this view). Draw an empty image panel so the text still shows.
            artefacts = {"image": np.zeros((50, 50, 3), dtype=np.uint8),
                         "contour": np.array([]), "mask": None}
        ax_img = fig.add_subplot(n, 2, 2 * i + 1)
        ax_txt = fig.add_subplot(n, 2, 2 * i + 2)
        _draw_view_panel(ax_img, ax_txt, artefacts, events_by_view[view_name])

    fig.suptitle(f"Height trace — {target}", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
