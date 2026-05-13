"""Manual review helpers for the skyline CV baseline.

The automatic registration is intentionally simple. This module creates a
human-editable bundle with annotated skyline overlays and per-view candidate
heights so the user can correct weak matches before fusing the results.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from .config import RunSpec
from .pipeline import (
    BuildingRecord,
    CapturedView,
    RegisteredBuildingEstimate,
    _annotate_view,
    _ensure_dir,
    _write_json,
    aggregate_building_heights,
    detect_skyline_contour,
    estimate_heights_from_registration,
    register_view_to_osm,
)


def _group_estimates_by_view(estimates: Sequence[RegisteredBuildingEstimate]) -> dict[str, list[RegisteredBuildingEstimate]]:
    grouped: dict[str, list[RegisteredBuildingEstimate]] = {}
    for estimate in estimates:
        grouped.setdefault(estimate.view_name, []).append(estimate)
    return grouped


def create_review_bundle(
    run: RunSpec,
    buildings: Sequence[BuildingRecord],
    captured_views: Sequence[CapturedView],
    output_dir: Path | None = None,
) -> dict:
    """Create a human-editable review bundle with annotated overlays."""

    bundle_dir = output_dir or (run.output_dir / "06_review")
    _ensure_dir(bundle_dir)

    review_views: list[dict] = []
    all_estimates: list[RegisteredBuildingEstimate] = []

    for captured in captured_views:
        registration = register_view_to_osm(
            captured,
            buildings,
            heading_search_deg=run.heading_search_deg,
            heading_step_deg=run.heading_step_deg,
        )
        estimates = estimate_heights_from_registration(
            captured,
            registration,
            buildings,
            camera_height_m=run.camera_height_m,
        )
        all_estimates.extend(estimates)

        annotated_path = bundle_dir / \
            f"{captured.viewpoint.name}_{int(round(captured.viewpoint.heading))%360:03d}_review.png"
        _annotate_view(
            captured,
            registration,
            annotated_path,
            title=f"{captured.viewpoint.name} | offset={registration['best_offset']:+.1f}° | score={registration['best_score']:.1f}",
        )

        contour, _ = detect_skyline_contour(captured.image)
        view_rows = []
        for estimate in estimates:
            view_rows.append(
                {
                    "feature_id": estimate.feature_id,
                    "name": estimate.name,
                    "estimated_height_m": estimate.estimated_height_m,
                    "confidence": estimate.confidence,
                    "heading_offset_deg": estimate.heading_offset_deg,
                    "manual_keep": True,
                    "manual_height_m": None,
                    "manual_confidence": None,
                }
            )

        review_views.append(
            {
                "view_name": captured.viewpoint.name,
                "query": captured.viewpoint.query,
                "image_path": str(captured.image_path),
                "annotated_path": str(annotated_path),
                "best_offset_deg": float(registration["best_offset"]),
                "best_score": float(registration["best_score"]),
                "contour_path": str(bundle_dir / f"{captured.viewpoint.name}_contour.npy"),
                "contour_sample_px": contour.astype(np.float32).tolist(),
                "candidates": view_rows,
            }
        )

        np.save(
            bundle_dir / f"{captured.viewpoint.name}_contour.npy", contour.astype(np.float32))

    bundle = {
        "site": {
            "name": run.site.name,
            "north": run.site.north,
            "south": run.site.south,
            "east": run.site.east,
            "west": run.site.west,
        },
        "bundle_dir": str(bundle_dir),
        "views": review_views,
    }
    _write_json(bundle_dir / "review_bundle.json", bundle)
    _write_json(bundle_dir / "view_estimates.json",
                [asdict(item) for item in all_estimates])
    _write_json(
        bundle_dir / "building_summary.json",
        aggregate_building_heights(all_estimates),
    )
    return bundle


def apply_review_bundle(bundle_path: Path) -> dict:
    """Apply manual edits from a review bundle and return a new summary."""

    bundle = json.loads(bundle_path.read_text())
    corrected: list[RegisteredBuildingEstimate] = []

    for view in bundle.get("views", []):
        view_name = view.get("view_name", "unknown")
        best_offset = float(view.get("best_offset_deg", 0.0))
        for candidate in view.get("candidates", []):
            if not candidate.get("manual_keep", True):
                continue

            height = candidate.get("manual_height_m")
            if height is None:
                height = candidate.get("estimated_height_m")

            confidence = candidate.get("manual_confidence")
            if confidence is None:
                confidence = candidate.get("confidence", 0.5)

            corrected.append(
                RegisteredBuildingEstimate(
                    feature_id=str(candidate.get("feature_id")),
                    name=str(candidate.get(
                        "name", candidate.get("feature_id"))),
                    view_name=view_name,
                    heading_offset_deg=best_offset,
                    x_px=float(candidate.get("x_px", 0.0)),
                    y_px=float(candidate.get("y_px", 0.0)),
                    forward_m=float(candidate.get("forward_m", 0.0)),
                    estimated_height_m=float(height),
                    confidence=float(confidence),
                )
            )

    summary = aggregate_building_heights(corrected)
    output_dir = bundle_path.parent
    _write_json(output_dir / "manual_building_heights.json", summary)
    _write_json(output_dir / "manual_view_estimates.json",
                [asdict(item) for item in corrected])
    return {
        "bundle": str(bundle_path),
        "views": len(bundle.get("views", [])),
        "corrected_estimates": len(corrected),
        "aggregated_buildings": len(summary),
        "output_dir": str(output_dir),
    }
