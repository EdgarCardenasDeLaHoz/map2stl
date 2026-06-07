"""Optional per-building event recorder for `estimate_heights_from_registration`.

When passed via the `trace` kwarg, this recorder captures one event row at every
gate / decision point of the height estimator. Used by `scripts/09_height_trace.py`
to walk a specific failing building (tag ~ 146 m, pred ~ 32 m) end-to-end and
identify which gate is responsible for the under-prediction.

The recorder is behaviour-neutral: when not passed, the estimator runs unchanged.
When `only_feature_id` is set, only events for that building are recorded —
useful when tracing a single target across all spin views of a region.

Event schema (every row):
  stage       — fixed identifier from STAGES below
  view_name   — captured.viewpoint.name
  feature_id  — BuildingRecord.feature_id

  Plus stage-specific fields (forward_m, x_px, roof_y_mask, gap, height_m, …).
  See STAGES for the full list and which fields each stage emits.

Serialisation: events are JSON-serialisable dicts; non-finite floats are
coerced to None in `to_json_ready()` so the output round-trips through stdlib
json without NaN/Inf gymnastics.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


# Ordered list of event stages emitted by estimate_heights_from_registration.
# Keep in sync with the trace calls in pipeline.py — adding a new stage requires
# updating this list AND the script schema in 09_height_trace.py.
STAGES: tuple[str, ...] = (
    "building_start",
    "drop_no_projection",
    "drop_x_out_of_bounds",
    "drop_forward_too_close",
    "closest_in_bin",
    "drop_closest_in_bin",
    "roof_y_from_mask",
    "contour_override",
    "drop_contour_nan",
    "pinhole_math",
    "drop_height_nan",
    "geometric_y_gate",
    "drop_geometric_gate",
    "drop_plausibility_tag",
    # Tag-disagreement filter (pipeline.py): drops a tagged building's per-view
    # estimate when |pred - tag| > min(2*tag, 50 m). This is the gate that
    # silently discards under-predicting glass-tower views, so it MUST be a
    # recorded stage for the Phase-1 height trace to see the failure.
    "drop_tag_disagreement",
    "drop_plausibility_area",
    "emit",
)


class HeightTraceRecorder:
    """Captures per-(view, building, stage) events from the height estimator.

    Pass to `estimate_heights_from_registration(..., trace=recorder)` to log
    every gate decision into `recorder.events`. Defaults: record everything.
    Pass `only_feature_id="b0389"` to skip events for other buildings.
    """

    def __init__(self, *, only_feature_id: str | None = None) -> None:
        self.only_feature_id = only_feature_id
        self.events: list[dict[str, Any]] = []
        # Per-view artefacts captured so the diagnostic renderer can draw the
        # rgb / contour / mask without re-running SegFormer. Populated by
        # `save_view`, keyed by view_name. Each value is a dict with keys
        # {"image", "contour", "mask"} where mask may be None.
        self.view_artifacts: dict[str, dict[str, Any]] = {}

    def __call__(self, stage: str, *, view_name: str, feature_id: str, **fields: Any) -> None:
        if self.only_feature_id is not None and feature_id != self.only_feature_id:
            return
        if stage not in STAGES:
            # Caller bug — fail loud so we don't silently drop trace points.
            raise ValueError(f"unknown trace stage: {stage!r}")
        self.events.append(
            {"stage": stage, "view_name": view_name, "feature_id": feature_id, **fields}
        )

    def save_view(self, view_name: str, image, contour, mask) -> None:
        """Record one view's RGB / contour / mask for later rendering.

        Idempotent — first call per view_name wins. Arrays are copied so the
        in-memory LRU in pipeline.py can free them safely.
        """
        if view_name in self.view_artifacts:
            return
        self.view_artifacts[view_name] = {
            "image": np.asarray(image).copy(),
            "contour": np.asarray(contour).copy(),
            "mask": np.asarray(mask).copy() if mask is not None else None,
        }

    def events_for(self, feature_id: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["feature_id"] == feature_id]

    def feature_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for e in self.events:
            seen.setdefault(e["feature_id"], None)
        return list(seen.keys())

    def to_json_ready(self) -> list[dict[str, Any]]:
        """Coerce NaN / ±Inf floats to None so json.dump works without allow_nan."""
        return [_coerce_finite(e) for e in self.events]


def _coerce_finite(obj: Any) -> Any:
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _coerce_finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_coerce_finite(v) for v in obj]
    return obj
