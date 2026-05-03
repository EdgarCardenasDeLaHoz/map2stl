"""
tools/eval_roof_classifier.py — Evaluate roof-shape classification quality
across the five ROOF-2 evaluation cities.

For each city the script:
  1. Fetches OSM building data (using TerrainSession or direct city2stl calls).
  2. Records the *ground-truth* set of buildings that already carry an OSM
     ``roof:shape`` tag (these are real human annotations).
  3. Runs ``classify_roof_shapes()`` on a copy of the buildings with those
     tags removed (forcing the classifier to predict them).
  4. Compares predictions to ground-truth and reports per-class accuracy.
  5. Also runs classify on the full building set (overwrite=False) to report
     coverage gain (how many un-tagged buildings now have a roof shape).
  6. Saves per-city CSVs and a combined summary to strm2stl/output/.

Usage
-----
    # from the strm2stl/ directory:
    ..\\..venv\\Scripts\\python.exe -m tools.eval_roof_classifier
    # or:
    ..\\..venv\\Scripts\\python.exe tools/eval_roof_classifier.py

Outputs
-------
  output/eval_roof_clf_<city>.csv          — per-building predictions vs GT
  output/eval_roof_clf_summary.csv         — city-level accuracy summary
  output/eval_roof_clf_confusion.txt       — per-city confusion matrix dump
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import json
import sys
import time
from collections import Counter
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EVAL_CITIES: dict[str, dict] = {
    "Amsterdam_eval": {
        "north": 52.380, "south": 52.355, "east": 4.920, "west": 4.880,
    },
    "Vienna_eval": {
        "north": 48.215, "south": 48.190, "east": 16.380, "west": 16.340,
    },
    "Prague_eval": {
        "north": 50.090, "south": 50.065, "east": 14.450, "west": 14.410,
    },
    "Berlin_eval": {
        "north": 52.535, "south": 52.510, "east": 13.415, "west": 13.375,
    },
    "Rotterdam_eval": {
        "north": 51.930, "south": 51.905, "east": 4.490, "west": 4.450,
    },
}

_SHAPE_LABELS = ["flat", "gabled", "hipped", "pyramidal", "skillion", "dome"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_tagged(features: list) -> int:
    """Number of features with a roof:shape tag."""
    return sum(
        1 for f in features
        if (f.get("properties") or {}).get("roof:shape")
    )


def _strip_roof_shapes(features: list) -> list:
    """Deep-copy features list with roof:shape tags removed."""
    stripped = copy.deepcopy(features)
    for f in stripped:
        props = f.get("properties")
        if props and "roof:shape" in props:
            del props["roof:shape"]
    return stripped


def _build_confusion_matrix(
    true_labels: list[str | None],
    pred_labels: list[str | None],
) -> dict[str, dict[str, int]]:
    """Build a per-class confusion matrix from ground-truth / prediction pairs."""
    classes = sorted({lbl for lbl in true_labels + pred_labels if lbl})
    matrix: dict[str, dict[str, int]] = {c: {d: 0 for d in classes} for c in classes}
    for t, p in zip(true_labels, pred_labels):
        if t and p and t in matrix and p in matrix[t]:
            matrix[t][p] += 1
    return matrix


def _accuracy(cm: dict[str, dict[str, int]]) -> float:
    """Overall accuracy from a confusion matrix (sum of diagonal / total)."""
    correct = sum(cm[c][c] for c in cm)
    total = sum(v for row in cm.values() for v in row.values())
    return correct / total if total else 0.0


def _format_confusion(city: str, cm: dict, classes: list[str]) -> str:
    lines = [f"=== {city} ===", "     " + "  ".join(f"{c[:6]:>6}" for c in classes)]
    for t in classes:
        row = "  ".join(f"{cm.get(t, {}).get(p, 0):>6}" for p in classes)
        lines.append(f"{t[:5]:<5}  {row}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core evaluation function
# ---------------------------------------------------------------------------

def _evaluate_city(
    name: str,
    cfg: dict,
    session: object,
    output_dir: Path,
    verbose: bool,
) -> dict:
    """Run full evaluation for a single city.  Returns summary dict."""
    import numpy as np  # noqa: F401

    summary: dict = {
        "city": name,
        "n_buildings": 0,
        "n_osm_tagged": 0,
        "n_predicted_all": 0,
        "coverage_gain": 0,
        "accuracy": None,
        "top1_shape": None,
        "clf_classified": 0,
        "clf_skipped": 0,
        "clf_unchanged": 0,
        "elapsed_s": 0.0,
        "error": None,
    }

    try:
        # Create / select region
        try:
            session.create_region(
                name=name,
                north=cfg["north"], south=cfg["south"],
                east=cfg["east"], west=cfg["west"],
            )
        except Exception:
            session.select(name)

        # Fetch OSM
        session.fetch_cities()
        if session.city_data is None:
            summary["error"] = "city_data unavailable"
            return summary

        buildings_fc = session.city_data.get("buildings", {})
        features_orig = buildings_fc.get("features", [])
        n_bldg = len(features_orig)
        n_tagged = _count_tagged(features_orig)
        summary["n_buildings"] = n_bldg
        summary["n_osm_tagged"] = n_tagged

        if verbose:
            print(f"  Buildings: {n_bldg}  OSM-tagged: {n_tagged} "
                  f"({100*n_tagged/n_bldg:.1f}%)" if n_bldg else "  No buildings found")

        bbox = (cfg["north"], cfg["south"], cfg["east"], cfg["west"])

        # ── Accuracy evaluation ─────────────────────────────────────
        # Only possible when there are buildings with GT tags
        accuracy = None
        confusion = None
        per_bldg_rows: list[dict] = []

        if n_tagged >= 5:
            from city2stl.roof_classifier import classify_roof_shapes

            # Remove GT tags so classifier must predict them
            stripped_features = _strip_roof_shapes(features_orig)
            stripped_fc = {**buildings_fc, "features": stripped_features}

            # Fetch satellite for this region (needed by classifier)
            try:
                session.fetch_satellite()
                _sat_b64 = getattr(session, "satellite", None)
                if _sat_b64 is not None:
                    from PIL import Image as _PILImage
                    _img_bytes = base64.b64decode(_sat_b64)
                    satellite_rgb = np.array(
                        _PILImage.open(BytesIO(_img_bytes)).convert("RGB"),
                        dtype=np.uint8,
                    )
                else:
                    satellite_rgb = None
            except Exception:
                satellite_rgb = None

            # Fetch height raster if available
            height_raster = None
            try:
                if getattr(session, "building_heights", None) is None:
                    session.fetch_building_heights(providers=["wsf3d"])
                bh = getattr(session, "building_heights", None)
                if bh is not None:
                    height_raster = getattr(bh, "raster", None)
            except Exception as hgt_exc:
                if verbose:
                    print(f"  ⚠️  Height fetch skipped: {hgt_exc}")

            if satellite_rgb is None:
                if verbose:
                    print("  ⚠️  No satellite image — accuracy test skipped")
            else:
                t0 = time.perf_counter()
                predicted_fc = classify_roof_shapes(
                    stripped_fc,
                    satellite_rgb=satellite_rgb,
                    bbox=bbox,
                    height_raster=height_raster,
                    estimate_roof_heights=False,
                    overwrite=True,  # overwrite=True so ALL tagged buildings get a prediction
                )
                elapsed = time.perf_counter() - t0
                summary["elapsed_s"] = round(elapsed, 1)

                # Compare predicted vs original GT tags
                pred_features = predicted_fc.get("features", [])
                clf_stats = predicted_fc.get("_stats", {})
                summary["clf_classified"] = clf_stats.get("classified", 0)
                summary["clf_skipped"] = clf_stats.get("skipped", 0)
                summary["clf_unchanged"] = clf_stats.get("unchanged", 0)

                true_labels: list[str | None] = []
                pred_labels: list[str | None] = []

                for orig_feat, pred_feat in zip(features_orig, pred_features):
                    gt_shape = (orig_feat.get("properties") or {}).get("roof:shape")
                    pred_shape = (pred_feat.get("properties") or {}).get("roof:shape")
                    if gt_shape is None:
                        continue
                    true_labels.append(gt_shape.lower().strip())
                    pred_labels.append((pred_shape or "").lower().strip() or None)

                    per_bldg_rows.append({
                        "gt_shape": gt_shape,
                        "pred_shape": pred_shape,
                        "correct": int(gt_shape == pred_shape),
                    })

                if true_labels:
                    confusion = _build_confusion_matrix(true_labels, pred_labels)
                    accuracy = _accuracy(confusion)
                    if verbose:
                        print(f"  Accuracy: {accuracy:.3f} ({int(accuracy*len(true_labels))}"
                              f"/{len(true_labels)}) in {elapsed:.1f}s")
                    summary["accuracy"] = round(accuracy, 4)

        else:
            if verbose:
                print("  ⚠️  Fewer than 5 OSM-tagged buildings — skipping accuracy test")

        # ── Coverage gain ────────────────────────────────────────────
        # Run on full building set (overwrite=False) to measure how many
        # previously-untagged buildings receive a classification.
        try:
            session.fetch_satellite()
            _sat_b64_cov = getattr(session, "satellite", None)
        except Exception:
            _sat_b64_cov = getattr(session, "satellite", None)

        if _sat_b64_cov is not None:
            from PIL import Image as _PILImage2
            _img_bytes_cov = base64.b64decode(_sat_b64_cov)
            satellite_rgb_cov = np.array(
                _PILImage2.open(BytesIO(_img_bytes_cov)).convert("RGB"),
                dtype=np.uint8,
            )
        else:
            satellite_rgb_cov = None

        if satellite_rgb_cov is not None:
            from city2stl.roof_classifier import classify_roof_shapes

            height_raster = None
            try:
                bh = getattr(session, "building_heights", None)
                if bh is not None:
                    height_raster = getattr(bh, "raster", None)
            except Exception:
                pass

            covered_fc = classify_roof_shapes(
                buildings_fc,
                satellite_rgb=satellite_rgb_cov,
                bbox=bbox,
                height_raster=height_raster,
                overwrite=False,
            )
            n_now_tagged = _count_tagged(covered_fc.get("features", []))
            gain = n_now_tagged - n_tagged
            summary["n_predicted_all"] = n_now_tagged
            summary["coverage_gain"] = gain
            if verbose:
                print(f"  Coverage gain: +{gain} buildings "
                      f"→ {n_now_tagged}/{n_bldg} tagged "
                      f"({100*n_now_tagged/n_bldg:.1f}% total)")

            # Most common predicted shape
            shapes = [
                (f.get("properties") or {}).get("roof:shape", "").lower()
                for f in covered_fc.get("features", [])
                if (f.get("properties") or {}).get("roof:shape")
            ]
            if shapes:
                top1 = Counter(shapes).most_common(1)[0][0]
                summary["top1_shape"] = top1

        # Save per-building CSV
        if per_bldg_rows:
            per_bldg_csv = output_dir / f"eval_roof_clf_{name}.csv"
            with open(per_bldg_csv, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["gt_shape", "pred_shape", "correct"])
                writer.writeheader()
                writer.writerows(per_bldg_rows)
            if verbose:
                print(f"  Per-building CSV: {per_bldg_csv}")

        # Save confusion matrix
        if confusion:
            conf_path = output_dir / f"eval_roof_clf_confusion_{name}.txt"
            classes_present = sorted(set(
                [t for t in true_labels if t] + [p for p in pred_labels if p]
            ))
            conf_text = _format_confusion(name, confusion, classes_present)
            conf_path.write_text(conf_text, encoding="utf-8")
            if verbose:
                print(f"  Confusion matrix: {conf_path}")

    except Exception as exc:
        summary["error"] = str(exc)
        if verbose:
            print(f"  ERROR: {exc}")
        import traceback
        traceback.print_exc()

    return summary


# ---------------------------------------------------------------------------
# Evaluation entry point
# ---------------------------------------------------------------------------

def eval_roof_classifier(
    output_dir: Path | None = None,
    start_server: bool = True,
    port: int = 9090,
    verbose: bool = True,
) -> dict[str, dict]:
    """Run ROOF-2 classifier evaluation across all evaluation cities.

    Returns
    -------
    dict mapping city name → summary dict
    """
    if output_dir is None:
        output_dir = _STRM2STL / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    from app.session.terrain_session import TerrainSession

    session = TerrainSession(port=port)
    if start_server:
        session.start()

    summaries: list[dict] = []
    results: dict[str, dict] = {}

    for name, cfg in EVAL_CITIES.items():
        if verbose:
            print(f"\n{'─' * 60}")
            print(f"  {name}")
            print(f"{'─' * 60}")

        summary = _evaluate_city(name, cfg, session, output_dir, verbose)
        results[name] = summary
        summaries.append(summary)

    # ── Combined summary CSV ─────────────────────────────────────────
    if summaries:
        summary_csv = output_dir / "eval_roof_clf_summary.csv"
        fields = list(summaries[0].keys())
        with open(summary_csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(summaries)

        if verbose:
            print(f"\n{'═' * 60}")
            print(f"  Roof classifier evaluation summary")
            print(f"{'═' * 60}")
            print(f"  {'city':<22}  {'bldg':>5}  {'gt':>5}  "
                  f"{'gain':>5}  {'acc':>6}  {'top1_shape':<12}  note")
            for s in summaries:
                acc_str = f"{s['accuracy']:.3f}" if s["accuracy"] is not None else "   n/a"
                top1 = s.get("top1_shape") or ""
                note = s.get("error") or ""
                print(
                    f"  {s['city']:<22}  "
                    f"{s['n_buildings']:>5}  "
                    f"{s['n_osm_tagged']:>5}  "
                    f"{s['coverage_gain']:>+5}  "
                    f"{acc_str:>6}  "
                    f"{top1:<12}  "
                    f"{note}"
                )
            print(f"\n  Summary CSV: {summary_csv}")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate ROOF-2 roof-shape classifier across 5 evaluation cities."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, metavar="DIR",
        help="Directory for CSV output (default: strm2stl/output/).",
    )
    parser.add_argument("--no-server", action="store_true")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    eval_roof_classifier(
        output_dir=args.output_dir,
        start_server=not args.no_server,
        port=args.port,
        verbose=not args.quiet,
    )
