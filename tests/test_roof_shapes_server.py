"""D2 (F-DETAIL): the server classifies untagged roofs on read (``city_data.with_roof_shapes``),
``roof_shapes: "off"`` leaves the model byte-identical, and a client ``layer_data`` merge keeps
the classified roofs."""

from __future__ import annotations

import copy

import numpy as np
import pytest

import geo2stl.cache as geo_cache
from app.server.core import city_data
from city2stl import roof_cache, roof_classifier, roof_model
from city2stl.city_model import build_on_terrain, choose_scale

BBOX = dict(north=37.1840, south=37.1800, east=-3.5980, west=-3.6040)
BOX = (BBOX["north"], BBOX["south"], BBOX["east"], BBOX["west"])


def _bld(w, s, e, n, **props):
    ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
    return {"type": "Feature", "properties": {"height_m": 12.0, "height_source": "osm_tag", **props},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _payload():
    return {"buildings": {"type": "FeatureCollection", "features": [
        _bld(-3.6030, 37.1810, -3.6026, 37.1813, name="untagged"),
        _bld(-3.6020, 37.1810, -3.6016, 37.1813, name="osm", **{"roof:shape": "hipped"}),
        _bld(-3.6010, 37.1810, -3.6006, 37.1813, name="flat", **{"roof:shape": "flat"}),
    ]}, "roads": {"type": "FeatureCollection", "features": []}}


@pytest.fixture(autouse=True)
def tmp_roof_cache(monkeypatch, tmp_path):
    """Every test gets an empty on-disk roof cache and a fixed checkpoint id."""
    monkeypatch.setattr(geo_cache, "CACHE_ROOT", str(tmp_path))
    monkeypatch.setattr(roof_cache, "model_id", lambda path=None: "test-model")
    return tmp_path


@pytest.fixture
def fake_classifier(monkeypatch):
    """A checkpoint that is present and a classifier that calls every untagged roof gabled."""
    calls = []
    city_data._roof_memo.clear()

    def fake(fc, rgb, bbox, **kw):
        calls.append((rgb, kw))
        feats, n = [], 0
        for f in fc["features"]:
            f = copy.deepcopy(f)
            p = f["properties"]
            if not p.get("roof:shape"):
                p.update({"roof:shape": "gabled", "roof_source": "roof_model_gbm", "roof_confidence": 0.9})
                n += 1
            feats.append(f)
        return {**fc, "features": feats, "_stats": {"total": len(feats), "classified": n,
                                                    "skipped": 0, "unchanged": len(feats) - n}}

    monkeypatch.setattr(roof_model, "load", lambda *a, **k: object())
    monkeypatch.setattr(roof_classifier, "classify_roof_shapes", fake)
    return calls


@pytest.mark.parametrize("value,expect", [
    (None, "auto"), ("auto", "auto"), ("off", "off"), ("OFF", "off"), (True, "auto"), (False, "off"),
    ("false", "off"), ("", "auto"), ("garbage", "auto")])
def test_roof_shapes_mode(value, expect):
    assert city_data.roof_shapes_mode(value) == expect


def test_auto_classifies_untagged_never_overwrites_osm_and_reports_stats(fake_classifier):
    payload = _payload()
    before = copy.deepcopy(payload)
    out = city_data.with_roof_shapes(payload, *BOX, "auto")
    by = {f["properties"]["name"]: f["properties"] for f in out["buildings"]["features"]}
    assert by["untagged"]["roof:shape"] == "gabled" and by["untagged"]["roof_source"] == "roof_model_gbm"
    assert by["osm"]["roof:shape"] == "hipped" and "roof_source" not in by["osm"]
    assert by["flat"]["roof:shape"] == "flat"
    assert out["roof_shapes"] == {"mode": "auto", "total": 3, "classified": 1, "cached": 0,
                                  "unchanged": 2, "skipped": 0, "by_shape": {"gabled": 1}}
    assert "_stats" not in out["buildings"]
    assert payload == before                                   # input untouched
    assert fake_classifier[0][0] is None                       # the model reads its own tiles
    # a second read of the same footprints is served from memory
    city_data.with_roof_shapes(payload, *BOX, "auto")
    assert len(fake_classifier) == 1


def test_a_restart_reads_the_disk_cache_and_classifies_only_new_footprints(fake_classifier):
    city_data.with_roof_shapes(_payload(), *BOX, "auto")
    assert len(fake_classifier) == 1
    city_data._roof_memo.clear()                               # a server restart
    out = city_data.with_roof_shapes(_payload(), *BOX, "auto")
    assert len(fake_classifier) == 1                           # nothing left to classify
    assert out["roof_shapes"]["cached"] == 1 and out["roof_shapes"]["classified"] == 1
    p = out["buildings"]["features"][0]["properties"]
    assert (p["roof:shape"], p["roof_source"], p["roof_confidence"]) == ("gabled", "roof_model_gbm", 0.9)

    city_data._roof_memo.clear()
    payload = _payload()
    payload["buildings"]["features"].append(_bld(-3.6000, 37.1810, -3.5996, 37.1813, name="new"))
    out = city_data.with_roof_shapes(payload, *BOX, "auto")
    sent = fake_classifier[-1]
    assert len(fake_classifier) == 2 and out["roof_shapes"]["cached"] == 1
    assert out["roof_shapes"]["classified"] == 2
    assert [f["properties"]["roof:shape"] for f in out["buildings"]["features"]] == [
        "gabled", "hipped", "flat", "gabled"]
    assert sent[1]["use_model"] is True


def test_progress_reaches_the_classifier(fake_classifier):
    calls = []
    city_data.with_roof_shapes(_payload(), *BOX, "auto", progress=lambda d, t: calls.append((d, t)))
    assert fake_classifier[0][1]["progress"] is not None


def test_get_city_layers_passes_roof_progress(monkeypatch, fake_classifier):
    payload = _payload()
    monkeypatch.setattr(city_data, "lookup_city_layers", lambda *a, **k: (payload, "k"))
    monkeypatch.setattr(city_data, "with_survey_heights", lambda p, *a: p)
    hook = lambda d, t: None  # noqa: E731
    city_data.get_city_layers(*BOX, ["buildings"], roof_progress=hook)
    assert fake_classifier[0][1]["progress"] is hook


def test_off_returns_the_payload_and_never_calls_the_classifier(monkeypatch):
    def boom(*a, **k):
        pytest.fail("the classifier must not run when roof_shapes is off")

    monkeypatch.setattr(roof_classifier, "classify_roof_shapes", boom)
    monkeypatch.setattr(roof_model, "load", boom)
    payload = _payload()
    for off in ("off", False):
        assert city_data.with_roof_shapes(payload, *BOX, off) is payload


def test_skips_quietly_with_a_reason(monkeypatch):
    city_data._roof_memo.clear()
    monkeypatch.setattr(roof_model, "load", lambda *a, **k: None)
    payload = _payload()
    out = city_data.with_roof_shapes(payload, *BOX, "auto")
    assert out["buildings"] is payload["buildings"]
    assert out["roof_shapes"]["classified"] == 0 and "checkpoint" in out["roof_shapes"]["reason"]

    city_data._roof_memo.clear()

    def boom(*a, **k):
        raise RuntimeError("tiles down")

    monkeypatch.setattr(roof_model, "load", lambda *a, **k: object())
    monkeypatch.setattr(roof_classifier, "classify_roof_shapes", boom)
    out = city_data.with_roof_shapes(payload, *BOX, "auto")
    assert out["buildings"] is payload["buildings"] and "tiles down" in out["roof_shapes"]["reason"]


def test_get_city_layers_classifies_after_the_survey_fill(monkeypatch, fake_classifier):
    order = []
    payload = _payload()
    monkeypatch.setattr(city_data, "lookup_city_layers", lambda *a, **k: (payload, "k"))
    monkeypatch.setattr(city_data, "with_survey_heights",
                        lambda p, *a: order.append("survey") or {**p, "survey_fill": {"filled": 0}})
    real = city_data.with_roof_shapes
    monkeypatch.setattr(city_data, "with_roof_shapes",
                        lambda p, *a, **k: order.append("roofs") or real(p, *a, **k))
    auto = city_data.get_city_layers(*BOX, ["buildings"])
    assert order == ["survey", "roofs"]
    assert auto["roof_shapes"]["classified"] == 1 and auto["survey_fill"] == {"filled": 0}
    off = city_data.get_city_layers(*BOX, ["buildings"], roof_shapes="off")
    assert "roof_shapes" not in off and off["buildings"] is payload["buildings"]


def test_merge_layer_data_keeps_classified_roofs_unless_the_client_sets_one(fake_classifier):
    server = city_data.with_roof_shapes(_payload(), *BOX, "auto")
    client = copy.deepcopy(_payload()["buildings"])        # an edit made before the classifier ran
    client["features"][0]["properties"]["height_m"] = 30.0
    client["features"][1]["properties"]["roof:shape"] = "skillion"
    out = city_data.merge_layer_data(server, {"buildings": client})["buildings"]["features"]
    p0, p1 = (f["properties"] for f in out[:2])
    assert p0["height_m"] == 30.0 and p0["roof:shape"] == "gabled" and p0["roof_source"] == "roof_model_gbm"
    assert p1["roof:shape"] == "skillion" and "roof_source" not in p1
    # an explicit client roof on a classified feature wins
    client["features"][0]["properties"]["roof:shape"] = "dome"
    out = city_data.merge_layer_data(server, {"buildings": client})["buildings"]["features"]
    assert out[0]["properties"]["roof:shape"] == "dome"


def test_model_is_byte_identical_off_and_changes_with_auto(monkeypatch, fake_classifier):
    sc = choose_scale(BBOX, (40, 60), 100.0, 110.0, mm_per_px=2.0)
    z = sc.z_mm(np.full((40, 60), 100.0))
    payload = _payload()
    monkeypatch.setattr(city_data, "lookup_city_layers", lambda *a, **k: (payload, "k"))
    monkeypatch.setattr(city_data, "with_survey_heights", lambda p, *a: p)

    def build(layers):
        return build_on_terrain(z, BBOX, sc, {"buildings": layers["buildings"]}, cache=False).merged

    baseline = build(payload)                                     # today's inputs, no roof code
    off = build(city_data.get_city_layers(*BOX, ["buildings"], roof_shapes="off"))
    assert off.vertices.tobytes() == baseline.vertices.tobytes()
    assert off.faces.tobytes() == baseline.faces.tobytes()
    auto = build(city_data.get_city_layers(*BOX, ["buildings"], roof_shapes="auto"))
    assert auto.vertices.shape != baseline.vertices.shape or not np.array_equal(auto.vertices, baseline.vertices)
