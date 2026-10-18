"""The on-disk roof-shape cache (``city2stl.roof_cache``) and the batched model path of
``roof_classifier.classify_roof_shapes`` (same answers as one predict per building,
progress reported)."""

from __future__ import annotations

import copy
import sqlite3

import numpy as np
import pytest

import geo2stl.cache as geo_cache
from city2stl import roof_cache, roof_classifier, roof_model, roof_tiles

BOX = (37.1840, 37.1800, -3.5980, -3.6040)


def _bld(i, **props):
    w, s = -3.6030 + i * 1e-4, 37.1810
    ring = [[w, s], [w + 5e-5, s], [w + 5e-5, s + 4e-5], [w, s + 4e-5], [w, s]]
    return {"type": "Feature", "properties": {"building": "yes", **props},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


@pytest.fixture(autouse=True)
def tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(geo_cache, "CACHE_ROOT", str(tmp_path))
    return tmp_path


def test_store_then_lookup_round_trips_and_misses_are_absent():
    roof_cache.store([("a", "gabled", 0.8, "roof_model_gbm"), ("b", "flat", 0.6, "roof_model_gbm")])
    assert roof_cache.lookup(["a", "b", "c"]) == {"a": ("gabled", 0.8, "roof_model_gbm"),
                                                  "b": ("flat", 0.6, "roof_model_gbm")}
    roof_cache.store([("a", "flat", 0.7, "roof_model_gbm")])          # replaces
    assert roof_cache.lookup(["a"])["a"][0] == "flat"


def test_lookup_chunks_beyond_the_sqlite_parameter_limit():
    rows = [(f"k{i}", "flat", 0.5, "roof_model_gbm") for i in range(2000)]
    roof_cache.store(rows)
    assert len(roof_cache.lookup([r[0] for r in rows])) == 2000


def test_no_file_and_a_broken_file_are_misses_not_errors(tmp_cache):
    assert roof_cache.lookup(["a"]) == {}
    roof_cache.db_path().write_bytes(b"not a database")
    assert roof_cache.lookup(["a"]) == {}
    roof_cache.store([("a", "flat", 0.5, "x")])                     # logged, not raised


def test_key_follows_geometry_tags_and_model_not_other_properties():
    f = _bld(0, height=9)
    k = roof_cache.feature_key(f, "m1")
    assert roof_cache.feature_key(copy.deepcopy(f), "m1") == k
    assert roof_cache.feature_key({**f, "properties": {**f["properties"], "name": "x"}}, "m1") == k
    assert roof_cache.feature_key(f, "m2") != k
    assert roof_cache.feature_key({**f, "properties": {**f["properties"], "height": 12}}, "m1") != k
    g = copy.deepcopy(f)
    g["geometry"]["coordinates"][0][1][0] += 1e-6
    assert roof_cache.feature_key(g, "m1") != k


def test_model_id_hashes_content_and_changes_with_it(tmp_path):
    p = tmp_path / "m.joblib"
    p.write_bytes(b"one")
    a = roof_cache.model_id(str(p))
    p.write_bytes(b"two!")
    assert roof_cache.model_id(str(p)) != a and len(a) == 16


class _FakeModel:
    """Pitched probability from the footprint's west edge; ring 3 declines."""
    zoom = 18
    threshold = 0.5

    def __init__(self):
        self.batches = []

    def row(self, ring, tags):
        x = ring[0][0]
        return None if abs(x - (-3.6027)) < 1e-9 else [(x + 3.6030) * 1e3]

    def predict_rows(self, rows):
        self.batches.append(len(rows))
        return np.clip(np.asarray(rows, dtype=float)[:, 0], 0, 1)

    shape_for = roof_model.RoofShapeModel.shape_for


def _run(monkeypatch, model, batch, feats, progress=None):
    monkeypatch.setattr(roof_model, "load", lambda *a, **k: model)
    monkeypatch.setattr(roof_tiles, "prefetch_bbox", lambda *a, **k: None)
    monkeypatch.setattr(roof_classifier, "_PREDICT_BATCH", batch)
    monkeypatch.setattr(roof_classifier, "_PROGRESS_EVERY", 4)
    return roof_classifier.classify_roof_shapes(
        {"type": "FeatureCollection", "features": feats}, None, BOX, use_model=True,
        progress=progress)


def test_batched_predictions_match_one_at_a_time_and_report_progress(monkeypatch):
    feats = [_bld(i) for i in range(10)] + [_bld(10, **{"roof:shape": "dome"})]
    one, many = _FakeModel(), _FakeModel()
    a = _run(monkeypatch, one, 1, feats)
    calls = []
    b = _run(monkeypatch, many, 4, feats, progress=lambda d, t: calls.append((d, t)))
    assert a == b
    assert max(one.batches) == 1 and max(many.batches) == 4 and sum(many.batches) == 9
    assert b["_stats"] == {"total": 11, "classified": 9, "by_model": 9, "skipped": 1,
                          "unchanged": 1, "n_images": 0}
    assert "roof:shape" not in b["features"][3]["properties"]          # declined, skipped
    assert {f["properties"]["roof:shape"] for f in b["features"][:10] if f is not b["features"][3]} \
        == {"flat", "gabled"}
    assert calls == [(0, 11), (4, 11), (8, 11), (11, 11)]
    assert all(f["properties"].get("roof_source") == "roof_model_gbm"
               for k, f in enumerate(b["features"]) if k not in (3, 10))


def test_cache_file_is_a_plain_sqlite_table(tmp_cache):
    roof_cache.store([("a", "flat", 0.5, "roof_model_gbm")])
    with sqlite3.connect(roof_cache.db_path()) as con:
        assert con.execute("SELECT shape FROM roofs WHERE key='a'").fetchone() == ("flat",)
