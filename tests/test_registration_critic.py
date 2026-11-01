"""city2stl.registration.critic: height-surface scoring, references and model rasters (offline)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from city2stl.registration import critic

BBOX = (40.001, 40.0, -75.0, -75.001)   # N, S, E, W; about 111 m x 85 m


def _scene(n=40):
    """Two 8x8 buildings (10 m and 20 m) on bare ground, row 0 = south."""
    h = np.zeros((n, n))
    h[4:12, 4:12] = 10.0
    h[20:28, 20:28] = 20.0
    return h


# ------------------------------------------------------------------------------ score_heights

def test_identical_surfaces_score_perfectly():
    h = _scene()
    r = critic.score_heights(h, h.copy(), 1.0)
    assert r["footprint"]["iou"] == 1.0
    assert r["footprint"]["precision"] == r["footprint"]["recall"] == 1.0
    assert r["cells"]["n"] == 128
    assert r["cells"]["mae_m"] == 0.0 and r["cells"]["bias_m"] == 0.0
    assert r["buildings"]["n"] == 2
    assert r["buildings"]["median_abs_error_m"] == 0.0
    assert r["buildings"]["within_2m_pct"] == 100.0
    assert r["valid_cells"] == 1600
    assert r["scale"] == {"fitted": False, "m_per_unit": 1.0}


def test_constant_height_offset_is_the_building_error():
    ref = _scene()
    model = np.where(ref > 0, ref + 3.0, 0.0)
    r = critic.score_heights(model, ref, 1.0)
    assert r["footprint"]["iou"] == 1.0
    assert r["buildings"]["median_abs_error_m"] == 3.0
    assert r["buildings"]["mean_error_m"] == 3.0
    assert r["buildings"]["within_2m_pct"] == 0.0 and r["buildings"]["within_5m_pct"] == 100.0
    assert r["cells"]["bias_m"] == 3.0 and r["cells"]["rmse_m"] == 3.0


def test_a_shifted_model_loses_iou():
    ref = _scene()
    model = np.roll(ref, 4, axis=1)                 # half a building width
    r = critic.score_heights(model, ref, 1.0)
    assert 0.2 < r["footprint"]["iou"] < 0.6
    assert r["footprint"]["precision"] == pytest.approx(0.5, abs=0.01)


def test_fit_scale_recovers_the_unit_ratio_and_absorbs_the_bias():
    ref = _scene()
    model = ref / 4.0                               # model in odd units: metres = 4 x unit
    r = critic.score_heights(model, ref, 1.0, fit_scale=True)
    assert r["scale"]["fitted"] is True
    assert r["scale"]["m_per_unit"] == pytest.approx(4.0)
    assert r["buildings"]["median_abs_error_m"] == 0.0
    assert r["footprint"]["iou"] == 1.0
    # Without fitting the same model is wrong everywhere: 7.5 m and 15 m too low.
    raw = critic.score_heights(model, ref, 1.0)
    assert raw["buildings"]["median_abs_error_m"] == pytest.approx(11.25)
    assert raw["buildings"]["mean_error_m"] == pytest.approx(-11.25)


def test_labels_define_buildings_and_invalid_cells_are_ignored():
    ref = _scene()
    labels = np.zeros(ref.shape, int)
    labels[4:12, 4:12] = 1
    labels[20:28, 20:28] = 2
    model = ref.copy()
    model[20:28, 20:28] = 17.0                      # second building 3 m low
    r = critic.score_heights(model, ref, 1.0, labels=labels)
    assert r["buildings"]["n"] == 2
    assert r["buildings"]["median_abs_error_m"] == 1.5      # (0 + 3) / 2
    assert r["buildings"]["p90_abs_error_m"] == pytest.approx(2.7)
    valid = np.ones(ref.shape, bool)
    valid[20:28, :] = False                         # the bad building is not scored
    ok = critic.score_heights(model, ref, 1.0, labels=labels, ref_valid=valid)
    assert ok["buildings"]["n"] == 1 and ok["buildings"]["median_abs_error_m"] == 0.0
    assert ok["valid_cells"] == 1600 - 8 * 40


def test_nan_cells_are_excluded_and_empty_scene_has_no_building_stats():
    ref = _scene()
    model = ref.copy()
    model[4:12, 4:12] = np.nan
    r = critic.score_heights(model, ref, 1.0)
    assert r["buildings"]["n"] == 1
    flat = np.zeros((10, 10))
    e = critic.score_heights(flat, flat, 1.0)
    assert e["footprint"]["iou"] is None and e["buildings"] == {"n": 0}
    assert e["cells"] == {"n": 0}


def test_shape_mismatch_raises():
    with pytest.raises(ValueError, match="not on one grid"):
        critic.score_heights(np.zeros((4, 4)), np.zeros((5, 5)), 1.0)


def test_roofs_resolvable_depends_on_cell_size_and_roof_shape_is_measured():
    ref = _scene()
    ref[4:12, 4:8] = 14.0                           # a stepped roof
    flat = np.where(ref > 0, np.where(ref == 14.0, 12.0, ref), 0.0)
    fine = critic.score_heights(flat, ref, 1.0)
    assert fine["roofs"]["resolvable"] is True and fine["roofs"]["note"] is None
    assert fine["roofs"]["n"] >= 1
    assert fine["roofs"]["median_relief_ref_m"] > fine["roofs"]["median_relief_model_m"] - 1e-9
    coarse = critic.score_heights(flat, ref, 5.0)
    assert coarse["roofs"]["resolvable"] is False
    assert "5.0 m cells" in coarse["roofs"]["note"]
    assert critic.score_heights(flat, ref, critic.ROOF_RESOLVABLE_CELL_M)["roofs"]["resolvable"]


def test_small_footprints_are_not_buildings():
    ref = np.zeros((20, 20))
    ref[5:7, 5:7] = 10.0                            # 4 cells: counted
    ref[15, 15] = 10.0                              # 1 cell: dropped
    r = critic.score_heights(ref, ref, 1.0)
    assert r["buildings"]["n"] == 1


# ------------------------------------------------------------------------------ Reference

def test_reference_defaults_valid_and_describes_itself():
    h = np.array([[0.0, 5.0], [np.nan, 3.0]])
    ref = critic.Reference(h, BBOX, 2.5, "pack:x", meta={"kind": "plate"})
    assert ref.shape == (2, 2)
    assert ref.valid.dtype == bool and ref.valid.tolist() == [[True, True], [False, True]]
    d = ref.describe()
    assert d["source"] == "pack:x" and d["shape"] == [2, 2] and d["cell_m"] == 2.5
    assert d["bbox"] == {"north": 40.001, "south": 40.0, "east": -75.0, "west": -75.001}
    assert d["valid_pct"] == 75.0 and d["kind"] == "plate"
    json.dumps(d)


# ------------------------------------------------------------------------------ resample_to_grid

def test_resample_identity_and_row_flip_and_fill():
    src = np.arange(16, dtype=float).reshape(4, 4)
    same = critic.resample_to_grid(src, BBOX, BBOX, (4, 4))
    np.testing.assert_allclose(same, src, atol=1e-6)
    flipped = critic.resample_to_grid(src, BBOX, BBOX, (4, 4), src_row0="north")
    np.testing.assert_allclose(flipped, src[::-1], atol=1e-6)
    # a destination window twice as wide as the source: the outside is `fill`
    wide = (BBOX[0], BBOX[1], BBOX[2] + 0.001, BBOX[3])
    out = critic.resample_to_grid(src, BBOX, wide, (4, 8), fill=-1.0)
    assert out.shape == (4, 8)
    assert (out[:, 4:] == -1.0).all() and (out[:, :4] >= 0).all()
    nan_out = critic.resample_to_grid(src, BBOX, wide, (4, 8))
    assert np.isnan(nan_out[:, 5:]).all()


def test_resample_nearest_keeps_values_and_nan_source_cells_stay_nan():
    src = np.array([[1.0, 2.0], [3.0, np.nan]])
    out = critic.resample_to_grid(src, BBOX, BBOX, (4, 4), nearest=True)
    assert set(np.unique(out[np.isfinite(out)])) == {1.0, 2.0, 3.0}
    assert np.isnan(out[2:, 2:]).all()               # the NaN source cell is the north-east quadrant
    assert np.isfinite(out[:2, :]).all() and np.isfinite(out[:, :2]).all()


# ------------------------------------------------------------------------------ pack_reference

def _pack(root, slug, meta, **arrays):
    d = root / slug
    d.mkdir(parents=True)
    for name, arr in arrays.items():
        np.save(d / f"{name}.npy", arr)
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def test_pack_reference_unknown_slug_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="No align pack"):
        critic.pack_reference("ghost", data_dir=tmp_path)


def test_pack_reference_without_arrays_raises(tmp_path):
    _pack(tmp_path, "empty", {"osm_bbox_nsew": list(BBOX)})
    with pytest.raises(FileNotFoundError, match="no stl_heightmap"):
        critic.pack_reference("empty", data_dir=tmp_path)


def test_pack_reference_survey_is_taken_as_metres(tmp_path):
    plate = _scene(32)
    plate[0, 0] = np.nan
    _pack(tmp_path, "survey", {"osm_bbox_nsew": list(BBOX), "note": "lidar"},
          stl_heightmap=plate)
    ref = critic.pack_reference("survey", data_dir=tmp_path)
    assert ref.meta["kind"] == "survey" and ref.meta["m_per_unit"] == 1.0
    assert ref.source == "pack:survey" and ref.shape == (32, 32)
    assert ref.heights_m[10, 10] == 0.0 or ref.heights_m[6, 6] == 10.0
    assert ref.heights_m[6, 6] == 10.0
    assert ref.heights_m.max() == 20.0
    assert ref.cell_m == pytest.approx(3.0, rel=0.3)        # ~100 m / 32 cells


def test_pack_reference_plate_is_scaled_to_the_tallest_roof_and_warped(tmp_path):
    res = 64
    plate = np.zeros((res, res), np.float32)
    plate[10:20, 10:20] = 2.0                       # plate units; tallest roof is 30 m
    meta = {"osm_bbox_nsew": list(BBOX), "tallest_m": 30.0, "resolution": res,
            "cell_size_m": 2.0, "pipeline_guess": {"matrix": [[1, 0, 5], [0, 1, 3]],
                                                   "source": "geometric"}}
    _pack(tmp_path, "plate", meta, stl_heightmap=plate)
    ref = critic.pack_reference("plate", data_dir=tmp_path)
    assert ref.meta["kind"] == "plate" and ref.meta["placement"] == "geometric"
    assert ref.meta["m_per_unit"] == pytest.approx(15.0)
    assert ref.cell_m == 2.0 and ref.shape == (res, res)
    # translated by (+5 x, +3 y) pixels
    assert ref.heights_m[15 + 3, 15 + 5] == pytest.approx(30.0)
    assert ref.heights_m[15, 15 + 20] == 0.0 or np.isfinite(ref.heights_m[15, 15 + 20])
    # an explicit matrix overrides the pack's guess
    moved = critic.pack_reference("plate", data_dir=tmp_path, matrix=[[1, 0, 0], [0, 1, 0]])
    assert moved.meta["placement"] == "explicit"
    assert moved.heights_m[15, 15] == pytest.approx(30.0)
    assert moved.valid.all()                        # no relief array: footprint is the full plate


def test_pack_reference_respects_the_relief_footprint(tmp_path):
    res = 32
    plate = np.zeros((res, res), np.float32)
    plate[4:8, 4:8] = 1.0
    relief = np.full((res, res), np.nan, np.float32)
    relief[:, :16] = 0.0                            # the plate only covers the west half
    meta = {"osm_bbox_nsew": list(BBOX), "tallest_m": 10.0, "resolution": res,
            "pipeline_guess": {"matrix": [[1, 0, 0], [0, 1, 0]]}}
    _pack(tmp_path, "half", meta, stl_heightmap=plate, stl_relief=relief,
          stl_residual=plate)
    ref = critic.pack_reference("half", data_dir=tmp_path)
    assert ref.valid[:, :15].all() and not ref.valid[:, 17:].any()
    assert np.isnan(ref.heights_m[:, 20]).all()
    assert ref.heights_m[5, 5] == pytest.approx(10.0)


def test_pack_reference_default_data_dir_comes_from_env(tmp_path, monkeypatch):
    _pack(tmp_path, "viaenv", {"osm_bbox_nsew": list(BBOX)}, stl_heightmap=_scene(16))
    monkeypatch.setenv("ALIGN_DATA_DIR", str(tmp_path))
    assert critic.pack_reference("viaenv").shape == (16, 16)


# ------------------------------------------------------------------------------ ndsm_reference

def test_ndsm_reference_wraps_the_provider_and_none_is_a_lookup_error(monkeypatch):
    import city2stl.height.providers.lidar_3dep_ept as ept
    seen = {}

    def fake(bbox, resolution=512):
        seen["args"] = (bbox, resolution)
        return _scene(24)

    monkeypatch.setattr(ept, "get_ndsm", fake)
    ref = critic.ndsm_reference(BBOX, resolution=24)
    assert seen["args"] == (BBOX, 24)
    assert ref.source == "ndsm:3dep" and ref.meta["kind"] == "lidar" and ref.shape == (24, 24)
    monkeypatch.setattr(ept, "get_ndsm", lambda *a, **k: None)
    with pytest.raises(LookupError, match="No 3DEP lidar nDSM"):
        critic.ndsm_reference(BBOX)


# ------------------------------------------------------------------------------ models

def _feature(ring, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _ref_grid(n=50):
    return critic.Reference(np.zeros((n, n)), BBOX, 2.0, "pack:t")


def _ring(x0, y0, x1, y1):
    """A lon/lat ring from fractions of the BBOX (fx west->east, fy south->north)."""
    n, s, e, w = BBOX
    lon = lambda f: w + f * (e - w)       # noqa: E731
    lat = lambda f: s + f * (n - s)       # noqa: E731
    return [[lon(x0), lat(y0)], [lon(x1), lat(y0)], [lon(x1), lat(y1)], [lon(x0), lat(y1)],
            [lon(x0), lat(y0)]]


def test_buildings_raster_fills_polygons_with_ids_and_heights_row_zero_south():
    ref = _ref_grid()
    feats = [_feature(_ring(0.1, 0.1, 0.3, 0.3), height_m=12),
             _feature(_ring(0.6, 0.6, 0.9, 0.9), height="25"),
             _feature(_ring(0.4, 0.4, 0.5, 0.5)),                  # default 10 m
             {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}},
             {"geometry": None}, None]
    h, lab = critic.buildings_raster(feats, ref)
    assert h.shape == lab.shape == ref.shape and lab.dtype == np.int32
    assert h[10, 10] == 12.0 and lab[10, 10] == 1          # south-west is low rows
    assert h[40, 40] == 25.0 and lab[40, 40] == 2
    assert h[22, 22] == 10.0 and lab[22, 22] == 3
    assert h[5, 40] == 0.0 and lab[5, 40] == 0
    assert lab.max() == 3
    assert critic._feature_height({"height_m": "tall"}) == 10.0


def test_buildings_raster_no_polygons_is_empty():
    h, lab = critic.buildings_raster([], _ref_grid(10))
    assert not h.any() and not lab.any() and lab.dtype == np.int32


def test_score_model_buildings_against_a_matching_reference():
    ref = _ref_grid()
    feats = [_feature(_ring(0.1, 0.1, 0.4, 0.4), height_m=15),
             _feature(_ring(0.6, 0.6, 0.9, 0.9), height_m=30)]
    truth, _ = critic.buildings_raster(feats, ref)
    ref = critic.Reference(truth, BBOX, 2.0, "pack:t")
    out = critic.score_model({"kind": "buildings", "features": feats}, ref)
    assert out["model"] == {"kind": "buildings", "features": 2}
    assert out["footprint"]["iou"] == 1.0
    assert out["buildings"]["n"] == 2 and out["buildings"]["median_abs_error_m"] == 0.0
    assert out["reference"]["source"] == "pack:t"
    # one building too tall by 4 m, one right: median of (4, 0) = 2
    wrong = [dict(feats[0], properties={"height_m": 19}), feats[1]]
    off = critic.score_model({"kind": "buildings", "features": wrong}, ref)
    assert off["buildings"]["median_abs_error_m"] == 2.0
    assert off["buildings"]["mean_error_m"] == 2.0
    json.dumps(off)


def test_score_model_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown model kind"):
        critic.score_model({"kind": "mesh"}, _ref_grid(8))
    with pytest.raises(ValueError, match="Unknown model kind"):
        critic.score_model({}, _ref_grid(8))
