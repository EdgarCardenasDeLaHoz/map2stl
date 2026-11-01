"""Part of ``city2stl.skyline._core.height`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .height_withhold import _seed_from_view_name
from .types import RegisteredBuildingEstimate  # noqa: E402


def aggregate_building_heights(estimates: Sequence[RegisteredBuildingEstimate]) -> list[dict]:
    grouped: dict[str, list[RegisteredBuildingEstimate]] = {}
    for estimate in estimates:
        grouped.setdefault(estimate.feature_id, []).append(estimate)

    output: list[dict] = []
    for feature_id, items in grouped.items():
        heights = np.asarray(
            [item.estimated_height_m for item in items], dtype=np.float32)
        _confidences = np.asarray(
            [item.confidence for item in items], dtype=np.float32)
        if heights.size == 0:
            continue

        # Per-seed grouping for cross-seed agreement and outlier downweighting.
        per_seed_items: dict[str, list[RegisteredBuildingEstimate]] = {}
        for item in items:
            seed_name = _seed_from_view_name(item.view_name)
            per_seed_items.setdefault(seed_name, []).append(item)

        # Per-view outlier rejection inside each seed: a single dissenting
        # view (rooftop occluded by a tree, mis-bounded segment, depth
        # blowup) can pull a seed's median 20+ m. When a seed has ≥3
        # views of the same building, drop any view that's more than
        # 2.5 MAD from the seed's per-view median before computing the
        # seed's authoritative height. Improves cross-seed agreement
        # numbers and aligns with the cross-view smoothing of matches
        # (no point trusting a view whose match was overridden because
        # the consensus disagreed).
        per_view_outliers_dropped = 0
        for seed_name, seed_its in list(per_seed_items.items()):
            if len(seed_its) < 3:
                continue
            view_heights = np.asarray(
                [it.estimated_height_m for it in seed_its], dtype=np.float32)
            v_med = float(np.median(view_heights))
            v_mad = float(np.median(np.abs(view_heights - v_med)))
            if v_mad < 1.0:
                continue
            v_threshold = 2.5 * v_mad
            kept = [
                it for it, h in zip(seed_its, view_heights, strict=False)
                if abs(float(h) - v_med) <= v_threshold
            ]
            if kept and len(kept) < len(seed_its):
                per_view_outliers_dropped += (len(seed_its) - len(kept))
                per_seed_items[seed_name] = kept

        per_seed_median = {
            s: float(np.median([it.estimated_height_m for it in seed_its]))
            for s, seed_its in per_seed_items.items()}

        # Outlier-seed downweighting: when ≥ 3 seeds disagree, find seeds
        # whose per-seed median is > 1.5 × MAD from the overall seed-median
        # median, and zero their contribution. This rejects an outlier seed
        # entirely (e.g. one bad heading anchor) rather than letting it pull
        # the aggregate. For 2 seeds we can't tell which is the outlier, so
        # we leave both in.
        outlier_seeds: set[str] = set()
        if len(per_seed_median) >= 3:
            vals = np.asarray(list(per_seed_median.values()), dtype=np.float32)
            seed_median_overall = float(np.median(vals))
            seed_mad = float(np.median(np.abs(vals - seed_median_overall)))
            if seed_mad >= 1.0:  # only meaningful with non-trivial spread
                threshold = 1.5 * seed_mad
                for s, m in per_seed_median.items():
                    if abs(m - seed_median_overall) > threshold:
                        outlier_seeds.add(s)

        # Build the effective items/heights/confidences (excluding outliers).
        # ``items`` is the raw input; rebuild from the per-view-filtered
        # per_seed_items so per-view outliers are also removed.
        survivors_set = set()
        for seed_name, seed_its in per_seed_items.items():
            if seed_name in outlier_seeds:
                continue
            for it in seed_its:
                survivors_set.add(id(it))
        eff_items = [it for it in items if id(it) in survivors_set]
        if not eff_items:
            eff_items = list(items)  # all were outliers — fall back to raw
        eff_heights = np.asarray(
            [it.estimated_height_m for it in eff_items], dtype=np.float32)
        eff_confidences = np.asarray(
            [it.confidence for it in eff_items], dtype=np.float32)

        # F-SKY12 Phase B: downweight views where depth says the building is
        # significantly shorter than the geometric estimate. This fires when
        # the geometric path is likely chasing a false silhouette top (reflection
        # or adjacent structure above the actual roofline). threshold = 0.7×:
        # depth must say < 70% of geometric before we penalise it.
        eff_confidences_adj = eff_confidences.copy()
        for _i, _it in enumerate(eff_items):
            _dh = getattr(_it, "depth_height_m", None)
            _dd = getattr(_it, "depth_disagreement", None)
            if (_dd and _dh is not None and _dh > 0.0
                    and _it.estimated_height_m > 0.0
                    and _dh < _it.estimated_height_m * 0.70):
                eff_confidences_adj[_i] *= 0.5

        median = float(np.median(eff_heights))
        spread = float(np.median(np.abs(eff_heights - median)))
        weighted = float(np.average(
            eff_heights, weights=np.maximum(eff_confidences_adj, 1e-3)))

        # F-SKY1: collect floor-period height estimates from views with a
        # usable autocorrelation lock (confidence ≥ 0.30, ≥2 views required
        # to guard against single-facade noise). These are OSM-independent.
        _f1_valid = [
            float(_it.inferred_height_m)
            for _it in eff_items
            if getattr(_it, "inferred_height_m", None) is not None
            and getattr(_it, "floor_confidence", None) is not None
            and _it.floor_confidence >= 0.30
            and _it.inferred_height_m > 0.0
        ]
        f1_height_m: float | None = None
        f1_n_views: int = 0
        if len(_f1_valid) >= 2:
            f1_height_m = float(np.median(np.asarray(_f1_valid, dtype=np.float32)))
            f1_n_views = len(_f1_valid)

        # F-SKY12 Phase B: collect depth-rescue heights — views where DA2 says
        # the building is significantly taller than the geometric estimate
        # (under-prediction case: glass towers, small sky gap). Require ≥2
        # agreeing views to avoid single-view DA2 artefacts.
        _d_rescue = [
            float(_it.depth_height_m)
            for _it in eff_items
            if getattr(_it, "depth_height_m", None) is not None
            and _it.depth_height_m > 0.0
            and getattr(_it, "depth_disagreement", None)
            and _it.depth_height_m > _it.estimated_height_m * 1.30
        ]
        depth_rescue_height_m: float | None = None
        if len(_d_rescue) >= 2:
            depth_rescue_height_m = float(
                np.median(np.asarray(_d_rescue, dtype=np.float32)))

        # Effective height: geometric median, rescued upward by F-SKY1 or
        # depth when they agree the building is ≥40% taller than geometric.
        # Only rescues upward — over-prediction is rare in this pipeline and
        # downward correction without cross-validation would hurt recall.
        effective_height_m: float = median
        effective_height_source: str = "geometric"
        for _h_alt, _src in (
            (f1_height_m, "f_sky1"),
            (depth_rescue_height_m, "depth_rescue"),
        ):
            if _h_alt is not None and _h_alt > effective_height_m * 1.40:
                effective_height_m = _h_alt
                effective_height_source = _src

        if len(per_seed_median) >= 2:
            vals = np.asarray(list(per_seed_median.values()), dtype=np.float32)
            seed_disagreement_m = float(np.max(vals) - np.min(vals))
            seed_std_m = float(np.std(vals))
        else:
            seed_disagreement_m = 0.0
            seed_std_m = 0.0

        output.append(
            {
                "feature_id": feature_id,
                "name": items[0].name,
                "n_views": int(heights.size),
                "n_views_after_outlier_filter": int(eff_heights.size),
                "n_view_outliers_dropped": int(per_view_outliers_dropped),
                "n_seeds": len(per_seed_items),
                "n_outlier_seeds": len(outlier_seeds),
                "outlier_seeds": sorted(outlier_seeds),
                "median_height_m": median,
                "weighted_height_m": weighted,
                "mad_m": spread,
                # Reported from the same weights the weighted mean uses, so
                # a depth-downweighted view shows up in both.
                # DIAGNOSTIC, not accuracy: confidence and seed disagreement do not track
                # error (corr 0.02-0.16); reports show n_views and anchor_dev_m instead.
                "mean_confidence": float(np.mean(eff_confidences_adj)),
                "per_seed_median_m": per_seed_median,
                "seed_disagreement_m": seed_disagreement_m,
                "seed_std_m": seed_std_m,
                # Photo anchor deviation: None here (a Street View only row has no photo anchor);
                # the photo pipeline fills it.
                "anchor_dev_m": None,
                # F-SKY1: OSM-independent floor-count height (None when <2
                # views locked a facade period).
                "f_sky1_height_m": f1_height_m,
                "f_sky1_n_views": f1_n_views,
                # F-SKY12 Phase B: depth-rescue height (None when <2 views
                # had DA2 > geometric × 1.3).
                "depth_rescue_height_m": depth_rescue_height_m,
                # Effective height: geometric median rescued upward by F-SKY1
                # or depth when they agree the building is ≥40% taller.
                "effective_height_m": effective_height_m,
                "effective_height_source": effective_height_source,
                "views": [
                    {
                        "view_name": item.view_name,
                        "height_m": item.estimated_height_m,
                        "confidence": item.confidence,
                        "heading_offset_deg": item.heading_offset_deg,
                    }
                    for item in items
                ],
            }
        )

    output.sort(
        key=lambda row: (-row["n_seeds"], -row["n_views"], -row["median_height_m"]))
    return output
