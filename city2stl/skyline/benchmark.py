"""Skyline height benchmark (F-SKYBENCH): surveyed truth per footprint, and the scorer.

Truth for a building is its height above ground measured two ways and cross-checked:

- a survey nDSM (``city2stl.height.providers.survey``: USGS 3DEP, IGN, CNIG, ČÚZK, …), and
- Google Photorealistic 3D Tiles (``Google3DProvider``).

Each source gives the p95 of the nDSM inside the footprint shrunk by ``ERODE_M`` (so
façade edges and neighbouring roofs don't bleed in). The two agree when they differ by
at most ``max(AGREE_ABS_M, AGREE_REL × height)``; only agreeing ("confirmed") buildings
make the headline. Disagreements are kept and counted: they are new construction,
demolition, trees over low roofs, or a bad ground estimate, and say which source to doubt.

Why not OSM height tags: they are sparse (22 of 329 scored buildings on Cartagena) and are
mostly ``building:levels`` × 3.2 m plus one level for the roof (``city2stl/heights.py``
``METRES_PER_LEVEL``). Why not one source: either alone hides its own errors
(decision: ``docs/decisions/building-heights.md``, 2026-10-03). Where no 3D Tiles can be read
(the monthly cap: San Juan, Honolulu, Fort Lauderdale) the truth is the survey alone,
``survey_only``: scored by the band tables as single-source and reported apart.

Each survey record also stores p50 / p70 / p90 / p95 / max roof heights and a ground p5
(``footprint_stats``, review 2026-10-09 item 1), so a reading can be scored against the statistic
it measures (``METHOD_STAT``), and an age flag from OSM ``start_date`` (``temporal_flag``).

Truth is measured on the footprints a run actually scored (``heights.json`` →
``footprint_lonlat``), keyed by a geometry hash, and cached per region under
``runs/benchmark/truth/`` so re-scoring needs no network.
"""

from __future__ import annotations

import logging

from geo2stl.cache import (  # noqa: F401 - file_lock re-exported (bm.file_lock)
    atomic_write_json,
    file_lock,
)
from geo2stl.geo import M_PER_DEG_SPHERE as M_PER_DEG_LAT  # noqa: F401 (bm.M_PER_DEG_LAT)

from .benchmark_geometry import (  # noqa: F401
    _SNAP_EPS_CELLS,
    AGREE_ABS_M,
    AGREE_REL,
    BANDS,
    BENCHMARK_ROOT,
    DISTANCE_BANDS,
    ERODE_M,
    EUBUCCO_BANDS,
    FALLBACKS,
    GROUND_PERCENTILE,
    GROUND_RING_M,
    METHOD_STAT,
    MIN_CELLS,
    MIN_FINITE_FRACTION,
    REGIONS,
    RESOLUTION_M,
    REVIEW_BANDS,
    ROOF_PERCENTILE,
    ROOF_STATS,
    ROOF_STATS_VERSION,
    STAT_VERSION,
    TILE_M,
    TILE_PAD_M,
    TOL_ABS_M,
    TOL_REL,
    Tile,
    _buffer_m,
    _cells,
    _erode,
    _grid_dim,
    _inside,
    _own_bbox,
    _polygon,
    _snapped_bbox,
    benchmark_runs,
    footprint_key,
    footprint_stat,
    footprint_stats,
    ground_ring,
    region_key,
    tile_bbox,
    tile_cell,
    tiles_for,
)
from .benchmark_scoring import (  # noqa: F401
    CORROBORATED_TIERS,
    DISTANCE_LABELS,
    EUBUCCO_LABELS,
    NESTED_COVER,
    NO_TRUTH,
    READING_TIERS,
    REVIEW_LABELS,
    SINGLE_SOURCE,
    TWO_SOURCE,
    _band_label,
    _errors,
    _pairs,
    attach_roof_stats,
    band_errors,
    band_of,
    label_tiers,
    matched_truth,
    nested_keys,
    per_view_relative,
    relative_metrics,
    score_buildings,
    score_by_tier,
    score_survey_rows,
    scoring_truth,
    street_view_buildings,
    survey_blind,
    tol_m,
    truth_mode,
    withheld_buildings,
)
from .benchmark_sources import (  # noqa: F401
    _SOURCE_PROVIDER,
    SHIPPED_SOURCES,
    _sources_cache_path,
    load_sources_cache,
    score_sources,
    source_heights,
    source_ndsm,
)
from .benchmark_tables import (  # noqa: F401
    _GENERIC_WORDS,
    _dist_m,
    _name_words,
    _pred_tier,
    band_method_table,
    band_tier_table,
    bench_tables,
    match_known_tower,
    method_readings,
    pipeline_metrics,
    report_seed_positions,
    score_known,
    seed_positions,
)
from .benchmark_truth import (  # noqa: F401
    FLAT_MIN_TOWERS,
    FLAT_READ_SHARE,
    FLAT_SHARE,
    FLAT_TAG_MIN_M,
    TEMPORAL_MAY_POSTDATE,
    TEMPORAL_PREDATES,
    TEMPORAL_UNKNOWN,
    TRUTH_WORKERS,
    TilesUnavailable,
    _drop_tiles,
    _truth_cache_path,
    built_year,
    classify,
    flat_mesh,
    footprint_truth,
    load_report,
    load_truth_cache,
    osm_start_dates,
    refresh_survey_truth,
    report_key,
    save_truth_cache,
    survey_ndsm,
    survey_part,
    temporal_flag,
    tiles_ndsm,
    truth_workers,
    write_json_atomic,
)

logger = logging.getLogger(__name__)
