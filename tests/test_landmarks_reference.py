"""Reference landmark (F-DETAIL D4): the stored Alhambra raster swapped in for OSM heights.

``city2stl.landmarks.reference_landmarks`` with a tiny synthetic raster for the unit tests, and
one ``requires_cache`` check of the real Granada numbers against the study's method B
(``output/detail_study/exp4_merge``).
"""

import csv
import json

import numpy as np
import pytest
from rasterio.transform import Affine
from test_landmarks import (
    BBOX,
    BUILDINGS,
    CATHEDRAL,
    SHORT,
    TALL,
    TOWNHALL,
    _build,
    _fc,
    _flat_scale,
)

from city2stl import landmarks as lm

# One free-standing tower: only the towers layer has it (no osm_id), at 50 m in OSM.
T_LON, T_LAT = -3.5995, 37.1745
TOWER_RING = [[[T_LON - 0.00008, T_LAT - 0.00008], [T_LON + 0.00008, T_LAT - 0.00008],
               [T_LON + 0.00008, T_LAT + 0.00008], [T_LON - 0.00008, T_LAT + 0.00008],
               [T_LON - 0.00008, T_LAT - 0.00008]]]
TOWER_LAYER = _fc({"geometry": {"type": "Polygon", "coordinates": TOWER_RING},
                   "properties": {"height_m": 50.0}})

RULE = {"source": "test raster", "units": [
    {"key": "way/1", "osm_id": "way/1", "layer": "buildings", "labels": [{"label": 1}]},
    {"key": "ref:9", "osm_id": None, "layer": "towers", "labels": [{"label": 9}],
     "footprint": TOWER_RING, "centroid": [T_LON, T_LAT]},
    {"key": "ref:10", "osm_id": None, "layer": "towers", "labels": [{"label": 10}],
     "footprint": TOWER_RING, "centroid": [-3.0, 37.0]},     # far away: not in this model
]}


def _raster(height_m=12.0, gap=False):
    """A 1e-5 degree raster over the test bbox, one flat height."""
    res = 0.00001
    w, e, n, s = BBOX["west"] - 0.001, BBOX["east"] + 0.001, BBOX["north"] + 0.001, BBOX["south"] - 0.001
    t = Affine(res, 0, w, 0, -res, n)
    arr = np.full((round((n - s) / res), round((e - w) / res)), height_m, np.float32)
    if gap:
        arr[100, 100] = np.nan
    return arr, t


def _top_mm(model, scale_h):
    return 3.0 + scale_h * model.scale.z_mm_per_m


class TestReferenceLandmarks:
    def test_off_is_unchanged(self):
        ov, stats = lm.reference_landmarks(BBOX, enabled=False, raster=_raster(), rule=RULE)
        assert ov == {} and stats["reason"] == "off"
        base = _build(BUILDINGS, None, layers={"towers": TOWER_LAYER})
        off = _build(BUILDINGS, ov, layers={"towers": TOWER_LAYER})
        assert np.array_equal(base.merged.vertices, off.merged.vertices)
        assert np.array_equal(base.merged.faces, off.merged.faces)

    def test_outside_raster_is_unchanged(self):
        arr, t = _raster()
        far = Affine(t.a, 0, 10.0, 0, t.e, 50.0)
        ov, stats = lm.reference_landmarks(BBOX, enabled=True, raster=(arr, far), rule=RULE)
        assert ov == {} and stats["reason"] == "outside the raster"

    def test_swaps_exactly_the_rule_footprints(self):
        ov, stats = lm.reference_landmarks(BBOX, enabled=True, raster=_raster(), rule=RULE)
        assert set(ov) == {"way/1", "ref:9"}            # ref:10 lies outside the model
        assert stats["units"] == 2 and stats["labels"] == 2
        model = _build(BUILDINGS, ov, layers={"towers": TOWER_LAYER})
        rep = model.report["landmarks"]
        assert {k: v["status"] for k, v in rep.items()} == {"way/1": "applied", "ref:9": "applied"}
        assert rep["ref:9"]["kind"] == "reference"
        # The OSM 40 m cathedral and the 50 m tower copy are gone: everything swapped is 12 m.
        base = _build(BUILDINGS, None, layers={"towers": TOWER_LAYER})
        assert base.merged.bounds[1, 2] > _top_mm(base, 49.0)
        # The tallest remaining thing is TALL (25 m, not in the rule): untouched.
        assert model.merged.bounds[1, 2] == pytest.approx(_top_mm(model, 25.0), abs=0.1)
        # Footprints that were not in the rule keep their OSM buildings.
        fps = model.report["landmarks"]
        assert set(fps) == {"way/1", "ref:9"}
        for feat in (TALL, TOWNHALL, SHORT):
            assert feat["properties"]["osm_id"] not in fps
        assert model.report["layers"]["buildings"]["landmark_replaced"] >= 3

    def test_swapped_height_is_the_raster_and_watertight(self):
        ov, _ = lm.reference_landmarks(BBOX, enabled=True, raster=_raster(12.0), rule=RULE)
        model = _build(_fc(CATHEDRAL), ov, layers={"towers": TOWER_LAYER})
        assert model.report["merged"]["watertight"]
        assert model.merged.is_watertight
        assert model.merged.bounds[1, 2] == pytest.approx(_top_mm(model, 12.0), abs=0.1)
        assert model.report["landmarks"]["ref:9"]["status"] == "applied"

    def test_heightfield_is_depinched(self):
        ov, _ = lm.reference_landmarks(BBOX, enabled=True, raster=_raster(0.2, gap=True), rule=RULE)
        ndsm = ov["way/1"].ndsm
        assert np.isfinite(ndsm).all() and ndsm.min() >= lm.REFERENCE_MIN_M

    def test_local_ground_follows_the_terrain(self):
        # A hillside: the swapped top rises with the ground instead of sitting at its highest point.
        from city2stl.city_model import build_on_terrain
        ov, _ = lm.reference_landmarks(BBOX, enabled=True, raster=_raster(10.0), rule=RULE)
        scale = _flat_scale()
        z = scale.z_mm(np.tile(np.linspace(480.0, 520.0, 150), (150, 1)))
        model = build_on_terrain(z, BBOX, scale, {"buildings": _fc(CATHEDRAL)},
                                 layer_overrides={"buildings": {"max_slenderness": 0}},
                                 landmark_overrides={"way/1": ov["way/1"]})
        assert model.report["landmarks"]["way/1"]["status"] == "applied"
        v = model.parts["buildings"].vertices
        from city2stl.city_model import Terrain
        above = v[:, 2] - Terrain(z, BBOX, scale).sample(v[:, 0], v[:, 1])
        roof = above[above > 1.0]
        # Every roof vertex stands 10 m over the ground right under it, on a 40 m slope.
        assert roof.size and roof.max() == pytest.approx(10 * scale.z_mm_per_m, abs=0.15)
        assert roof.min() > 0.5 * 10 * scale.z_mm_per_m


@pytest.mark.requires_cache
def test_granada_matches_the_study_method_b():
    """The stored raster, scored per swapped label on lidar like ``before_after.csv``."""
    from pathlib import Path

    study = Path(__file__).resolve().parents[1] / "output" / "detail_study"
    if not (study / "shared" / "alhambra_grids.npz").exists():
        pytest.skip("detail study outputs not present")
    h, t, meta, rule = lm.load_reference()
    g = np.load(study / "shared" / "alhambra_grids.npz", allow_pickle=True)
    assert h.shape == g["lidar"].shape and meta["scale_chosen"] == "isotropic"
    from scipy.ndimage import uniform_filter
    ras = np.nan_to_num(np.flipud(h))        # stored row 0 north; the study grid row 0 south
    labels, lidar, ours = g["building_labels"], g["lidar"], g["ours"]
    f = np.nan_to_num(lidar)
    trees = (np.sqrt(np.maximum(uniform_filter(f * f, 3) - uniform_filter(f, 3) ** 2, 0)) > 1.5) & (f > 2.0)
    rows = {int(r["label"]): r for r in csv.DictReader(open(study / "exp4_merge" / "before_after.csv", encoding="utf-8"))}
    swapped = [lab["label"] for u in rule["units"] for lab in u["labels"]]
    assert len(swapped) == 22
    better, errs = 0, []
    for lab in swapped:
        m = (labels == lab) & np.isfinite(lidar) & ~trees
        if int(rows[lab]["eval_cells"]) < 10 or m.sum() < 10:
            better += 1                      # study evaluates these on a handful of cells only
            continue
        d = np.abs(ras[m] - lidar[m])
        errs.append(d)
        better += np.median(d) < np.median(np.abs(ours[m] - lidar[m]))
    med = float(np.median(np.concatenate(errs)))
    # Study method B: 22 of 22 on its merged model; on the raster alone Torre del Criado (275) is not.
    assert better >= 21, f"improved {better} of 22"
    assert med == pytest.approx(2.37, abs=0.5), med
    # The metadata records both scale scores and the kept one is the lower.
    sc = meta["scores_median_abs_m"]
    assert sc[meta["scale_chosen"]] == min(sc.values())
    json.dumps(meta)
    # And the stored raster resolves to the rule's 22 swaps over a Granada bbox.
    gb = dict(zip(("north", "south", "east", "west"), meta["bbox_nsew"], strict=True))
    ov, stats = lm.reference_landmarks(gb, enabled=True)
    assert stats["labels"] == 22 and len(ov) >= 17
