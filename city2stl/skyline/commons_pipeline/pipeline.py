"""F-WEB2: a region's building heights from its Commons skyline photos, end to end.

    python -m city2stl.skyline.scripts.17_photo_pipeline --region miami --report <report dir> [--workers 8]

Needs the outline cache (``13_photo_profiles``), and for links the embeddings and pairs
(``16_embed_photos``, ``14_group_photos``). No downloads, no GPU: everything works on cached
outlines. Each photo gets a camera by the best route it qualifies for, and is kept only when
the route's gate passes:

1. labels (``sites/annotations/<region>_*.json``): ``photo_localize.solve_from_labels``;
   the unlabelled copy in ``same_pose_as`` is measured;
2. recorded location (``commons_photos.pipeline_status`` FIT): ``skyline_match.refine`` within
   500 m from ``commons_photos.fit_prior`` (EXIF FOV +-8 %, retried at +-40 % when the gate
   fails; a free FOV without EXIF; a cylindrical projection for panoramas wider than 3:1);
   gate misfit <= 0.3 and skyline coverage >= 25 %;
3. unlocated, linked to a located photo of the same spot (``photo_groups.embed_links``):
   refine from that photo's kept camera; same gate;
4. unlocated with EXIF FOV, not linked: full ``skyline_match.locate``; gate: the best pose
   beats the runner-up by >= 0.1 misfit (EXIF-FOV validation, 2026-10-04: right 0.17-0.19,
   wrong <= 0.035).
   Unlocated photos (and wrong geotags) not placed by 3 or 4 go to the manual-label list in
   ``placement_queue.json`` (labels place a photo to +-5 m). Screen and routes: user review of
   38 rejected photos, 2026-10-07 (``docs/decisions/building-heights.md``).

Towers in each kept photo: ``photo_heights.measure_towers`` + ``loo_heights``, towers beyond
``--max-dist-m`` dropped (a tower 7.7 km away read +208 m). Each reading has a median-row height
(``photo_m``) and a peak-row height (``peak_m``, T17 stepped tops). Per building: the photos'
readings are combined with ``photo_heights.combine_photo_heights``, a median weighted by each
photo's anchor deviation (T30, ``--photo-weighting anchor``, the default; ``median`` is the plain
median), on the peak readings where ``photo_heights.choose_top`` finds the photos agree better on
the peak (``--top``: ``auto`` default, ``median`` never uses the peak). A row keeps the old
plain-median result as ``photo_median_m`` and says which top it used (``top``).
Every candidate photo is measured, kept or not, and saved to ``photo_results.json`` with its
towers' truth, so the gates (incl. ``--max-anchor-dev``: towers must agree with their own OSM
heights) can be tuned with ``--rescore`` without placing photos again. Refinement tries camera
heights ``--h-cams`` (street, deck, rooftop). Scored against the benchmark truth and against
Street View on the same buildings (the report's ``heights.json``). Writes ``photos.json``,
``photo_heights.json`` and the report's ``benchmark.html``.

``--untagged`` (F-WEB2 C2): also heights for untagged buildings. Each candidate photo fits its
tilt and camera height on its tagged towers and implies a height for every untagged footprint
in view within ``--untagged-max-dist-m`` (``photo_heights.implied_heights``), dropping heights
above min(the site's ``max_plausible_height_m``, 1.2x the tallest tagged tower in view) and
columns a nearer tagged tower explains; where a tagged tower behind the footprint explains
its columns the height is only an upper bound (``untagged_capped``, not counted). A building is
kept when kept photos from 2+ viewpoints (cameras within 100 m are one) with 15 deg of view
directions at the footprint agree within max(3 m, min(10 %, 10 m)), with footprints behind an
agreed building dropped from the photos where it covers them
(``photo_heights.agreed_with_occlusion``); ``summary.untagged.rules`` counts what each rule
removed; ``--untagged-far-first`` (experiment) also lets a farther agreed building cap the
nearer footprints over its columns. Written to
``photo_heights.json`` (``untagged``) and scored against the benchmark truth cache
(``--untagged-truth fetch`` measures missing footprints: paid 3D Tiles reads).
``--untagged-candidates truth`` limits candidates to footprints the truth cache holds, so a
run can be scored without fetching (the default ``all`` keeps competing owners in play).
"""

from __future__ import annotations

import json
import os
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

from city2stl.skyline import photo_heights as ph

from . import placement
from .cache import outlined
from .finish import finish


def one_thread_per_worker() -> None:
    """The pool is the parallelism: set before the workers start so each uses one BLAS thread."""
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(v, "1")


@dataclass
class PipelineArgs:
    """Every knob of the pipeline; the CLI's defaults and help live here, so the library and
    the script cannot drift apart. ``help`` is the ``--flag`` text."""

    region: str
    report: Path
    workers: int = 8
    max_misfit: float = 0.3
    min_margin: float = 0.1
    min_coverage: float = 0.25
    max_dist_m: float = 5000.0
    h_cams: list[float] = field(
        default_factory=lambda: [2.0, 30.0, 60.0],
        metadata={"help": "camera heights tried when refining (street, station/deck, rooftop)"})
    max_anchor_dev: float | None = field(default=None, metadata={"help": (
        "keep a photo only when its towers sit within this many m (median) of their OSM "
        "heights; off by default until calibrated")})
    min_towers: int = 4
    rescue_anchor_dev: float | None = field(default=20.0, metadata={"help": (
        "keep a photo failing its fit/margin gate when its towers sit within this many m of "
        "their OSM heights (median); see placement._keep")})
    rescue_min_towers: int = 12
    rescue_max_p75: float | None = field(default=40.0, metadata={"help": (
        "a rescued photo's 75th-percentile |photo - OSM| (m) must stay under this; see "
        "placement._keep")})
    photo_weighting: str = field(default="anchor", metadata={"choices": ("anchor", "median"), "help": (
        "combine a building's photo readings by a median weighted 1/max(photo anchor "
        "deviation, 5 m) (T30, default), or the plain median")})
    top: str = field(default="auto", metadata={"choices": ("auto", "median"), "help": (
        "auto: a tower's peak row where photos agree better on it (T17 stepped tops); "
        "median: always the median row")})
    untagged: bool = field(default=False, metadata={"help": (
        "also measure untagged buildings that 2+ kept photos agree on (C2)")})
    untagged_max_dist_m: float = field(default=ph.UNTAGGED_MAX_DIST_M, metadata={"help": (
        "C2 candidates farther than this from the camera are skipped")})
    untagged_truth: str = field(default="cached", metadata={"choices": ("cached", "fetch"), "help": (
        "score C2 on the benchmark truth cache, or fetch missing footprints (paid 3D Tiles reads)")})
    untagged_far_first: bool = field(default=False, metadata={"help": (
        "C2 experiment: a farther agreed building caps the nearer footprints over its columns "
        "(counted in summary.untagged.rules)")})
    untagged_candidates: str = field(default="all", metadata={"choices": ("all", "truth"), "help": (
        "truth: only footprints the truth cache holds (scoring without fetches)")})
    no_truth_fetch: bool = field(default=False, metadata={"help": (
        "score on the cached truth only (no survey or paid 3D Tiles reads)")})
    reuse: Path | None = field(default=None, metadata={"help": (
        "an earlier photo_results.json: photos whose route is unchanged (and not a failed "
        "recorded fit) are copied, not placed again")})
    rescore: bool = field(default=False, metadata={"help": (
        "no placing: apply the gates to the saved photo_results.json")})


def add_arguments(ap) -> None:
    """The ``PipelineArgs`` fields as ``--flags`` (``min_margin`` -> ``--min-margin``)."""
    for f in fields(PipelineArgs):
        flag = "--" + f.name.replace("_", "-")
        kw: dict = {"help": f.metadata.get("help")}
        if f.name in ("region", "report"):
            kw.update(required=True, **({"type": Path} if f.name == "report" else {}))
        elif f.type == "bool":
            kw["action"] = "store_true"
        else:
            default = f.default_factory() if f.default is MISSING else f.default
            kw["default"] = default
            if "choices" in f.metadata:
                kw["choices"] = f.metadata["choices"]
            elif f.name == "h_cams":
                kw.update(type=float, nargs="+")
            elif f.name == "reuse":
                kw["type"] = Path
            elif f.type.startswith("int"):
                kw["type"] = int
            else:
                kw["type"] = float
        ap.add_argument(flag, **kw)


def run_pipeline(args: PipelineArgs) -> dict:
    """Place every photo (or, with ``rescore``, reload ``photo_results.json``), then keep,
    combine, score and write the report. Returns the summary."""
    one_thread_per_worker()
    if not args.rescore:                        # placing photos is a heavy job (CLAUDE.md)
        from city2stl.resources import wait_for_ram

        wait_for_ram()
    meta = {m["key"]: m for m in outlined(args.region)}
    from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox

    osm, _ = _load_osm_for_region(_load_region_bbox(args.region))
    saved = args.report / "photo_results.json"
    if args.rescore:
        results = json.loads(saved.read_text(encoding="utf-8"))
    else:
        if args.reuse:
            placement._REUSE.update({r["key"]: r for r in json.loads(args.reuse.read_text(encoding="utf-8"))})
        results = placement.place(args, meta, osm)
        saved.write_text(json.dumps(results, indent=0), encoding="utf-8")
    summary = finish(args, results, meta, osm)
    saved.write_text(json.dumps(results, indent=0), encoding="utf-8")
    return summary
